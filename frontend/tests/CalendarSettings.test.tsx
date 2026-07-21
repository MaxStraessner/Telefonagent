import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CalendarSettingsPage } from "../src/pages/CalendarSettingsPage";

const providers = [
  { provider: "google", label: "Google Kalender", configured: false, missing_configuration: ["GOOGLE_CALENDAR_CLIENT_ID"] },
  { provider: "microsoft", label: "Microsoft Outlook", configured: true, missing_configuration: [] },
];
const calendars = [
  { id: "calendar-1", connection_id: "connection-1", external_calendar_id: "external-1", calendar_name: "Hauptkalender", calendar_timezone: "Europe/Berlin", owner_name: "Owner", access_role: "owner", is_primary: true, can_write: true, is_selected_for_availability: true, is_selected_for_booking: true, last_seen_at: "2026-07-21T10:00:00Z" },
  { id: "calendar-2", connection_id: "connection-1", external_calendar_id: "external-2", calendar_name: "Teamkalender", calendar_timezone: "Europe/Berlin", owner_name: "Owner", access_role: "writer", is_primary: false, can_write: true, is_selected_for_availability: false, is_selected_for_booking: false, last_seen_at: "2026-07-21T10:00:00Z" },
  { id: "calendar-3", connection_id: "connection-1", external_calendar_id: "external-3", calendar_name: "Nur Lesen", calendar_timezone: "Europe/Berlin", owner_name: "Owner", access_role: "reader", is_primary: false, can_write: false, is_selected_for_availability: false, is_selected_for_booking: false, last_seen_at: "2026-07-21T10:00:00Z" },
];
const overview = {
  providers,
  connections: [{ id: "connection-1", provider: "microsoft", account_email: "owner@example.test", display_name: "Owner", connection_status: "connected", last_successful_request_at: "2026-07-21T10:00:00Z", last_error_code: null, created_at: "2026-07-21T09:00:00Z", calendars }],
};
const configuration = {
  id: "config-1", tenant_id: "tenant-1", timezone: "Europe/Berlin", slot_interval_minutes: 15,
  minimum_notice_minutes: 120, maximum_booking_horizon_days: 60, buffer_before_minutes: 0,
  buffer_after_minutes: 0, maximum_suggestions_per_request: 3,
  business_hours: [{ weekday: 0, start_time: "09:00:00", end_time: "12:00:00", is_active: true }],
  updated_at: "2026-07-21T10:00:00Z",
};

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }

function installFetch(options: { overview?: typeof overview; appointmentTypes?: unknown[]; failConnections?: boolean } = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const mock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init });
    if (url.endsWith("/calendar/connections")) return Promise.resolve(options.failConnections ? json({ error: { message: "Kalenderdienst nicht erreichbar" } }, 503) : json(options.overview ?? overview));
    if (url.endsWith("/calendar/configuration") && (init?.method ?? "GET") === "GET") return Promise.resolve(json(configuration));
    if (url.endsWith("/calendar/configuration") && init?.method === "PUT") return Promise.resolve(json(configuration));
    if (url.endsWith("/calendar/configuration/calendars")) return Promise.resolve(json(calendars));
    if (url.endsWith("/calendar/appointment-types") && (init?.method ?? "GET") === "GET") return Promise.resolve(json(options.appointmentTypes ?? []));
    if (url.endsWith("/calendar/appointment-types") && init?.method === "POST") return Promise.resolve(json({ id: "type-1", tenant_id: "tenant-1", ...JSON.parse(String(init.body)), created_at: "now", updated_at: "now" }, 201));
    if (url.includes("/calendar/connections/") && url.endsWith("/calendars")) return Promise.resolve(json(calendars));
    return Promise.resolve(json({}));
  });
  vi.stubGlobal("fetch", mock);
  return calls;
}

beforeEach(() => { window.history.replaceState({}, "", "/kalender"); });
afterEach(() => vi.unstubAllGlobals());

