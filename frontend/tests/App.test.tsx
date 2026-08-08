import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";

const tenant = {
  id: "11111111-1111-1111-1111-111111111111", slug: "salon-haarkunst-test", name: "Salon Haarkunst Test",
  industry: "hair_salon", timezone: "Europe/Berlin", status: "active",
  settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Guten Tag", presentation_mode_enabled: false, diagnostics_enabled: true },
  primary_location: { id: "22222222-2222-2222-2222-222222222222", name: "Hauptstandort", street: "", postal_code: "", city: "", country_code: "DE", timezone: "Europe/Berlin", is_primary: true },
};
const services = [{ id: "1", name: "Herrenhaarschnitt", description: "Klassisch", duration_minutes: 30, is_active: true }];
const staff = [{ id: "1", display_name: "Anna", role_name: "Stylist:in", is_active: true }];
const status = { environment: "development", backend_version: "0.1.0", realtime_voice_configured: false, telephony_configured: false, calendar_configured: false, database_connected: true, realtime_model: "gpt-realtime-2.1", realtime_voice: "marin" };
const authSession = {
  user: { id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", username: "owner", email: "owner@example.test", display_name: "Lokale Administration", role: "owner", is_platform_admin: false },
  tenant: { id: tenant.id, slug: tenant.slug, name: tenant.name },
  idle_expires_at: "2026-07-24T12:30:00Z", absolute_expires_at: "2026-07-24T23:00:00Z",
};

function mockSuccess() {
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const url = String(input);
    const body = url.endsWith("/auth/session") ? authSession : url.endsWith("/tenant") ? tenant : url.endsWith("/services") ? services : url.endsWith("/staff") ? staff : url.endsWith("/appointments") ? [] : url.endsWith("/platform/status") ? status : { status: "healthy", database: "connected" };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
}

beforeEach(() => { mockSuccess(); });
afterEach(() => vi.unstubAllGlobals());

describe("Telefonagent App", () => {
  it("zeigt Ladezustand und rendert danach die Übersicht mit API-Mandant", async () => {
    render(<App />);
    expect(screen.getByText("Sitzung wird geprüft …")).toBeInTheDocument();
    expect(await screen.findByText("Guten Tag bei Salon Haarkunst Test")).toBeInTheDocument();
    expect(screen.getByText("Aus PostgreSQL geladen")).toBeInTheDocument();
  });

  it("zeigt einen verständlichen Fehlerzustand mit Wiederholung", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => Promise.resolve(
      String(input).endsWith("/auth/session")
        ? new Response(JSON.stringify(authSession), { status: 200, headers: { "Content-Type": "application/json" } })
        : new Response("{}", { status: 503 }),
    )));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Verbindung nicht verfügbar");
    expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });

  it("kennzeichnet eine fehlende serverseitige Sprachkonfiguration", async () => {
    window.history.pushState({}, "", "/testgespraech");
    render(<App />);
    expect(await screen.findByText("Gespräch mit Lina")).toBeInTheDocument();
    expect(screen.getAllByText("Nicht eingerichtet").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Testgespräch starten/ })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("OpenAI Realtime ist serverseitig noch nicht konfiguriert");
  });

  it("wechselt zwischen Test- und Präsentationsmodus und speichert die Auswahl", async () => {
    window.history.pushState({}, "", "/testgespraech");
    render(<App />);
    await screen.findByText("Technische Diagnose");
    await userEvent.click(screen.getByRole("button", { name: "Präsentation" }));
    await waitFor(() => expect(screen.queryByText("Technische Diagnose")).not.toBeInTheDocument());
    expect(localStorage.getItem("telefonagent-display-mode")).toBe("presentation");
  });

  it("macht die mobile Navigation über eine beschriftete Schaltfläche erreichbar", async () => {
    render(<App />);
    await screen.findByText("Guten Tag bei Salon Haarkunst Test");
    const button = screen.getByRole("button", { name: "Navigation öffnen" });
    await userEvent.click(button);
    expect(screen.getByRole("navigation", { name: "Hauptnavigation" })).toBeInTheDocument();
  });
});
