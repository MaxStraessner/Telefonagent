import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  handlers: new Map<string, Array<(...args: unknown[]) => void>>(),
  connect: vi.fn<() => Promise<void>>(),
  close: vi.fn(),
  transportClose: vi.fn(),
  mute: vi.fn(),
  interrupt: vi.fn(),
  requestResponse: vi.fn(),
  emit(type: string, ...args: unknown[]) {
    this.handlers.get(type)?.forEach((handler) => handler(...args));
  },
}));

vi.mock("@openai/agents/realtime", () => {
  class RealtimeAgent {
    constructor(_options: unknown) {}
  }
  class OpenAIRealtimeWebRTC {
    callId = "call_test_123456";
    constructor(_options: unknown) {}
    requestResponse = sdk.requestResponse;
    close = sdk.transportClose;
  }
  class RealtimeSession {
    constructor(_agent: unknown, _options: unknown) {}
    on(type: string, handler: (...args: unknown[]) => void) {
      sdk.handlers.set(type, [...(sdk.handlers.get(type) ?? []), handler]);
    }
    connect = sdk.connect;
    close = sdk.close;
    mute = sdk.mute;
    interrupt = sdk.interrupt;
  }
  return { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession };
});

import { App } from "../src/App";

const tenant = {
  id: "11111111-1111-1111-1111-111111111111", slug: "salon-haarkunst-test", name: "Salon Haarkunst Test",
  industry: "hair_salon", timezone: "Europe/Berlin", status: "active",
  settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Guten Tag, hier ist Lina.", presentation_mode_enabled: false, diagnostics_enabled: true },
  primary_location: null,
};
const platformStatus = { environment: "development", backend_version: "0.1.0", realtime_voice_configured: true, telephony_configured: false, calendar_configured: false, database_connected: true, realtime_model: "gpt-realtime-2.1", realtime_voice: "marin" };
const agentConfig = {
  tenant_id: tenant.id, tenant_name: tenant.name, assistant_name: "Lina", language: "de",
  welcome_message: "Guten Tag, hier ist Lina.", instructions: "Sei freundlich. Keine Werkzeuge.", model: "gpt-realtime-2.1", voice: "marin",
  maximum_session_minutes: 10, transcription_enabled: true, raw_event_logging: false,
  vad: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 600, create_response: true, interrupt_response: true },
};
const clientSecret = { client_secret: "ek_test", expires_at: 1_900_000_000, session_id: "sess_test", model: "gpt-realtime-2.1", voice: "marin", tenant_id: tenant.id, tenant_name: tenant.name, assistant_name: "Lina" };

let trackStop: ReturnType<typeof vi.fn>;
let getUserMedia: ReturnType<typeof vi.fn>;

function mockBackend() {
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    let body: unknown = { status: "healthy", database: "connected" };
    if (url.endsWith("/tenant")) body = tenant;
    else if (url.endsWith("/services") || url.endsWith("/staff") || url.endsWith("/appointments")) body = [];
    else if (url.endsWith("/platform/status")) body = platformStatus;
    else if (url.endsWith("/realtime/agent-config")) body = agentConfig;
    else if (url.endsWith("/realtime/client-secret")) {
      expect(init?.method).toBe("POST");
      body = clientSecret;
    }
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
}

