import { useEffect, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";
import type { PlatformAdmin } from "../types/api";
import { PageHeader } from "./shared";

export function PlatformAdminsPage() {
  const { session } = useAuth();
  const [admins, setAdmins] = useState<PlatformAdmin[]>([]);
  const [form, setForm] = useState({ username: "", display_name: "", email: "", current_password: "" });
  const [pendingAdmin, setPendingAdmin] = useState<PlatformAdmin | null>(null);
  const [reauthPassword, setReauthPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  async function load() { try { setAdmins(await api.platformAdmins()); } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Administratoren konnten nicht geladen werden."); } }
  useEffect(() => { void load(); }, []);
  if (session?.user.platform_role !== "owner") return <Navigate to="/plattform" replace />;
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { await api.invitePlatformAdmin(form); setForm({ username: "", display_name: "", email: "", current_password: "" }); setMessage("Plattformadmin-Einladung wurde erstellt."); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Einladung konnte nicht erstellt werden."); }
  }
  async function confirmToggle(event: FormEvent) {
    event.preventDefault();
    if (!pendingAdmin) return;
    try { await api.updatePlatformAdmin(pendingAdmin.id, { display_name: pendingAdmin.display_name, email: pendingAdmin.email, is_active: !pendingAdmin.is_active, current_password: reauthPassword }); setPendingAdmin(null); setReauthPassword(""); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Administrator konnte nicht geändert werden."); }
  }
  return <div className="page"><PageHeader eyebrow="Owner-Bereich" title="Plattformadministratoren" description="Nur der Plattforminhaber kann weitere Plattformadmins verwalten. Jede Änderung verlangt Reauthentifizierung." />
    {error && <p className="form-error" role="alert">{error}</p>}{message && <p className="form-success" role="status">{message}</p>}
    <div className="grid company-grid"><section className="card detail-card"><h2>Admin einladen</h2><form className="settings-form" onSubmit={submit}><label>Name<input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label><label>Benutzername<input required value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></label><label>E-Mail<input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label><label>Aktuelles Owner-Passwort<input required type="password" autoComplete="current-password" value={form.current_password} onChange={(event) => setForm({ ...form, current_password: event.target.value })} /></label><button className="primary-button">Sicher einladen</button></form></section>
      <section className="card detail-card"><h2>Bestehende Plattformkonten</h2>{admins.map((admin) => <article className="account-row" key={admin.id}><div><strong>{admin.display_name}</strong><p>{admin.platform_role === "owner" ? "Plattforminhaber" : "Plattformadmin"} · {admin.email}</p><small>{admin.is_active ? "Aktiv" : "Deaktiviert"}</small></div>{admin.platform_role !== "owner" && <button type="button" onClick={() => setPendingAdmin(admin)}>{admin.is_active ? "Deaktivieren" : "Aktivieren"}</button>}</article>)}</section></div>
    {pendingAdmin && <div className="dialog-backdrop"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-confirm-title"><h2 id="admin-confirm-title">Plattformadmin {pendingAdmin.is_active ? "deaktivieren" : "aktivieren"}</h2><p>Die Änderung widerruft aktive Sitzungen von {pendingAdmin.display_name}.</p><form className="settings-form" onSubmit={confirmToggle}><label>Aktuelles Owner-Passwort<input autoFocus required type="password" autoComplete="current-password" value={reauthPassword} onChange={(event) => setReauthPassword(event.target.value)} /></label><div className="form-actions"><button type="button" onClick={() => { setPendingAdmin(null); setReauthPassword(""); }}>Abbrechen</button><button className="primary-button">Bestätigen</button></div></form></section></div>}
  </div>;
}