describe("Kalenderverwaltung", () => {
  it("zeigt echten Ladezustand und nicht konfigurierte Provider ohne simulierte Verbindung", async () => {
    installFetch();
    render(<CalendarSettingsPage />);
    expect(screen.getByLabelText("Kalenderdaten werden geladen")).toBeInTheDocument();
    expect(await screen.findByText("Google Kalender")).toBeInTheDocument();
    expect(screen.getByText("Anbieter noch nicht konfiguriert")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Verbinden" })[0]).toBeDisabled();
    expect(screen.getByText("owner@example.test")).toBeInTheDocument();
  });

  it("stellt mehrere Verfügbarkeitskalender und genau einen Zielkalender dar", async () => {
    installFetch(); render(<CalendarSettingsPage />);
    await screen.findByText("Google Kalender");
    await userEvent.click(screen.getByRole("tab", { name: "Kalenderauswahl" }));
    expect(screen.getByText("Hauptkalender")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox", { name: "Verfügbarkeit prüfen" })).toHaveLength(3);
    expect(screen.getAllByRole("radio", { name: "Zielkalender" }).filter((item) => (item as HTMLInputElement).checked)).toHaveLength(1);
    expect(screen.getAllByRole("radio", { name: "Zielkalender" })[2]).toBeDisabled();
  });

  it("speichert beim Wechsel weiterhin exakt einen beschreibbaren Zielkalender", async () => {
    const calls = installFetch(); render(<CalendarSettingsPage />);
    await screen.findByText("Google Kalender");
    await userEvent.click(screen.getByRole("tab", { name: "Kalenderauswahl" }));
    await userEvent.click(screen.getAllByRole("radio", { name: "Zielkalender" })[1]);
    await userEvent.click(screen.getByRole("button", { name: "Auswahl speichern" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/configuration/calendars"))).toBe(true));
    const saved = calls.find((call) => call.url.endsWith("/configuration/calendars"));
    const body = JSON.parse(String(saved?.init?.body));
    expect(body.calendars.filter((item: { is_selected_for_booking: boolean }) => item.is_selected_for_booking)).toHaveLength(1);
    expect(body.calendars.find((item: { calendar_id: string }) => item.calendar_id === "calendar-2").is_selected_for_booking).toBe(true);
  });

  it("bearbeitet mehrere Geschäftszeitfenster und sendet wirksame Regeln", async () => {
    const calls = installFetch(); render(<CalendarSettingsPage />);
    await screen.findByText("Google Kalender");
    await userEvent.click(screen.getByRole("tab", { name: "Verfügbarkeit" }));
    await userEvent.click(screen.getAllByRole("button", { name: "+ Zeitfenster" })[0]);
    expect(screen.getAllByLabelText("Montag Beginn")).toHaveLength(2);
    await userEvent.clear(screen.getByLabelText("Mindestvorlauf (Min.)"));
    await userEvent.type(screen.getByLabelText("Mindestvorlauf (Min.)"), "90");
    await userEvent.click(screen.getByRole("button", { name: "Regeln speichern" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/configuration") && call.init?.method === "PUT")).toBe(true));
    const saved = calls.find((call) => call.url.endsWith("/configuration") && call.init?.method === "PUT");
    expect(JSON.parse(String(saved?.init?.body)).minimum_notice_minutes).toBe(90);
  });

  it("legt eine echte Terminart an und zeigt keine fest eingebauten Beispieldaten", async () => {
    const calls = installFetch(); render(<CalendarSettingsPage />);
    await screen.findByText("Google Kalender");
    await userEvent.click(screen.getByRole("tab", { name: "Terminarten" }));
    expect(screen.getByText("Noch keine Terminart angelegt")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "Erstberatung");
    await userEvent.click(screen.getByRole("button", { name: "Terminart anlegen" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/appointment-types") && call.init?.method === "POST")).toBe(true));
  });

  it("zeigt verständliche API-Fehler statt einen falschen Erfolgszustand", async () => {
    installFetch({ failConnections: true }); render(<CalendarSettingsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Kalenderdienst nicht erreichbar");
    expect(screen.queryByText("Verbunden")).not.toBeInTheDocument();
  });

  it("bleibt auf schmalen Ansichten semantisch vollständig bedienbar", async () => {
    Object.defineProperty(window, "innerWidth", { value: 390, configurable: true });
    installFetch(); render(<CalendarSettingsPage />);
    await screen.findByText("Google Kalender");
    const tabs = screen.getByRole("tablist");
    expect(within(tabs).getAllByRole("tab")).toHaveLength(4);
    await userEvent.click(within(tabs).getByRole("tab", { name: "Verfügbarkeit" }));
    expect(screen.getByLabelText("Zeitzone")).toBeInTheDocument();
  });
});
