import { act, render, screen, waitFor } from "@testing-library/react";
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
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("Call History", () => {
  it("zeigt Zusammenfassungsvorschau und Zustellstatus in der Übersicht", async () => {
    window.history.replaceState({}, "", "/anrufe");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls") ? [{ ...call, summary_preview: "Bitte nächste Woche zurückrufen.", notification_status: "sent" }] : baseBody(url));
    }));
    render(<App />);
    expect((await screen.findAllByText("Bitte nächste Woche zurückrufen.")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("E-Mail vom Mailserver angenommen").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: "Zusammenfassung" })).toBeInTheDocument();
  });
  it("stellt bestätigte Gesprächsbeiträge als zugängliche Tabelle dar", async () => {
    window.history.replaceState({}, "", "/anrufe/call-1");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls/call-1") ? { ...call, notification_status: "uncertain" } : baseBody(url));
    }));
    render(<App />);
    expect(await screen.findByRole("table", { name: "Gesprächsbeiträge in zeitlicher Reihenfolge" })).toBeInTheDocument();
    expect(screen.getAllByText("Bestätigt")).toHaveLength(2);
    expect(screen.getByText(/E-Mail-Zustellung unklar – keine automatische Wiederholung/, { selector: "dd" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "E-Mail erneut zustellen" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Erneuten Versand vorbereiten" }));
    expect(screen.getByRole("button", { name: "E-Mail erneut zustellen" })).toBeInTheDocument();
  });
  it("zeigt Anrufe ohne Nachricht ohne erfundene Auswertung", async () => {
    window.history.replaceState({}, "", "/anrufe/call-1");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls/call-1") ? { ...call, no_message: true, transcript: [], summary: null, analysis_status: "failed", analysis_error_code: "transcript_empty" } : baseBody(url));
    }));
    render(<App />);
    expect(await screen.findByText(/Keine Nachricht hinterlassen/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Erneut analysieren" })).not.toBeInTheDocument();
  });
  it("zeigt gelöschte Inhalte nicht als ausstehende Analyse", async () => {
    window.history.replaceState({}, "", "/anrufe");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls") ? [{ ...call, documentation_status: "deleted", analysis_status: "not_requested", result: null }] : baseBody(url));
    }));
    render(<App />);
    expect((await screen.findAllByText("Gesprächsinhalte gelöscht")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Analyse ausstehend")).not.toBeInTheDocument();
  });
  it("aktualisiert eine asynchron fertiggestellte Auswertung automatisch", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/anrufe/call-1");
    let finished = false;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls/call-1") ? { ...call, analysis_status: finished ? "completed" : "pending", summary: finished ? "Automatisch ausgewertet" : null } : baseBody(url));
    }));
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("Die Auswertung wird vorbereitet.")).toBeInTheDocument();
    finished = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("Automatisch ausgewertet")).toBeInTheDocument();
  });
  it("trennt Terminwunsch, vorläufige Transkription und tatsächliche Buchungen", async () => {
    window.history.replaceState({}, "", "/anrufe/call-1");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      return response(url.endsWith("/calls/call-1") ? { ...call,
        operating_mode: "answering_machine", voice_model: "gpt-realtime-2.1", callback_required: true,
        analysis_details: { main_concern: "Bitte zurückrufen", appointment_request: "Freitagvormittag", contact_phone: null, contact_email: null },
        transcript: [{ speaker: "customer", text: "Unvollständiger Satz", state: "provisional", start_ms: 1250 }],
        actions: [], appointments: [],
      } : baseBody(url));
    }));
    render(<App />);
    expect(await screen.findByText("Anrufbeantworter")).toBeInTheDocument();
    expect(screen.getByText("Freitagvormittag")).toBeInTheDocument();
    expect(screen.getByText("Keine Terminbuchung verknüpft.")).toBeInTheDocument();
    expect(screen.getByText(/Vorläufig ·/)).toBeInTheDocument();
    expect(screen.getByText("Gewünscht")).toBeInTheDocument();
  });
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
