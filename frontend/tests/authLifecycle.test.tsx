import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";

afterEach(() => vi.unstubAllGlobals());

function json(value: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function unauthenticatedBootstrap(url: string) {
  if (url.endsWith("/auth/session")) return json({ error: {} }, 401);
  if (url.endsWith("/auth/setup-status")) return json({ available: false });
  return null;
}

it("zeigt nach einer Recovery-Anfrage immer die neutrale Bestätigung", async () => {
  window.history.replaceState({}, "", "/passwort-vergessen");
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    const bootstrap = unauthenticatedBootstrap(url);
    if (bootstrap) return Promise.resolve(bootstrap);
    if (url.endsWith("/auth/forgot-password")) return Promise.resolve(json(null, 204));
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await userEvent.type(await screen.findByLabelText("Benutzername oder E-Mail"), "person@example.test");
  await userEvent.click(screen.getByRole("button", { name: "Link anfordern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Falls ein aktives Konto existiert");
});

it("setzt ein Passwort nur mit dem Token aus der URL zurück", async () => {
  const token = "valid-reset-token-with-more-than-twenty-characters";
  window.history.replaceState({}, "", `/passwort-zuruecksetzen?token=${token}`);
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const bootstrap = unauthenticatedBootstrap(url);
    if (bootstrap) return Promise.resolve(bootstrap);
    if (url.endsWith("/auth/reset-password")) {
      expect(JSON.parse(String(init?.body))).toMatchObject({ token });
      return Promise.resolve(json(null, 204));
    }
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  const fields = await screen.findAllByLabelText(/Passwort/);
  await userEvent.type(fields[0], "replacement password long enough");
  await userEvent.type(fields[1], "replacement password long enough");
  await userEvent.click(screen.getByRole("button", { name: "Passwort ändern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Passwort wurde geändert");
});

it("prüft eine Einladung vor der Annahme und zeigt nur redigierte Kontodaten", async () => {
  const token = "valid-invitation-token-with-more-than-twenty-characters";
  window.history.replaceState({}, "", `/einladung/${token}`);
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    const bootstrap = unauthenticatedBootstrap(url);
    if (bootstrap) return Promise.resolve(bootstrap);
    if (url.includes("/auth/invitations/") && url.endsWith(token)) {
      const calls = fetchMock.mock.calls.filter(([value]) => String(value).includes("/auth/invitations/"));
      return Promise.resolve(calls.length === 1
        ? json({ email: "invite@example.test", display_name: "Invite Person", company_name: "Beispiel GmbH", role: "company_user", expires_at: "2026-08-08T12:00:00Z" })
        : json(null, 204));
    }
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByText("Sie wurden zu Beispiel GmbH eingeladen.")).toBeInTheDocument();
  const fields = screen.getAllByLabelText(/Passwort/);
  await userEvent.type(fields[0], "invitation password long enough");
  await userEvent.type(fields[1], "invitation password long enough");
  await userEvent.click(screen.getByRole("button", { name: "Einladung annehmen" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Konto ist bereit");
});

it("leitet ein Konto mit Pflichtwechsel vor jedem Tenant-Request um", async () => {
  window.history.replaceState({}, "", "/");
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(input, { ...init, signal: undefined });
    }
  });
  const session = {
    user: { id: "u1", username: "temporary", email: null, display_name: "Temporary", role: "company_user", platform_role: null, is_platform_admin: false, must_change_password: true },
    tenant: { id: "t1", slug: "beispiel", name: "Beispiel GmbH" },
    active_company: { id: "t1", slug: "beispiel", name: "Beispiel GmbH" },
    membership: { tenant_id: "t1", role: "company_user", is_primary_admin: false },
    permissions: [], mode: "company", idle_expires_at: "2026-08-05T13:00:00Z", absolute_expires_at: "2026-08-05T20:00:00Z",
  };
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/auth/session")) return Promise.resolve(json(session));
    throw new Error(`Unexpected tenant request ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Passwort ändern" })).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
});
