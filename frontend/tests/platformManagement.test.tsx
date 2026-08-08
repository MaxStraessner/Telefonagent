import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";

afterEach(() => vi.unstubAllGlobals());

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

const dashboard = {
  companies_total: 4, companies_trial: 1, companies_active: 2,
  companies_suspended: 1, companies_archived: 0,
  active_company_users: 7, pending_invitations: 2,
};

function platformSession(role: "owner" | "admin", active = false) {
  return {
    user: { id: "platform-user", username: role, email: `${role}@example.test`, display_name: role === "owner" ? "Platform Owner" : "Platform Admin", role: null, platform_role: role, is_platform_admin: true, must_change_password: false },
    tenant: active ? { id: "company-1", slug: "example", name: "Example GmbH" } : null,
    active_company: active ? { id: "company-1", slug: "example", name: "Example GmbH" } : null,
    membership: null,
    permissions: ["platform.read", "platform.companies.manage", "platform.audit.read", "company.context.select", ...(role === "owner" ? ["platform.admins.manage"] : [])],
    mode: active ? "company" : "platform",
    idle_expires_at: "2026-08-05T18:00:00Z", absolute_expires_at: "2026-08-06T00:00:00Z",
  };
}

it("zeigt Plattformkennzahlen ohne impliziten Tenant-Request und schützt die Owner-Navigation", async () => {
  window.history.replaceState({}, "", "/plattform");
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(platformSession("admin")));
    if (url.endsWith("/platform/dashboard")) return Promise.resolve(json(dashboard));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  expect(await screen.findByText("4")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Plattformadmins" })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/tenant"))).toBe(false);
});

it("zeigt den aktiven Supportkontext dauerhaft und bietet das Verlassen an", async () => {
  window.history.replaceState({}, "", "/plattform");
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(platformSession("owner", true)));
    if (url.endsWith("/platform/dashboard")) return Promise.resolve(json(dashboard));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByText("Aktiver Unternehmenskontext: Example GmbH")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Kontext verlassen" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Plattformadmins" })).toBeInTheDocument();
});

it("legt Unternehmen im zweistufigen Wizard mit direktem Startkonto ohne E-Mail an", async () => {
  window.history.replaceState({}, "", "/plattform/unternehmen/neu");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  });
  const company = { id: "company-new", slug: "neue-gmbh", name: "Neue GmbH", legal_name: "Neue GmbH", status: "trial", is_demo: false, active_user_count: 0, has_primary_admin: false, onboarding_complete: false, created_at: "2026-08-05T12:00:00Z", industry: "services", timezone: "Europe/Berlin", contact_name: null, contact_email: null, contact_phone: null, default_language: "de" };
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(platformSession("owner")));
    if (url.endsWith("/platform/companies") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      expect(body.first_admin).toMatchObject({ delivery: "temporary_password", username: "erste-admin", email: null, temporary_password: "sicheres startpasswort" });
      return Promise.resolve(json(company, 201));
    }
    if (url.endsWith("/platform/companies/company-new")) return Promise.resolve(json(company));
    if (url.endsWith("/platform/companies/company-new/users") || url.endsWith("/platform/companies/company-new/invitations")) return Promise.resolve(json([]));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await userEvent.type(await screen.findByLabelText("Anzeigename"), "Neue GmbH");
  await userEvent.type(screen.getByLabelText(/^Rechtlicher Name/), "Neue GmbH");
  await userEvent.type(screen.getByLabelText(/^Slug/), "neue-gmbh");
  await userEvent.click(screen.getByRole("button", { name: "Weiter" }));
  await userEvent.type(screen.getByLabelText("Name"), "Erste Admin");
  await userEvent.type(screen.getByLabelText("Benutzername"), "erste-admin");
  await userEvent.type(screen.getByLabelText(/^Startpasswort/), "sicheres startpasswort");
  await userEvent.click(screen.getByRole("button", { name: "Unternehmen und Startkonto anlegen" }));
  expect(await screen.findByRole("heading", { name: "Neue GmbH" })).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
});

