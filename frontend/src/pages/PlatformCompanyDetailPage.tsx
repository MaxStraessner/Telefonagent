import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";
import type { AccountInvitation, CompanyDetail, CompanyStatus, CompanyUser, CompanyUserInvite } from "../types/api";
import { PageHeader } from "./shared";

const emptyInvite: CompanyUserInvite = { username: "", display_name: "", email: "", role: "company_user" };

export function PlatformCompanyDetailPage() {
  const { companyId = "" } = useParams();
  const { selectCompanyContext } = useAuth();
  const navigate = useNavigate();
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [users, setUsers] = useState<CompanyUser[]>([]);
  const [invitations, setInvitations] = useState<AccountInvitation[]>([]);
  const [invite, setInvite] = useState(emptyInvite);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { const [nextCompany, nextUsers, nextInvites] = await Promise.all([api.company(companyId), api.platformCompanyUsers(companyId), api.platformCompanyInvitations(companyId)]); setCompany(nextCompany); setUsers(nextUsers); setInvitations(nextInvites); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Unternehmensdaten konnten nicht geladen werden."); }
  }, [companyId]);
  useEffect(() => { void load(); }, [load]);
  async function changeStatus(target: CompanyStatus) {
    if (!window.confirm(`Status wirklich auf „${target}“ ändern? Aktive Sitzungen können widerrufen werden.`)) return;
    try { setCompany(await api.updateCompanyStatus(companyId, target)); setMessage("Status wurde serverseitig geändert."); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Status konnte nicht geändert werden."); }
  }
  async function createInvite(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { await api.invitePlatformCompanyUser(companyId, invite); setInvite(emptyInvite); setMessage("Einladung wurde erstellt und sicher zugestellt."); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Einladung konnte nicht erstellt werden."); }
  }
  async function openContext() {
    try { await selectCompanyContext(companyId); navigate("/"); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Kontext konnte nicht gewählt werden."); }
  }
  if (!company) return <div className="page">{error ? <p className="form-error" role="alert">{error}</p> : <p>Lade Unternehmen …</p>}</div>;
  const usable = company.status === "trial" || company.status === "active";
  return <div className="page">
    <PageHeader eyebrow={company.slug} title={company.name} description={company.legal_name ?? "Unternehmensverwaltung"} />
    {error && <p className="form-error" role="alert">{error}</p>}{message && <p className="form-success" role="status">{message}</p>}
    <div className="page-actions">{usable && <button className="primary-button" onClick={() => void openContext()}>Unternehmenskontext öffnen</button>}<select aria-label="Unternehmensstatus" value={company.status} onChange={(event) => void changeStatus(event.target.value as CompanyStatus)}><option value="trial">Testphase</option><option value="active">Aktiv</option><option value="suspended">Gesperrt</option><option value="archived">Archiviert</option></select></div>
    {!usable && <div className="warning-banner">Dieser Status erlaubt nur Verwaltungszugriff. Fachfunktionen, OAuth, Buchungen und Testgespräche bleiben gesperrt.</div>}
    <div className="grid company-grid"><section className="card detail-card"><h2>Stammdaten</h2><dl><dt>Status</dt><dd>{company.status}</dd><dt>Branche</dt><dd>{company.industry}</dd><dt>Zeitzone</dt><dd>{company.timezone}</dd><dt>Kontakt</dt><dd>{company.contact_name ?? "–"}<br />{company.contact_email ?? "–"}</dd><dt>Onboarding</dt><dd>{company.onboarding_complete ? "Vollständig" : "Einladung ausstehend"}</dd></dl></section>
      <section className="card detail-card"><h2>Benutzer einladen</h2><form className="settings-form" onSubmit={createInvite}><label>Name<input required value={invite.display_name} onChange={(event) => setInvite({ ...invite, display_name: event.target.value })} /></label><label>Benutzername<input required value={invite.username} onChange={(event) => setInvite({ ...invite, username: event.target.value })} /></label><label>E-Mail<input required type="email" value={invite.email} onChange={(event) => setInvite({ ...invite, email: event.target.value })} /></label><label>Rolle<select value={invite.role} onChange={(event) => setInvite({ ...invite, role: event.target.value as CompanyUserInvite["role"] })}><option value="company_user">Mitarbeiter</option><option value="company_admin">Administrator</option></select></label><button className="primary-button">Einladung senden</button></form></section></div>
    <section className="card table-card"><h2>Unternehmensbenutzer</h2><table><thead><tr><th>Name</th><th>Rolle</th><th>Status</th><th>Primär</th></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td><strong>{user.display_name}</strong><small>{user.email ?? user.username}</small></td><td>{user.role === "company_admin" ? "Administrator" : "Mitarbeiter"}</td><td>{user.is_active ? "Aktiv" : "Deaktiviert"}</td><td>{user.is_primary_admin ? "Ja" : "–"}</td></tr>)}</tbody></table></section>
    <section className="card table-card"><h2>Einladungen</h2><table><thead><tr><th>Empfänger</th><th>Rolle</th><th>Status</th><th>Gültig bis</th></tr></thead><tbody>{invitations.map((item) => <tr key={item.id}><td>{item.email}</td><td>{item.role}</td><td>{item.status}</td><td>{new Date(item.expires_at).toLocaleString("de-DE")}</td></tr>)}</tbody></table></section>
  </div>;
}
