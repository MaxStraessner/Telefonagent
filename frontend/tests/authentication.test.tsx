import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";

afterEach(() => vi.unstubAllGlobals());

it("fragt vor der Anmeldung keine Tenant-Daten an und zeigt den Login", async () => {
  window.history.replaceState({}, "", "/login");
  const fetchMock = vi.fn((_input: string | URL | Request) => Promise.resolve(
    new Response(JSON.stringify({ error: { code: "authentication_required", message: "Bitte melden Sie sich an." } }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    }),
  ));
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Willkommen zurück" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/auth\/session$/);
  expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/auth\/setup-status$/);
});

it("meldet sich an und lädt Tenant-Daten erst nach erfolgreicher Sitzung", async () => {
  window.history.replaceState({}, "", "/login");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(input, { ...init, signal: undefined });
    }
  });
  const session = {
    user: { id: "u1", username: "owner", email: null, display_name: "Owner", role: "owner", is_platform_admin: false },
    tenant: { id: "t1", slug: "beispiel", name: "Beispiel GmbH" },
    idle_expires_at: "2026-07-24T12:30:00Z",
    absolute_expires_at: "2026-07-24T23:00:00Z",
  };
  let authenticated = false;
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session"))
      return Promise.resolve(new Response(JSON.stringify({ error: {} }), { status: 401, headers: { "Content-Type": "application/json" } }));
    if (url.endsWith("/auth/setup-status"))
      return Promise.resolve(new Response(JSON.stringify({ available: false }), { status: 200, headers: { "Content-Type": "application/json" } }));
    if (url.endsWith("/auth/login")) {
      authenticated = true;
      expect(init?.credentials).toBe("include");
      expect(new Headers(init?.headers).get("X-Requested-With")).toBe("Telefonagent");
      return Promise.resolve(new Response(JSON.stringify(session), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    expect(authenticated).toBe(true);
    const body = url.endsWith("/tenant")
      ? { id: "t1", slug: "beispiel", name: "Beispiel GmbH", industry: "services", timezone: "Europe/Berlin", status: "active", settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Hallo", presentation_mode_enabled: false, diagnostics_enabled: true }, primary_location: null }
      : url.endsWith("/platform/status")
        ? { environment: "test", backend_version: "1", realtime_voice_configured: false, telephony_configured: false, calendar_configured: false, database_connected: true, realtime_model: "model", realtime_voice: "voice" }
        : url.endsWith("/health")
          ? { status: "healthy", database: "connected" }
          : [];
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByRole("heading", { name: "Willkommen zurück" });
  await userEvent.type(screen.getByLabelText("Benutzername oder E-Mail"), "owner");
  await userEvent.type(screen.getByLabelText("Passwort"), "correct horse battery staple");
  await userEvent.click(screen.getByRole("button", { name: "Anmelden" }));
  expect(await screen.findByText("Guten Tag bei Beispiel GmbH")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/tenant"))).toBe(true));
});

it("führt die einmalige Ersteinrichtung aus und lädt Tenant-Daten danach", async () => {
  window.history.replaceState({}, "", "/einrichtung");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(input, { ...init, signal: undefined });
    }
  });
  const session = {
    user: { id: "u-setup", username: "owner", email: null, display_name: "Owner", role: "owner", is_platform_admin: false },
    tenant: { id: "t-setup", slug: "beispiel", name: "Beispiel GmbH" },
    idle_expires_at: "2026-07-24T12:30:00Z",
    absolute_expires_at: "2026-07-24T23:00:00Z",
  };
  let initialized = false;
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/session"))
      return Promise.resolve(new Response(JSON.stringify({ error: {} }), { status: 401, headers: { "Content-Type": "application/json" } }));
    if (url.endsWith("/auth/setup-status"))
      return Promise.resolve(new Response(JSON.stringify({ available: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    if (url.endsWith("/auth/initial-setup")) {
      initialized = true;
      expect(new Headers(init?.headers).get("X-Requested-With")).toBe("Telefonagent");
      return Promise.resolve(new Response(JSON.stringify(session), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    expect(initialized).toBe(true);
    const body = url.endsWith("/tenant")
      ? { id: "t-setup", slug: "beispiel", name: "Beispiel GmbH", industry: "services", timezone: "Europe/Berlin", status: "active", settings: { assistant_name: "Lina", default_language: "de", welcome_message: "Hallo", presentation_mode_enabled: false, diagnostics_enabled: true }, primary_location: null }
      : url.endsWith("/platform/status")
        ? { environment: "test", backend_version: "1", realtime_voice_configured: false, telephony_configured: false, calendar_configured: false, database_connected: true, realtime_model: "model", realtime_voice: "voice" }
        : url.endsWith("/health")
          ? { status: "healthy", database: "connected" }
          : [];
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByRole("heading", { name: "Unternehmen einrichten" });
  await userEvent.type(screen.getByLabelText("Einrichtungscode"), "setup-code");
  await userEvent.type(screen.getByLabelText("Unternehmensname"), "Beispiel GmbH");
  await userEvent.type(screen.getByLabelText("Branche"), "services");
  await userEvent.type(screen.getByLabelText("Dein Name"), "Owner");
  await userEvent.type(screen.getByLabelText("Benutzername"), "owner");
  const passwords = screen.getAllByLabelText(/Passwort/);
  await userEvent.type(passwords[0], "correct horse battery staple");
  await userEvent.type(passwords[1], "correct horse battery staple");
  await userEvent.click(screen.getByRole("button", { name: "Unternehmen einrichten" }));
  expect(await screen.findByText("Guten Tag bei Beispiel GmbH")).toBeInTheDocument();
});