it("erstellt einen Unternehmensbenutzer ohne E-Mail und zeigt ihn sofort in der Liste", async () => {
  window.history.replaceState({}, "", "/plattform/unternehmen/company-1");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  });
  const company = { id: "company-1", slug: "example", name: "Example GmbH", legal_name: "Example GmbH", status: "active", is_demo: false, active_user_count: 0, has_primary_admin: false, onboarding_complete: false, created_at: "2026-08-05T12:00:00Z", industry: "services", timezone: "Europe/Berlin", contact_name: null, contact_email: null, contact_phone: null, default_language: "de" };
  const created = { id: "user-new", username: "neue-admin", display_name: "Neue Admin", email: null, role: "company_admin", is_active: true, is_primary_admin: false, must_change_password: true, last_login_at: null };
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(platformSession("owner")));
    if (url.endsWith("/platform/companies/company-1") && (!init?.method || init.method === "GET")) return Promise.resolve(json(company));
    if (url.endsWith("/platform/companies/company-1/users") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      expect(body).toEqual({ username: "neue-admin", display_name: "Neue Admin", email: null, role: "company_admin", password: "sicheres startpasswort" });
      return Promise.resolve(json(created, 201));
    }
    if (url.endsWith("/platform/companies/company-1/users")) return Promise.resolve(json([]));
    if (url.endsWith("/platform/companies/company-1/invitations")) return Promise.resolve(json([]));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Benutzer erstellen" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Administrator" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Mitarbeiter" })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Name"), "Neue Admin");
  await userEvent.type(screen.getByLabelText("Benutzername"), "neue-admin");
  await userEvent.selectOptions(screen.getByLabelText("Rolle"), "company_admin");
  await userEvent.type(screen.getByLabelText(/^Startpasswort/), "sicheres startpasswort");
  await userEvent.click(screen.getByRole("button", { name: "Benutzer erstellen" }));
  expect(await screen.findByText("Benutzer wurde erstellt.")).toBeInTheDocument();
  expect(screen.getByText("Neue Admin")).toBeInTheDocument();
  expect(screen.getByText("neue-admin")).toBeInTheDocument();
  expect(screen.getByText("Passwortwechsel erforderlich")).toBeInTheDocument();
});

it("zeigt Konflikte der direkten Benutzeranlage verständlich an", async () => {
  window.history.replaceState({}, "", "/plattform/unternehmen/company-1");
  const company = { id: "company-1", slug: "example", name: "Example GmbH", legal_name: null, status: "active", is_demo: false, active_user_count: 0, has_primary_admin: false, onboarding_complete: false, created_at: "2026-08-05T12:00:00Z", industry: "services", timezone: "Europe/Berlin", contact_name: null, contact_email: null, contact_phone: null, default_language: "de" };
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(platformSession("owner")));
    if (url.endsWith("/platform/companies/company-1") && (!init?.method || init.method === "GET")) return Promise.resolve(json(company));
    if (url.endsWith("/platform/companies/company-1/users") && init?.method === "POST") return Promise.resolve(json({ error: { code: "account_conflict", message: "Dieser Benutzername ist bereits vergeben." } }, 409));
    if (url.endsWith("/platform/companies/company-1/users") || url.endsWith("/platform/companies/company-1/invitations")) return Promise.resolve(json([]));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await userEvent.type(await screen.findByLabelText("Name"), "Doppelte Person");
  await userEvent.type(screen.getByLabelText("Benutzername"), "doppelt");
  await userEvent.type(screen.getByLabelText(/^Startpasswort/), "sicheres startpasswort");
  await userEvent.click(screen.getByRole("button", { name: "Benutzer erstellen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Dieser Benutzername ist bereits vergeben.");
});

it("legt einen Plattformadmin direkt und ohne E-Mail an", async () => {
  window.history.replaceState({}, "", "/plattform/administratoren");
  const owner = { id: "owner", username: "owner", display_name: "Platform Owner", email: null, platform_role: "owner", is_active: true, must_change_password: false, last_login_at: null };
  const created = { id: "admin-new", username: "platform-admin", display_name: "Platform Admin", email: null, platform_role: "admin", is_active: true, must_change_password: true, last_login_at: null };
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(platformSession("owner")));
    if (url.endsWith("/platform/admins") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      expect(body).toEqual({ username: "platform-admin", display_name: "Platform Admin", email: null, password: "sicheres startpasswort", current_password: "owner passwort korrekt" });
      return Promise.resolve(json(created, 201));
    }
    if (url.endsWith("/platform/admins")) return Promise.resolve(json([owner]));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await userEvent.type(await screen.findByLabelText("Name"), "Platform Admin");
  await userEvent.type(screen.getByLabelText("Benutzername"), "platform-admin");
  await userEvent.type(screen.getByLabelText(/^Startpasswort/), "sicheres startpasswort");
  await userEvent.type(screen.getByLabelText("Owner-Passwort zur Reauthentifizierung"), "owner passwort korrekt");
  await userEvent.click(screen.getByRole("button", { name: "Plattformadmin erstellen" }));
  expect(await screen.findByText("Plattformadministrator wurde erstellt.")).toBeInTheDocument();
  expect(screen.getByText("platform-admin")).toBeInTheDocument();
  expect(screen.getByText("Passwortwechsel erforderlich")).toBeInTheDocument();
});

