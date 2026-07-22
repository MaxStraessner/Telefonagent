import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  handlers: new Map<string, Array<(...args: unknown[]) => void>>(),
  transportHandlers: new Map<string, Array<(...args: unknown[]) => void>>(),
  connect: vi.fn<() => Promise<void>>(),
  close: vi.fn(),
  transportClose: vi.fn(),
  mute: vi.fn(),
  interrupt: vi.fn(),
  requestResponse: vi.fn(),
  sessionOff: vi.fn(),
  transportOff: vi.fn(),
  emit(type: string, ...args: unknown[]) {
    this.handlers.get(type)?.forEach((handler) => handler(...args));
  },
  emitTransport(type: string, ...args: unknown[]) {
    this.transportHandlers.get(type)?.forEach((handler) => handler(...args));
  },
}));

vi.mock("@openai/agents/realtime", () => {
  const tool = (options: unknown) => options;
  class RealtimeAgent {
    constructor(_options: unknown) {}
  }
  class OpenAIRealtimeWebRTC {
    callId = "call_test_123456";
    status = "connected";
    constructor(_options: unknown) {}
    on(type: string, handler: (...args: unknown[]) => void) {
      sdk.transportHandlers.set(type, [...(sdk.transportHandlers.get(type) ?? []), handler]);
    }
    off(type: string, handler: (...args: unknown[]) => void) {
      sdk.transportHandlers.set(type, (sdk.transportHandlers.get(type) ?? []).filter((candidate) => candidate !== handler));
      sdk.transportOff(type, handler);
    }
    requestResponse = sdk.requestResponse;
    close = sdk.transportClose;
  }
  class RealtimeSession {
    constructor(_agent: unknown, _options: unknown) {}
    on(type: string, handler: (...args: unknown[]) => void) {
      sdk.handlers.set(type, [...(sdk.handlers.get(type) ?? []), handler]);
    }
    off(type: string, handler: (...args: unknown[]) => void) {
      sdk.handlers.set(type, (sdk.handlers.get(type) ?? []).filter((candidate) => candidate !== handler));
      sdk.sessionOff(type, handler);
    }
    connect = sdk.connect;
    close = sdk.close;
    mute = sdk.mute;
    interrupt = sdk.interrupt;
  }
  return { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession, tool };
});

import { App } from "../src/App";
import { BrowserRealtimeClient, CONNECTION_TIMEOUT_MS, type RealtimeClientCallbacks } from "../src/features/realtime/realtimeClient";

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
  speed: 1, configuration_version: 1, capability_keys: [], tool_names: [],
  maximum_session_minutes: 10, transcription_enabled: true, raw_event_logging: false,
  vad: { type: "server_vad" as const, threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 600, eagerness: null, create_response: true, interrupt_response: true },
};
const clientSecret = { client_secret: "ek_test", expires_at: 1_900_000_000, session_id: "sess_test", model: "gpt-realtime-2.1", voice: "marin", speed: 1, configuration_version: 1, call_session_id: "call-local", tenant_id: tenant.id };

function clientCallbacks(): RealtimeClientCallbacks {
  return {
    onState: vi.fn(), onHistory: vi.fn(), onEvent: vi.fn(), onError: vi.fn(), onConnected: vi.fn(),
    onUserSpeechStopped: vi.fn(), onAssistantAudioPlaying: vi.fn(), onResponseCompleted: vi.fn(), onPlaybackStatus: vi.fn(), onCallId: vi.fn(),
  };
}

let trackStop: ReturnType<typeof vi.fn>;
let getUserMedia: ReturnType<typeof vi.fn>;
let microphoneTrack: {
  enabled: boolean;
  stop: ReturnType<typeof vi.fn>;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
  emitEnded: () => void;
};

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
  sdk.transportHandlers.clear();
  sdk.connect.mockReset().mockResolvedValue(undefined);
  sdk.close.mockReset();
  sdk.transportClose.mockReset();
  sdk.mute.mockReset();
  sdk.interrupt.mockReset();
  sdk.requestResponse.mockReset();
  sdk.sessionOff.mockReset();
  sdk.transportOff.mockReset();
  trackStop = vi.fn();
  let endedHandler: (() => void) | undefined;
  microphoneTrack = {
    enabled: true,
    stop: trackStop,
    addEventListener: vi.fn((type: string, handler: () => void) => { if (type === "ended") endedHandler = handler; }),
    removeEventListener: vi.fn((type: string, handler: () => void) => { if (type === "ended" && endedHandler === handler) endedHandler = undefined; }),
    emitEnded: () => endedHandler?.(),
  };
  getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [microphoneTrack], getAudioTracks: () => [microphoneTrack] });
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  vi.stubGlobal("RTCPeerConnection", class {});
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: vi.fn() });
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: vi.fn().mockResolvedValue(undefined) });
  mockBackend();
  localStorage.clear();
  window.history.pushState({}, "", "/testgespraech");
});

