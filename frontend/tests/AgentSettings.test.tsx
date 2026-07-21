import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentSettingsPage } from "../src/pages/AgentSettingsPage";

const config = {
  tenant_id: "11111111-1111-1111-1111-111111111111", version: 1, updated_at: "2026-07-21T10:00:00Z", can_edit: true, role: "owner",
  company_name: "Salon Haarkunst Test", assistant_name: "Lina", assistant_role: "digitaler Terminassistent", transparency_notice: "Ich bin eine KI.", address_formality: "formal", language: "de",
  standard_greeting: "Guten Tag", outside_hours_greeting: "Wir haben geschlossen", test_greeting: "Willkommen zum Test", farewell: "Auf Wiederhören",
  voice: "marin", speech_speed: 1, pronunciation_instructions: "", pronunciation_style: "neutral", regional_accent: "", tone: "friendly_service", custom_style_instructions: "", response_length: "short", question_style: "one_at_a_time",
  turn_detection_type: "server_vad", turn_eagerness: "medium", vad_threshold: .5, prefix_padding_ms: 300, silence_duration_ms: 600, interruptions_enabled: true, idle_prompt_enabled: false, idle_timeout_ms: 10000,
  primary_task: "Unterstütze bei Terminanfragen", off_topic_behavior: "Lehne sachfremde Fragen ab", off_topic_mode: "brief_redirect", uncertainty_behavior: "Sage offen, wenn etwas unbekannt ist", uncertainty_modes: ["acknowledge", "ask_clarifying"], fallback_message: "Bitte wenden Sie sich an das Unternehmen", simple_mode: true,
  topics: [{ id: "topic-1", label: "Termine", instructions: "Erfrage den Terminwunsch", topic_type: "allowed", is_active: true, sort_order: 0 }], custom_rules: [],
};
const knowledge = {
  tenant_id: config.tenant_id, version: 1, can_edit: true,
  profile: { company_description: "Friseursalon", products: "Pflegeprodukte", locations: "Hauptstandort", important_notes: "Keine Preiszusagen", contact_phone: "", contact_email: "", website: "" }, faqs: [],
  services: [{ id: "service-1", name: "Haarschnitt", description: "Klassisch", price_information: "Preis auf Anfrage", is_active: true, sort_order: 0 }],
  business_hours: Array.from({ length: 7 }, (_, weekday) => ({ weekday, opens_at: "09:00", closes_at: "18:00", is_closed: weekday === 6 })),
};
const catalog = { voices: [{ value: "marin", label: "Marin", recommended: true }, { value: "cedar", label: "Cedar", recommended: true }], capabilities: [] };
const runtime = { tenant_id: config.tenant_id, configuration_version: 1, company_name: "Salon Haarkunst Test", assistant_name: "Lina", language: "de", style: "friendly_service", business_hours_status: "open", model: "gpt-realtime-2.1", voice: "marin", speed: 1, turn_detection: { type: "server_vad" }, capability_keys: [], tool_names: [], greeting: "Willkommen zum Test", prompt_sections: ["Plattformregeln"] };

function installApi(overrides: { canEdit?: boolean } = {}) {
  const calls: Array<{ url: string; method: string; body?: Record<string, unknown> }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined;
    calls.push({ url, method, body });
    let response: unknown;
    if (url.endsWith("/agent/config") && method === "PUT") response = { ...body, version: 2, updated_at: "2026-07-21T10:01:00Z", can_edit: true, role: "owner" };
    else if (url.endsWith("/agent/config")) response = { ...config, can_edit: overrides.canEdit ?? true, role: overrides.canEdit === false ? "member" : "owner" };
    else if (url.endsWith("/agent/knowledge")) response = { ...knowledge, can_edit: overrides.canEdit ?? true };
    else if (url.endsWith("/agent/catalog")) response = catalog;
    else if (url.endsWith("/agent/test-session")) response = { ...runtime, configuration_version: calls.some((call) => call.method === "PUT") ? 2 : 1 };
    else response = {};
    return new Response(JSON.stringify(response), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}

beforeEach(() => { installApi(); vi.stubGlobal("confirm", vi.fn(() => true)); });
afterEach(() => vi.unstubAllGlobals());

describe("KI-Einstellungen", () => {
  it("lädt alle Bereiche und zeigt die wirksame Testkonfiguration", async () => {
    render(<MemoryRouter><AgentSettingsPage /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "KI konfigurieren" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Lina")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Testen" }));
    expect(screen.getByText("gpt-realtime-2.1")).toBeInTheDocument();
    expect(screen.getByText("Willkommen zum Test")).toBeInTheDocument();
    expect(screen.getAllByText("Keine", { selector: "dd" })).toHaveLength(2);
  });

  it("kennzeichnet Änderungen, kann zurücksetzen und speichert mit Versionsschutz", async () => {
    const calls = installApi();
    render(<MemoryRouter><AgentSettingsPage /></MemoryRouter>);
    const input = await screen.findByDisplayValue("Lina");
    await userEvent.clear(input); await userEvent.type(input, "Mira");
    expect(screen.getByText("Nicht gespeicherte Änderungen")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Zurücksetzen" }));
    expect(screen.getByDisplayValue("Lina")).toBeInTheDocument();
    await userEvent.clear(screen.getByDisplayValue("Lina")); await userEvent.type(screen.getByLabelText("Name des Assistenten"), "Mira");
    await userEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
    expect(await screen.findByText(/Gespeichert/)).toBeInTheDocument();
    const write = calls.find((call) => call.method === "PUT");
    expect(write?.body?.expected_version).toBe(1);
    expect(write?.body?.assistant_name).toBe("Mira");
    expect(screen.getByText(/Version 2 ist gespeichert/)).toBeInTheDocument();
  });

  it("erzwingt bei Mitgliedern einen schreibgeschützten Zustand", async () => {
    installApi({ canEdit: false });
    render(<MemoryRouter><AgentSettingsPage /></MemoryRouter>);
    expect(await screen.findByText(/darf die Konfiguration ansehen/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Lina")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Änderungen speichern" })).toBeDisabled();
  });
});
