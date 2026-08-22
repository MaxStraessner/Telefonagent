import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";

const tenant = {
  id: "11111111-1111-1111-1111-111111111111", slug: "salon", name: "Salon Beispiel",
  industry: "hair_salon", timezone: "Europe/Berlin", status: "active",
  settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Hallo", presentation_mode_enabled: false, diagnostics_enabled: true },
  primary_location: null,
};
const session = {
  user: { id: "u1", username: "owner", email: null, display_name: "Owner", role: "company_admin", platform_role: null, is_platform_admin: false, must_change_password: false },
  tenant: { id: tenant.id, slug: tenant.slug, name: tenant.name }, active_company: null,
  membership: { tenant_id: tenant.id, role: "company_admin", is_primary_admin: true },
  permissions: ["company.read", "company.features.use", "company.manage"], mode: "company",
  idle_expires_at: "2026-08-22T12:30:00Z", absolute_expires_at: "2026-08-22T23:00:00Z",
};
const call = {
  id: "call-1", started_at: "2026-08-22T08:30:00Z", ended_at: "2026-08-22T08:33:05Z",
  caller_name: "Max Mustermann", caller_phone: "+491701234567", category: "appointment",
  result: "Termin für Dienstag vereinbart.", summary: "Der Anrufer möchte einen Herrenhaarschnitt.",
  action_required: false, action_items: [], priority: "normal", status: "open", duration_seconds: 185,
  analysis_status: "completed", external_call_id: "CA123", phone_number_id: "phone-1",
  transcript: [
    { speaker: "agent", text: "Guten Tag, wie kann ich helfen?" },
    { speaker: "customer", text: "Ich hätte gerne einen Termin." },
  ],
};

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function baseBody(url: string): unknown {
  if (url.endsWith("/auth/session")) return session;
  if (url.endsWith("/tenant")) return tenant;
  if (url.endsWith("/platform/status")) return { environment: "test", backend_version: "1", realtime_voice_configured: true, telephony_configured: true, calendar_configured: false, database_connected: true, realtime_model: "model", realtime_voice: "voice" };
  if (url.endsWith("/health")) return { status: "healthy", database: "connected" };
  return [];
}

beforeEach(() => { window.history.replaceState({}, "", "/"); });
afterEach(() => vi.unstubAllGlobals());

describe("Call History", () => {
  it("lädt die Anrufliste mit den fachlichen Ergebnissen", async () => {
    window.history.replaceState({}, "", "/anrufe");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls") ? [call] : baseBody(url));
    }));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Anrufe" })).toBeInTheDocument();
    expect((await screen.findAllByText("Max Mustermann")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Termin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Termin für Dienstag vereinbart.").length).toBeGreaterThan(0);
  });

  it("zeigt das Transkript und ändert den Bearbeitungsstatus", async () => {
    window.history.replaceState({}, "", "/anrufe/call-1");
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/calls/call-1") && init?.method === "PATCH") return response({ ...call, status: "done" });
      if (url.endsWith("/calls/call-1")) return response(call);
      return response(baseBody(url));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Max Mustermann" })).toBeInTheDocument();
    expect(screen.getByText("Guten Tag, wie kann ich helfen?")).toBeInTheDocument();
    expect(screen.getByText("Ich hätte gerne einen Termin.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Als erledigt markieren" }));
    expect(await screen.findByRole("button", { name: "Wieder öffnen" })).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
  });

  it("zeigt einen verständlichen Ladefehler", async () => {
    window.history.replaceState({}, "", "/anrufe");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return url.endsWith("/calls")
        ? response({ error: { code: "calls_unavailable", message: "Anrufe sind vorübergehend nicht verfügbar." } }, 503)
        : response(baseBody(url));
    }));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Anrufe sind vorübergehend nicht verfügbar.");
    expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });
});