afterEach(() => {
  vi.useRealTimers();
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
    expect(sdk.sessionOff).toHaveBeenCalledTimes(4);
    expect(sdk.transportOff).toHaveBeenCalledOnce();
    expect(microphoneTrack.removeEventListener).toHaveBeenCalledWith("ended", expect.any(Function));
    expect(screen.getByLabelText("Sprachausgabe des Assistenten")).toHaveProperty("srcObject", null);
    expect(screen.getByText("Testgespräch beendet.")).toBeInTheDocument();
  });

  it("zeigt Mikrofonanfrage und Verbindungsaufbau als getrennte Zustände", async () => {
    let allowMicrophone: ((stream: MediaStream) => void) | undefined;
    let finishConnection: (() => void) | undefined;
    getUserMedia.mockReturnValue(new Promise((resolve) => { allowMicrophone = resolve; }));
    sdk.connect.mockReturnValue(new Promise<void>((resolve) => { finishConnection = resolve; }));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(screen.getAllByText("Mikrofonzugriff wird angefordert").length).toBeGreaterThan(0);
    act(() => allowMicrophone?.({ getTracks: () => [microphoneTrack], getAudioTracks: () => [microphoneTrack] } as unknown as MediaStream));
    expect(await screen.findByText("Sprachverbindung wird aufgebaut …")).toBeInTheDocument();
    expect(screen.getAllByText("Verbindung wird aufgebaut").length).toBeGreaterThan(0);
    act(() => finishConnection?.());
    expect(await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.")).toBeInTheDocument();
  });

  it("erklärt eine verweigerte Mikrofonfreigabe verständlich", async () => {
    getUserMedia.mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Mikrofonzugriff wurde verweigert");
    expect(sdk.connect).not.toHaveBeenCalled();
  });

  it.each([
    ["NotFoundError", "kein verfügbares Mikrofon"],
    ["NotReadableError", "blockiert oder wird von einer anderen Anwendung verwendet"],
  ])("erklärt den Mikrofonfehler %s", async (name, expected) => {
    getUserMedia.mockRejectedValue(new DOMException("media error", name));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
    expect(sdk.connect).not.toHaveBeenCalled();
  });

  it("meldet einen Browser ohne WebRTC-Unterstützung vor der Mikrofonanfrage", async () => {
    vi.stubGlobal("RTCPeerConnection", undefined);
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Browser unterstützt die benötigten WebRTC");
    expect(getUserMedia).not.toHaveBeenCalled();
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

  it("hält das Mikrofon bis zum tatsächlichen Ende des Audiopuffers gesperrt", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    act(() => sdk.emit("transport_event", { type: "output_audio_buffer.started", response: { id: "r1" } }));
    expect(microphoneTrack.enabled).toBe(false);
    expect(screen.getByText("Wiedergabe läuft")).toBeInTheDocument();
    act(() => sdk.emit("transport_event", { type: "response.output_audio.done", response_id: "r1" }));
    expect(microphoneTrack.enabled).toBe(false);
    expect(screen.getByText("Wiedergabe läuft")).toBeInTheDocument();
    act(() => sdk.emit("transport_event", { type: "response.done", response: { id: "r1", status: "completed" } }));
    expect(microphoneTrack.enabled).toBe(false);
    expect(screen.queryByText("Unterbrochen")).not.toBeInTheDocument();
    expect(sdk.close).not.toHaveBeenCalled();
    act(() => sdk.emit("transport_event", { type: "output_audio_buffer.stopped", response: { id: "r1" } }));
    expect(microphoneTrack.enabled).toBe(true);
    expect(screen.getByText("Vollständig wiedergegeben")).toBeInTheDocument();
  });

  it("markiert nur eine nachgewiesene Pufferlöschung und Stornierung als unterbrochen", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    act(() => {
      sdk.emit("transport_event", { type: "output_audio_buffer.started", response_id: "r-cancel" });
      sdk.emit("transport_event", { type: "output_audio_buffer.cleared", response_id: "r-cancel" });
      sdk.emit("transport_event", { type: "response.done", response: { id: "r-cancel", status: "cancelled" } });
    });
    expect(microphoneTrack.enabled).toBe(true);
    expect(screen.getByText("Unterbrochen")).toBeInTheDocument();
  });

  it("behandelt completed plus echtes Buffer-Stopped stets als normalen Abschluss", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    act(() => {
      sdk.emit("transport_event", { type: "response.done", response: { id: "r-complete", status: "completed" } });
      sdk.emit("transport_event", { type: "output_audio_buffer.stopped", response_id: "r-complete" });
    });
    expect(screen.getByText("Vollständig wiedergegeben")).toBeInTheDocument();
    expect(screen.queryByText("Unterbrochen")).not.toBeInTheDocument();
  });

  it("beendet die Sitzung kontrolliert, wenn der Mikrofontrack endet", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    act(() => microphoneTrack.emitEnded());
    expect(await screen.findByRole("alert")).toHaveTextContent("Mikrofonzugriff wurde während des Gesprächs beendet");
    expect(trackStop).toHaveBeenCalled();
  });

  it("meldet blockierte Audiowiedergabe verständlich und räumt auf", async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new DOMException("blocked", "NotAllowedError"));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    fireEvent.loadedMetadata(screen.getByLabelText("Sprachausgabe des Assistenten"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Audioausgabe wurde vom Browser blockiert");
    expect(trackStop).toHaveBeenCalled();
  });

  it("behandelt einen unerwarteten Transportabbruch als Sitzungsfehler", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    act(() => sdk.emitTransport("connection_change", "disconnected"));
    expect(await screen.findByRole("alert")).toHaveTextContent("WebRTC-Sprachverbindung wurde unterbrochen");
    expect(trackStop).toHaveBeenCalled();
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

  it("bleibt beendet, wenn ein noch laufender Verbindungsaufbau später auflöst", async () => {
    let finishConnection: (() => void) | undefined;
    sdk.connect.mockReturnValue(new Promise<void>((resolve) => { finishConnection = resolve; }));
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Sprachverbindung wird aufgebaut …");
    await userEvent.click(screen.getByRole("button", { name: "Gespräch beenden" }));
    expect(screen.getByText("Testgespräch beendet.")).toBeInTheDocument();
    act(() => finishConnection?.());
    await waitFor(() => expect(screen.getAllByText("Gespräch beendet").length).toBeGreaterThan(0));
    expect(screen.queryByText("Die Verbindung steht. Du kannst jetzt sprechen.")).not.toBeInTheDocument();
    expect(trackStop).toHaveBeenCalled();
  });

  it("zeigt unveränderte Raw-Ereignisse, zählt Gesprächsrunden und begrenzt den Puffer", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    act(() => {
      sdk.emit("transport_event", { type: "input_audio_buffer.speech_started" });
      sdk.emit("transport_event", { type: "input_audio_buffer.speech_stopped" });
      sdk.emit("transport_event", { type: "response.created" });
      sdk.emit("transport_event", { type: "response.done", response: { status: "completed" } });
    });
    fireEvent.playing(screen.getByLabelText("Sprachausgabe des Assistenten"));
    expect(await screen.findByText("input_audio_buffer.speech_started")).toBeInTheDocument();
    expect(screen.getByText("input_audio_buffer.speech_stopped")).toBeInTheDocument();
    expect(screen.getByText("response.created")).toBeInTheDocument();
    expect(screen.getByText("response.done")).toBeInTheDocument();
    expect(screen.getByText("html_audio_playing")).toBeInTheDocument();
    expect(screen.getByText("Gesprächsrunden").parentElement).toHaveTextContent("1");
    act(() => { for (let index = 0; index < 45; index += 1) sdk.emit("transport_event", { type: `test.event.${index}` }); });
    expect(document.querySelectorAll(".event-row")).toHaveLength(40);
  });

  it("ignoriert Nutzer-Sprachaktivität im stummgeschalteten Zustand", async () => {
    render(<App />);
    await screen.findByText("Gespräch mit Lina");
    await userEvent.click(screen.getByRole("button", { name: /Testgespräch starten/ }));
    await screen.findByText("Die Verbindung steht. Du kannst jetzt sprechen.");
    await userEvent.click(screen.getByRole("button", { name: "Mikrofon stummschalten" }));
    act(() => sdk.emit("transport_event", { type: "input_audio_buffer.speech_started" }));
    expect(screen.getAllByText("Mikrofon ist stumm").length).toBeGreaterThan(0);
  });

  it("erzeugt aus response.output_audio.done kein künstliches Buffer-Stopped-Ereignis", async () => {
    const callbacks = clientCallbacks();
    const client = new BrowserRealtimeClient(callbacks);
    await client.connect(agentConfig, clientSecret, { getTracks: () => [microphoneTrack], getAudioTracks: () => [microphoneTrack] } as unknown as MediaStream, document.createElement("audio"));
    vi.mocked(callbacks.onEvent).mockClear();
    act(() => {
      sdk.emit("transport_event", { type: "output_audio_buffer.started", response_id: "r-no-fake" });
      sdk.emit("transport_event", { type: "response.output_audio.done", response_id: "r-no-fake" });
    });
    const serializedEvents = vi.mocked(callbacks.onEvent).mock.calls.map((call) => JSON.stringify(call)).join("\n");
    expect(serializedEvents).toContain("response.output_audio.done");
    expect(serializedEvents).not.toContain("output_audio_buffer.stopped");
    expect(microphoneTrack.enabled).toBe(false);
    client.close();
  });

  it("ignoriert SDK-audio_stopped und verarbeitet ein echtes Buffer-Stopped genau einmal", async () => {
    const callbacks = clientCallbacks();
    const client = new BrowserRealtimeClient(callbacks);
    await client.connect(agentConfig, clientSecret, { getTracks: () => [microphoneTrack], getAudioTracks: () => [microphoneTrack] } as unknown as MediaStream, document.createElement("audio"));
    act(() => {
      sdk.emit("transport_event", { type: "output_audio_buffer.started", response_id: "r-once" });
      sdk.emit("transport_event", { type: "response.done", response: { id: "r-once", status: "completed" } });
    });
    vi.mocked(callbacks.onState).mockClear();
    vi.mocked(callbacks.onPlaybackStatus).mockClear();
    act(() => sdk.emit("audio_stopped"));
    expect(microphoneTrack.enabled).toBe(false);
    expect(callbacks.onState).not.toHaveBeenCalled();
    expect(sdk.handlers.has("audio_stopped")).toBe(false);
    act(() => sdk.emit("transport_event", { type: "output_audio_buffer.stopped", response_id: "r-once" }));
    expect(microphoneTrack.enabled).toBe(true);
    expect(callbacks.onState).toHaveBeenCalledTimes(1);
    expect(callbacks.onState).toHaveBeenCalledWith("connected");
    expect(callbacks.onPlaybackStatus).toHaveBeenCalledOnce();
    expect(callbacks.onPlaybackStatus).toHaveBeenCalledWith("completed", "r-once");
    client.close();
  });

  it("schließt zehn aufeinanderfolgende Antworten erst nach dem jeweiligen echten Buffer-Stopp ab", async () => {
    const callbacks = clientCallbacks();
    const client = new BrowserRealtimeClient(callbacks);
    await client.connect(agentConfig, clientSecret, { getTracks: () => [microphoneTrack], getAudioTracks: () => [microphoneTrack] } as unknown as MediaStream, document.createElement("audio"));
    vi.mocked(callbacks.onPlaybackStatus).mockClear();
    for (let index = 1; index <= 10; index += 1) {
      const responseId = `r-sequence-${index}`;
      act(() => {
        sdk.emit("transport_event", { type: "response.created", response: { id: responseId } });
        sdk.emit("transport_event", { type: "output_audio_buffer.started", response_id: responseId });
        sdk.emit("transport_event", { type: "response.output_audio.done", response_id: responseId });
        sdk.emit("transport_event", { type: "response.done", response: { id: responseId, status: "completed" } });
      });
      expect(microphoneTrack.enabled).toBe(false);
      act(() => sdk.emit("transport_event", { type: "output_audio_buffer.stopped", response_id: responseId }));
      expect(microphoneTrack.enabled).toBe(true);
    }
    const completedCalls = vi.mocked(callbacks.onPlaybackStatus).mock.calls.filter(([status]) => status === "completed");
    expect(completedCalls).toHaveLength(10);
    expect(completedCalls.map(([, responseId]) => responseId)).toEqual(Array.from({ length: 10 }, (_, index) => `r-sequence-${index + 1}`));
    expect(vi.mocked(callbacks.onPlaybackStatus).mock.calls.some(([status]) => status === "interrupted")).toBe(false);
    client.close();
  });

  it("bricht einen hängenden SDK-Verbindungsaufbau nach 15 Sekunden ab", async () => {
    vi.useFakeTimers();
    sdk.connect.mockReturnValue(new Promise<void>(() => undefined));
    const callbacks = clientCallbacks();
    const audio = document.createElement("audio");
    const client = new BrowserRealtimeClient(callbacks);
    const connecting = client.connect(agentConfig, clientSecret, { getTracks: () => [microphoneTrack], getAudioTracks: () => [microphoneTrack] } as unknown as MediaStream, audio);
    const rejection = expect(connecting).rejects.toMatchObject({ code: "realtime_connection_timeout" });
    await vi.advanceTimersByTimeAsync(CONNECTION_TIMEOUT_MS);
    await rejection;
    client.close();
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
