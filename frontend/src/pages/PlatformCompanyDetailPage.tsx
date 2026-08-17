import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";
import type {
  AccountInvitation,
  CompanyDetail,
  CompanyTelephony,
  CompanyStatus,
  CompanyUser,
  CompanyUserCreate,
  TwilioNumber,
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

const telephonyStatusLabels = {
  pending: "Synchronisierung ausstehend",
  synced: "Synchronisiert",
  blocked: "Blockiert",
  error: "Synchronisierung fehlgeschlagen",
} as const;

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
  const [telephony, setTelephony] = useState<CompanyTelephony | null>(null);
  const [twilioNumbers, setTwilioNumbers] = useState<TwilioNumber[]>([]);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [transferConflict, setTransferConflict] = useState<{ companyName: string } | null>(null);
  const [telephonyBusy, setTelephonyBusy] = useState(false);
  const [telephonyError, setTelephonyError] = useState<string | null>(null);
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
      const [nextTelephony, nextNumbers] = await Promise.allSettled([
        api.companyTelephony(companyId),
        api.twilioNumbers(),
      ]);
      if (nextTelephony.status === "fulfilled") {
        setTelephony(nextTelephony.value);
        setPhoneNumber(nextTelephony.value.phone_number ?? "");
      }
      if (nextNumbers.status === "fulfilled") {
        setTwilioNumbers(nextNumbers.value);
        setTelephonyError(null);
      } else {
        setTelephonyError(accountErrorMessage(nextNumbers.reason, "Twilio-Nummern konnten nicht geladen werden."));
      }
    } catch (cause) {
      setError(accountErrorMessage(cause, "Unternehmensdaten konnten nicht geladen werden."));
    }
  }, [companyId]);

  useEffect(() => { void load(); }, [load]);

  async function saveTelephony() {
    if (!phoneNumber.trim()) return;
    setTelephonyBusy(true);
    setTelephonyError(null);
    setTransferConflict(null);
    setMessage(null);
    try {
      const next = await api.assignCompanyTwilioNumber(companyId, phoneNumber.trim());
      setTelephony(next);
      setPhoneNumber(next.phone_number ?? "");
      setMessage(next.sync_status === "synced" ? "Twilio-Nummer wurde zugeordnet und synchronisiert." : "Twilio-Zuordnung wurde gespeichert; die Synchronisierung benötigt Aufmerksamkeit.");
      setTwilioNumbers(await api.twilioNumbers());
    } catch (cause) {
      setTelephonyError(accountErrorMessage(cause, "Twilio-Nummer konnte nicht zugeordnet werden."));
      if (cause instanceof ApiError && cause.code === "number_already_assigned") {
        setTransferConflict({
          companyName: typeof cause.details.assigned_company_name === "string"
            ? cause.details.assigned_company_name
            : "einem anderen Unternehmen",
        });
      }
    } finally {
      setTelephonyBusy(false);
    }
  }

  async function transferTelephony() {
    if (!transferConflict || !phoneNumber.trim()) return;
    if (!window.confirm(`Twilio-Nummer wirklich von ${transferConflict.companyName} auf ${company?.name ?? "dieses Unternehmen"} übertragen?`)) return;
    setTelephonyBusy(true);
    setTelephonyError(null);
    setMessage(null);
    try {
      const next = await api.assignCompanyTwilioNumber(companyId, phoneNumber.trim(), true);
      setTelephony(next);
      setPhoneNumber(next.phone_number ?? "");
      setTransferConflict(null);
      setMessage(next.sync_status === "synced" ? "Twilio-Nummer wurde übertragen und synchronisiert." : "Twilio-Nummer wurde übertragen; die Synchronisierung benötigt Aufmerksamkeit.");
      setTwilioNumbers(await api.twilioNumbers());
    } catch (cause) {
      setTelephonyError(accountErrorMessage(cause, "Twilio-Nummer konnte nicht übertragen werden."));
    } finally {
      setTelephonyBusy(false);
    }
  }

  async function removeTelephony() {
    if (!telephony?.phone_number) return;
    if (!window.confirm(`Zuordnung der Twilio-Nummer ${telephony.phone_number} entfernen? Die Nummer bleibt im Twilio-Konto bestehen.`)) return;
    setTelephonyBusy(true);
    setTelephonyError(null);
    setTransferConflict(null);
    setMessage(null);
    try {
      const next = await api.removeCompanyTwilioNumber(companyId);
      setTelephony(next);
      setPhoneNumber("");
      setMessage("Die Twilio-Zuordnung wurde entfernt. Die Nummer bleibt im Twilio-Konto bestehen.");
      setTwilioNumbers(await api.twilioNumbers());
    } catch (cause) {
      setTelephonyError(accountErrorMessage(cause, "Twilio-Zuordnung konnte nicht entfernt werden."));
    } finally {
      setTelephonyBusy(false);
    }
  }

  async function syncTelephony() {
    setTelephonyBusy(true);
    setTelephonyError(null);
    setMessage(null);
    try {
      const next = await api.syncCompanyTwilioNumber(companyId);
      setTelephony(next);
      setMessage(next.sync_status === "synced" ? "Twilio-Webhook wurde erneut synchronisiert." : "Synchronisierung abgeschlossen; bitte Statushinweis prüfen.");
    } catch (cause) {
      setTelephonyError(accountErrorMessage(cause, "Twilio-Webhook konnte nicht synchronisiert werden."));
    } finally {
      setTelephonyBusy(false);
    }
  }

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

    <section className="card admin-card telephony-card">
      <div className="card-heading"><div><p className="card-kicker">Zusätzlicher Transport</p><h2>Twilio-Telefonie</h2><p>Eine vorhandene Voice-Nummer wird mit dem zentralen Realtime-Agenten dieses Unternehmens verbunden.</p></div>{telephony?.sync_status && <span className={`account-status ${telephony.sync_status === "synced" ? "active" : telephony.sync_status === "pending" ? "pending" : "inactive"}`}>{telephonyStatusLabels[telephony.sync_status]}</span>}</div>
      {telephonyError && <p className="form-error" role="status">{telephonyError}</p>}
      <div className="form-grid">
        <label className="form-field form-field-wide"><span>Twilio-Nummer im E.164-Format</span><input type="tel" aria-label="Twilio-Nummer" list="twilio-number-suggestions" placeholder="+493012345678" value={phoneNumber} disabled={telephonyBusy || company.status !== "active"} onChange={(event) => { setPhoneNumber(event.target.value); setTransferConflict(null); }} /><datalist id="twilio-number-suggestions">{twilioNumbers.filter((number) => number.voice_capable).map((number) => <option key={number.sid} value={number.phone_number}>{number.friendly_name || "Twilio"}{number.assigned_company_name ? ` · ${number.assigned_company_name}` : ""}</option>)}</datalist><small>Die Nummer wird serverseitig im zentralen Twilio-Konto geprüft. Es wird keine Nummer gekauft oder freigegeben.</small></label>
      </div>
      {transferConflict && <div className="warning-banner"><p>Diese Nummer ist bereits {transferConflict.companyName} zugeordnet.</p><button type="button" disabled={telephonyBusy} onClick={() => void transferTelephony()}>Nummer bewusst auf dieses Unternehmen übertragen</button></div>}
      <dl className="detail-list telephony-details">
        <div><dt>Aktuelle Nummer</dt><dd>{telephony?.phone_number ?? "Nicht zugeordnet"}</dd></div>
        <div><dt>Erwartete Voice URL</dt><dd><code>{telephony?.expected_voice_url ?? "Wird geladen …"}</code></dd></div>
        <div><dt>Zuletzt synchronisiert</dt><dd>{telephony?.provider_synced_at ? new Date(telephony.provider_synced_at).toLocaleString("de-DE") : "Noch nicht"}</dd></div>
        <div><dt>Hinweiscode</dt><dd>{telephony?.error_code ?? "–"}</dd></div>
      </dl>
      <div className="form-actions form-actions-end"><button type="button" disabled={telephonyBusy || company.status !== "active" || !phoneNumber.trim()} onClick={() => void saveTelephony()}>{telephonyBusy ? "Bitte warten …" : "Nummer speichern"}</button><button type="button" disabled={telephonyBusy || !telephony?.phone_number_sid} onClick={() => void removeTelephony()}>Nummer entfernen</button><button className="primary-button" type="button" disabled={telephonyBusy || !telephony?.phone_number_sid} onClick={() => void syncTelephony()}>Erneut synchronisieren</button></div>
    </section>

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
