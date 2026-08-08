import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api } from "../api/client";
import type {
  AccountInvitation,
  CompanyDetail,
  CompanyStatus,
  CompanyUser,
  CompanyUserCreate,
} from "../types/api";
import { accountErrorMessage, PageHeader } from "./shared";

const emptyUser: CompanyUserCreate = {
  username: "",
  display_name: "",
  email: null,
  role: "company_user",
  password: "",
};

const statusLabels: Record<CompanyStatus, string> = {
  trial: "Testphase",
  active: "Aktiv",
  suspended: "Gesperrt",
  archived: "Archiviert",
};

function accountStatus(user: CompanyUser) {
  if (!user.is_active) return "Deaktiviert";
  return user.must_change_password ? "Passwortwechsel erforderlich" : "Aktiv";
}

export function PlatformCompanyDetailPage() {
  const { companyId = "" } = useParams();
  const { selectCompanyContext } = useAuth();
  const navigate = useNavigate();
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [users, setUsers] = useState<CompanyUser[]>([]);
  const [invitations, setInvitations] = useState<AccountInvitation[]>([]);
  const [form, setForm] = useState(emptyUser);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextCompany, nextUsers, nextInvites] = await Promise.all([
        api.company(companyId),
        api.platformCompanyUsers(companyId),
        api.platformCompanyInvitations(companyId),
      ]);
      setCompany(nextCompany);
      setUsers(nextUsers);
      setInvitations(nextInvites);
    } catch (cause) {
      setError(accountErrorMessage(cause, "Unternehmensdaten konnten nicht geladen werden."));
    }
  }, [companyId]);

  useEffect(() => { void load(); }, [load]);

  async function changeStatus(target: CompanyStatus) {
    if (!window.confirm(`Status wirklich auf „${statusLabels[target]}“ ändern? Aktive Sitzungen können widerrufen werden.`)) return;
    setError(null);
    try {
      setCompany(await api.updateCompanyStatus(companyId, target));
      setMessage("Unternehmensstatus wurde geändert.");
    } catch (cause) {
      setError(accountErrorMessage(cause, "Status konnte nicht geändert werden."));
    }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setSaving(true);
    try {
      const created = await api.createPlatformCompanyUser(companyId, form);
      setUsers((current) => [...current, created].sort((left, right) => left.display_name.localeCompare(right.display_name, "de")));
      setForm(emptyUser);
      setMessage("Benutzer wurde erstellt.");
    } catch (cause) {
      setError(accountErrorMessage(cause, "Benutzer konnte nicht erstellt werden."));
    } finally {
      setSaving(false);
    }
  }

  async function updateUser(user: CompanyUser, changes: Partial<Pick<CompanyUser, "role" | "is_active">>, success: string) {
    setError(null);
    setMessage(null);
    try {
      const updated = await api.updatePlatformCompanyUser(companyId, user.id, {
        display_name: user.display_name,
        email: user.email,
        role: changes.role ?? user.role,
        is_active: changes.is_active ?? user.is_active,
      });
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item));
      setMessage(success);
    } catch (cause) {
      setError(accountErrorMessage(cause, "Benutzer konnte nicht geändert werden."));
    }
  }

  async function transferPrimary(user: CompanyUser) {
    if (!window.confirm(`Primäre Administratorverantwortung an ${user.display_name} übertragen?`)) return;
    setError(null);
    try {
      await api.transferPlatformPrimaryAdmin(companyId, user.id);
      setMessage("Primäre Administratorverantwortung wurde übertragen.");
      await load();
    } catch (cause) {
      setError(accountErrorMessage(cause, "Verantwortung konnte nicht übertragen werden."));
    }
  }

  async function openContext() {
    try {
      await selectCompanyContext(companyId);
      navigate("/");
    } catch (cause) {
      setError(accountErrorMessage(cause, "Kontext konnte nicht gewählt werden."));
    }
  }

  if (!company) {
    return <div className="page">{error ? <p className="form-error" role="alert">{error}</p> : <p>Lade Unternehmen …</p>}</div>;
  }

  const usable = company.status === "trial" || company.status === "active";
  return <div className="page platform-page">
    <PageHeader
      eyebrow={company.slug}
      title={company.name}
      description={company.legal_name ?? "Unternehmensverwaltung"}
      action={<span className={`status-pill ${company.status}`}>{statusLabels[company.status]}</span>}
    />
    {error && <p className="form-error" role="alert">{error}</p>}
    {message && <p className="form-success" role="status">{message}</p>}

    <div className="management-toolbar">
      {usable && <button className="primary-button" onClick={() => void openContext()}>Unternehmenskontext öffnen</button>}
      <label className="compact-field">Status
        <select aria-label="Unternehmensstatus" value={company.status} onChange={(event) => void changeStatus(event.target.value as CompanyStatus)}>
          {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
    </div>
    {!usable && <div className="warning-banner">Dieser Status erlaubt nur Verwaltungszugriff. Fachfunktionen, OAuth, Buchungen und Testgespräche bleiben gesperrt.</div>}

    <div className="management-grid">
      <section className="card admin-card detail-card">
        <div className="card-heading"><div><p className="card-kicker">Unternehmen</p><h2>Stammdaten</h2></div></div>
        <dl className="detail-list">
          <div><dt>Branche</dt><dd>{company.industry}</dd></div>
          <div><dt>Zeitzone</dt><dd>{company.timezone}</dd></div>
          <div><dt>Kontakt</dt><dd>{company.contact_name ?? "–"}<br />{company.contact_email ?? "Keine E-Mail hinterlegt"}</dd></div>
          <div><dt>Onboarding</dt><dd>{company.onboarding_complete ? "Vollständig" : "Primäradmin ausstehend"}</dd></div>
        </dl>
      </section>

      <section className="card admin-card">
        <div className="card-heading"><div><p className="card-kicker">Zugriff</p><h2>Benutzer erstellen</h2><p>Das Startpasswort muss beim ersten Login geändert werden.</p></div></div>
        <form className="admin-form" onSubmit={createUser}>
          <div className="form-grid">
            <label className="form-field"><span>Name</span><input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
            <label className="form-field"><span>Benutzername</span><input required autoComplete="off" value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></label>
            <label className="form-field"><span>Rolle</span><select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as CompanyUserCreate["role"] })}><option value="company_admin">Administrator</option><option value="company_user">Mitarbeiter</option></select></label>
            <label className="form-field"><span>Startpasswort</span><input required minLength={15} maxLength={128} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /><small>Mindestens 15 Zeichen</small></label>
            <label className="form-field form-field-wide"><span>E-Mail <small>optional</small></span><input type="email" value={form.email ?? ""} onChange={(event) => setForm({ ...form, email: event.target.value || null })} /></label>
          </div>
          <div className="form-actions form-actions-end"><button className="primary-button" disabled={saving}>{saving ? "Wird erstellt …" : "Benutzer erstellen"}</button></div>
        </form>
      </section>
    </div>

    <section className="card table-card management-table">
      <div className="table-heading"><div><p className="card-kicker">Zugriffsverwaltung</p><h2>Unternehmensbenutzer</h2></div><span>{users.length} Konten</span></div>
      <table><thead><tr><th>Name</th><th>Benutzername</th><th>Rolle</th><th>Status</th><th>Primär</th><th>Aktionen</th></tr></thead>
        <tbody>{users.map((user) => <tr key={user.id}>
          <td><strong>{user.display_name}</strong><small>{user.email ?? "Keine E-Mail"}</small></td>
          <td>{user.username}</td>
          <td><select aria-label={`Rolle von ${user.display_name}`} value={user.role} disabled={user.is_primary_admin} onChange={(event) => void updateUser(user, { role: event.target.value as CompanyUser["role"] }, "Benutzerrolle wurde geändert.")}><option value="company_admin">Administrator</option><option value="company_user">Mitarbeiter</option></select></td>
          <td><span className={`account-status ${!user.is_active ? "inactive" : user.must_change_password ? "pending" : "active"}`}>{accountStatus(user)}</span></td>
          <td>{user.is_primary_admin ? <span className="primary-marker">Primäradmin</span> : "–"}</td>
          <td><div className="row-actions">{!user.is_primary_admin && <button type="button" onClick={() => void updateUser(user, { is_active: !user.is_active }, user.is_active ? "Benutzer wurde deaktiviert." : "Benutzer wurde aktiviert.")}>{user.is_active ? "Deaktivieren" : "Aktivieren"}</button>}{user.role === "company_admin" && user.is_active && !user.is_primary_admin && <button type="button" onClick={() => void transferPrimary(user)}>Als Primäradmin</button>}</div></td>
        </tr>)}</tbody>
      </table>
      {users.length === 0 && <p className="empty-state">Noch keine Unternehmensbenutzer vorhanden.</p>}
    </section>

    {invitations.length > 0 && <section className="card table-card management-table secondary-section">
      <div className="table-heading"><div><p className="card-kicker">Sekundärer Workflow</p><h2>Bestehende Einladungen</h2></div></div>
      <table><thead><tr><th>Empfänger</th><th>Benutzername</th><th>Rolle</th><th>Status</th><th>Gültig bis</th></tr></thead><tbody>{invitations.map((item) => <tr key={item.id}><td>{item.email}</td><td>{item.username}</td><td>{item.role === "company_admin" ? "Administrator" : "Mitarbeiter"}</td><td>{item.status}</td><td>{new Date(item.expires_at).toLocaleString("de-DE")}</td></tr>)}</tbody></table>
    </section>}
  </div>;
}
