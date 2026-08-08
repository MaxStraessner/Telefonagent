import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";
import type { AccountInvitation, CompanyUser, CompanyUserInvite } from "../types/api";
import { PageHeader } from "./shared";

const emptyInvite: CompanyUserInvite = { username: "", display_name: "", email: "", role: "company_user" };

export function AccountsPage() {
  const { session } = useAuth();
  const [users, setUsers] = useState<CompanyUser[]>([]);
  const [invitations, setInvitations] = useState<AccountInvitation[]>([]);
  const [form, setForm] = useState(emptyInvite);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const canChangeRoles = Boolean(session?.membership?.is_primary_admin || session?.user.platform_role);
  async function load() {
    try { const [nextUsers, nextInvitations] = await Promise.all([api.ownCompanyUsers(), api.ownCompanyInvitations()]); setUsers(nextUsers); setInvitations(nextInvitations); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Konten konnten nicht geladen werden."); }
  }
  useEffect(() => { void load(); }, []);
  async function invite(event: FormEvent) {
    event.preventDefault(); setError(null); setMessage(null); setSaving(true);
    try { await api.inviteOwnCompanyUser(form); setForm(emptyInvite); setMessage("Einladung wurde sicher erstellt und zugestellt."); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Einladung konnte nicht erstellt werden."); }
    finally { setSaving(false); }
  }
  async function toggle(user: CompanyUser) {
    if (!window.confirm(`${user.display_name} wirklich ${user.is_active ? "deaktivieren" : "aktivieren"}?`)) return;
    try { await api.updateOwnCompanyUser(user.id, { display_name: user.display_name, email: user.email, role: user.role, is_active: !user.is_active }); setMessage("Kontostatus wurde geändert."); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Konto konnte nicht geändert werden."); }
  }
  async function transfer(user: CompanyUser) {
    if (!window.confirm(`Primäre Verantwortung an ${user.display_name} übertragen?`)) return;
    try { await api.transferOwnPrimaryAdmin(user.id); setMessage("Primäre Verantwortung wurde übertragen."); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Verantwortung konnte nicht übertragen werden."); }
  }
  async function revoke(invitation: AccountInvitation) {
    if (!window.confirm(`Einladung für ${invitation.email} widerrufen?`)) return;
    try { await api.revokeOwnCompanyInvitation(invitation.id); setMessage("Einladung wurde widerrufen."); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Einladung konnte nicht widerrufen werden."); }
  }
  return <div className="page">
    <PageHeader eyebrow="Zugriffsverwaltung" title="Unternehmensbenutzer" description="Einladungen, Rollen und Primärverantwortung gelten ausschließlich im sichtbaren Unternehmenskontext." />
    {error && <p className="form-error" role="alert">{error}</p>}{message && <p className="form-success" role="status">{message}</p>}
    <div className="grid company-grid"><section className="card detail-card"><h2>Benutzer einladen</h2><form className="settings-form" onSubmit={invite}><label>Name<input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label><label>Benutzername<input required value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></label><label>E-Mail<input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label><label>Rolle<select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as CompanyUserInvite["role"] })}><option value="company_user">Mitarbeiter</option>{canChangeRoles && <option value="company_admin">Administrator</option>}</select></label><button className="primary-button" disabled={saving}>{saving ? "Wird gesendet …" : "Einladung senden"}</button></form></section>
      <section className="card detail-card"><h2>Bestehende Konten</h2>{users.map((user) => <article className="account-row" key={user.id}><div><strong>{user.display_name}{user.is_primary_admin ? " · Primäradmin" : ""}</strong><p>{user.username} · {user.role === "company_admin" ? "Administrator" : "Mitarbeiter"}</p><small>{user.is_active ? "Aktiv" : "Deaktiviert"}</small></div><div className="row-actions">{user.id !== session?.user.id && !user.is_primary_admin && <button type="button" onClick={() => void toggle(user)}>{user.is_active ? "Deaktivieren" : "Aktivieren"}</button>}{canChangeRoles && user.role === "company_admin" && user.is_active && !user.is_primary_admin && <button type="button" onClick={() => void transfer(user)}>Als Primäradmin</button>}</div></article>)}</section></div>
    <section className="card table-card"><h2>Einladungen</h2><table><thead><tr><th>Empfänger</th><th>Rolle</th><th>Status</th><th></th></tr></thead><tbody>{invitations.map((item) => <tr key={item.id}><td>{item.email}</td><td>{item.role}</td><td>{item.status}</td><td>{item.status === "sent" && <button type="button" onClick={() => void revoke(item)}>Widerrufen</button>}</td></tr>)}</tbody></table></section>
  </div>;
}