beforeEach(() => {
  sdk.handlers.clear();
  sdk.connect.mockReset().mockResolvedValue(undefined);
  sdk.close.mockReset();
  sdk.transportClose.mockReset();
  sdk.mute.mockReset();
  sdk.interrupt.mockReset();
  sdk.requestResponse.mockReset();
  trackStop = vi.fn();
  getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: trackStop }] });
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: vi.fn() });
  mockBackend();
  localStorage.clear();
  window.history.pushState({}, "", "/testgespraech");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Realtime browser voice flow", () => {
  it("baut die Sitzung erst nach Nutzerklick auf, begrüßt und räumt beim Beenden vollständig auf", async () => {
    render(<App />);
    expect(await screen.findByText("Gespräch mit Lina")).toBeInTheDocument();
    expect(getUserMedia).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.")).toBeInTheDocument();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    expect(sdk.connect).toHaveBeenCalledWith({ apiKey: "ek_test", model: "gpt-realtime-2.1" });
    expect(sdk.requestResponse).toHaveBeenCalledWith(expect.objectContaining({ instructions: expect.stringContaining("Guten Tag, hier ist Lina") }));

    act(() => sdk.emit("history_updated", [{ itemId: "u1", type: "message", role: "user", status: "completed", content: [{ type: "input_audio", transcript: "Ich brauche einen Termin" }] }]));
    expect(await screen.findByText("Ich brauche einen Termin")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Transkript leeren" }));
    expect(screen.queryByText("Ich brauche einen Termin")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Mikrofon stummschalten" }));
    expect(sdk.mute).toHaveBeenCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: "Gespräch beenden" }));
    expect(sdk.close).toHaveBeenCalled();
    expect(sdk.transportClose).toHaveBeenCalled();
    expect(trackStop).toHaveBeenCalled();
    expect(screen.getByText("Testgespräch beendet.")).toBeInTheDocument();
  });

  it("erklärt eine verweigerte Mikrofonfreigabe verständlich", async () => {
    getUserMedia.mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Mikrofonzugriff wurde verweigert");
    expect(sdk.connect).not.toHaveBeenCalled();
  });

  it("übersetzt abgelaufene Client-Secrets in einen erneuten Start", async () => {
    sdk.connect.mockRejectedValue(new Error("ephemeral client secret expired"));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Token ist abgelaufen");
    expect(trackStop).toHaveBeenCalled();
  });

  it("meldet WebRTC-Verbindungsfehler ohne technische Rohdetails", async () => {
    sdk.connect.mockRejectedValue(new Error("WebRTC peer connection ICE failed at secret endpoint"));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("WebRTC-Sprachverbindung konnte nicht aufgebaut werden");
    expect(alert).not.toHaveTextContent("secret endpoint");
  });

  it("unterbricht eine laufende Assistentenantwort kontrolliert", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    await userEvent.click(screen.getByRole("button", { name: "Antwort unterbrechen" }));
    expect(sdk.interrupt).toHaveBeenCalledOnce();
  });

  it("verhindert einen doppelten Start während des Verbindungsaufbaus", async () => {
    let finishConnection: (() => void) | undefined;
    sdk.connect.mockReturnValue(new Promise<void>((resolve) => { finishConnection = resolve; }));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    const startButton = screen.getByRole("button", { name: /Testgespräch starten/ });
    await userEvent.click(startButton);
    expect(startButton).toBeDisabled();
    await userEvent.click(startButton);
    expect(sdk.connect).toHaveBeenCalledOnce();
    act(() => finishConnection?.());
    expect(await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.")).toBeInTheDocument();
  });

  it("zeigt einen strukturierten Backendfehler ohne neuen Verbindungsversuch", async () => {
    const originalFetch = fetch as ReturnType<typeof vi.fn>;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith("/realtime/client-secret")) {
        return Promise.resolve(new Response(JSON.stringify({ error: { code: "realtime_provider_rejected", message: "Der Anbieter hat die Realtime-Konfiguration abgelehnt." } }), { status: 502, headers: { "Content-Type": "application/json" } }));
      }
      return originalFetch(input, init);
    }));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Realtime-Konfiguration abgelehnt");
    expect(sdk.connect).not.toHaveBeenCalled();
    expect(trackStop).toHaveBeenCalled();
  });

  it("schließt eine aktive Sitzung beim Verlassen der Seite", async () => {
    const { unmount } = render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    unmount();
    await waitFor(() => expect(trackStop).toHaveBeenCalled());
  });
});