it("verwirft beim Kontextwechsel alle alten Unternehmensdaten", async () => {
  window.history.replaceState({}, "", "/");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  });
  let active = "company-1";
  const sessionFor = () => active === "company-1" ? platformSession("owner", true) : {
    ...platformSession("owner", true),
    tenant: { id: "company-2", slug: "beta", name: "Beta GmbH" },
    active_company: { id: "company-2", slug: "beta", name: "Beta GmbH" },
  };
  const companySummary = { id: "company-2", slug: "beta", name: "Beta GmbH", legal_name: "Beta GmbH", status: "active", is_demo: false, active_user_count: 1, has_primary_admin: true, onboarding_complete: true, created_at: "2026-08-05T12:00:00Z" };
  const companyDetail = { ...companySummary, industry: "services", timezone: "Europe/Berlin", contact_name: null, contact_email: null, contact_phone: null, default_language: "de" };
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(sessionFor()));
    if (url.endsWith("/auth/context") && init?.method === "POST") { active = "company-2"; return Promise.resolve(json(sessionFor())); }
    if (url.endsWith("/tenant")) return Promise.resolve(json({ id: active, slug: active === "company-1" ? "example" : "beta", name: active === "company-1" ? "Example GmbH" : "Beta GmbH", industry: "services", timezone: "Europe/Berlin", status: "active", settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Hallo", presentation_mode_enabled: false, diagnostics_enabled: true }, primary_location: null }));
    if (url.endsWith("/services") || url.endsWith("/staff") || url.endsWith("/appointments")) return Promise.resolve(json([]));
    if (url.endsWith("/platform/status")) return Promise.resolve(json({ environment: "test", backend_version: "1", realtime_voice_configured: false, telephony_configured: false, calendar_configured: false, database_connected: true, realtime_model: "model", realtime_voice: "voice" }));
    if (url.endsWith("/health")) return Promise.resolve(json({ status: "healthy", database: "connected" }));
    if (url.endsWith("/platform/dashboard")) return Promise.resolve(json(dashboard));
    if (url.includes("/platform/companies?") || url.endsWith("/platform/companies")) return Promise.resolve(json([companySummary]));
    if (url.endsWith("/platform/companies/company-2")) return Promise.resolve(json(companyDetail));
    if (url.endsWith("/platform/companies/company-2/users") || url.endsWith("/platform/companies/company-2/invitations")) return Promise.resolve(json([]));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByText("Guten Tag bei Example GmbH")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "Plattformverwaltung" }));
  await userEvent.click(await screen.findByRole("link", { name: "Unternehmen" }));
  await userEvent.click(await screen.findByRole("link", { name: "Öffnen" }));
  await userEvent.click(await screen.findByRole("button", { name: "Unternehmenskontext öffnen" }));
  expect(await screen.findByText("Guten Tag bei Beta GmbH")).toBeInTheDocument();
  expect(screen.queryByText("Guten Tag bei Example GmbH")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/tenant"))).toHaveLength(2);
});

it("blockiert für Unternehmensbenutzer auch direkte Verwaltungs-URLs", async () => {
  window.history.replaceState({}, "", "/konten");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  });
  const session = {
    user: { id: "company-user", username: "user", email: "user@example.test", display_name: "Company User", role: "company_user", platform_role: null, is_platform_admin: false, must_change_password: false },
    tenant: { id: "company-1", slug: "example", name: "Example GmbH" }, active_company: { id: "company-1", slug: "example", name: "Example GmbH" },
    membership: { tenant_id: "company-1", role: "company_user", is_primary_admin: false }, permissions: ["company.read", "company.features.use"], mode: "company",
    idle_expires_at: "2026-08-05T18:00:00Z", absolute_expires_at: "2026-08-06T00:00:00Z",
  };
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(session));
    if (url.endsWith("/tenant")) return Promise.resolve(json({ id: "company-1", slug: "example", name: "Example GmbH", industry: "services", timezone: "Europe/Berlin", status: "active", settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Hallo", presentation_mode_enabled: false, diagnostics_enabled: true }, primary_location: null }));
    if (url.endsWith("/services") || url.endsWith("/staff") || url.endsWith("/appointments")) return Promise.resolve(json([]));
    if (url.endsWith("/platform/status")) return Promise.resolve(json({ environment: "test", backend_version: "1", realtime_voice_configured: false, telephony_configured: false, calendar_configured: false, database_connected: true, realtime_model: "model", realtime_voice: "voice" }));
    if (url.endsWith("/health")) return Promise.resolve(json({ status: "healthy", database: "connected" }));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByText("Guten Tag bei Example GmbH")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Konten" })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/company/users"))).toBe(false);
});
