import { useEffect, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api } from "../api/client";
import type { PlatformAdmin, PlatformAdminCreate } from "../types/api";
import { accountErrorMessage, PageHeader } from "./shared";

const emptyAdmin: PlatformAdminCreate = {
  username: "",
  display_name: "",
  email: null,
  password: "",
  current_password: "",
};

function adminStatus(admin: PlatformAdmin) {
  if (!admin.is_active) return "Deaktiviert";
  return admin.must_change_password ? "Passwortwechsel erforderlich" : "Aktiv";
}

export function PlatformAdminsPage() {
  const { session } = useAuth();
  const [admins, setAdmins] = useState<PlatformAdmin[]>([]);
  const [form, setForm] = useState(emptyAdmin);
  const [pendingAdmin, setPendingAdmin] = useState<PlatformAdmin | null>(null);
  const [reauthPassword, setReauthPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    try {
      setAdmins(await api.platformAdmins());
    } catch (cause) {
      setError(accountErrorMessage(cause, "Administratoren konnten nicht geladen werden."));
    }
  }

  useEffect(() => { void load(); }, []);
  if (session?.user.platform_role !== "owner") return <Navigate to="/plattform" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setSaving(true);
    try {
      const created = await api.createPlatformAdmin(form);
      setAdmins((current) => [...current, created].sort((left, right) => left.display_name.localeCompare(right.display_name, "de")));
      setForm(emptyAdmin);
      setMessage("Plattformadministrator wurde erstellt.");
    } catch (cause) {
      setError(accountErrorMessage(cause, "Plattformadministrator konnte nicht erstellt werden."));
    } finally {
      setSaving(false);
    }
  }

  async function confirmToggle(event: FormEvent) {
    event.preventDefault();
    if (!pendingAdmin) return;
    setError(null);
    try {
      await api.updatePlatformAdmin(pendingAdmin.id, {
        display_name: pendingAdmin.display_name,
        email: pendingAdmin.email,
        is_active: !pendingAdmin.is_active,
        current_password: reauthPassword,
      });
      setPendingAdmin(null);
      setReauthPassword("");
      setMessage("Plattformkonto wurde geändert.");
      await load();
    } catch (cause) {
      setError(accountErrorMessage(cause, "Administrator konnte nicht geändert werden."));
    }
  }

  return <div className="page platform-page">
    <PageHeader eyebrow="Owner-Bereich" title="Plattformadministratoren" description="Weitere Plattformadmins werden lokal angelegt. Anlage und Statusänderungen verlangen immer das aktuelle Owner-Passwort." />
    {error && <p className="form-error" role="alert">{error}</p>}
    {message && <p className="form-success" role="status">{message}</p>}

    <section className="card admin-card form-card-narrow">
      <div className="card-heading"><div><p className="card-kicker">Geschützter Vorgang</p><h2>Plattformadministrator erstellen</h2><p>Das Startpasswort muss beim ersten Login geändert werden. Eine E-Mail-Adresse ist nicht erforderlich.</p></div></div>
      <form className="admin-form" onSubmit={submit}>
        <div className="form-grid">
          <label className="form-field"><span>Name</span><input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
          <label className="form-field"><span>Benutzername</span><input required autoComplete="off" value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></label>
          <label className="form-field"><span>E-Mail <small>optional</small></span><input type="email" value={form.email ?? ""} onChange={(event) => setForm({ ...form, email: event.target.value || null })} /></label>
          <label className="form-field"><span>Startpasswort</span><input required minLength={15} maxLength={128} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /><small>Mindestens 15 Zeichen</small></label>
          <label className="form-field form-field-wide"><span>Owner-Passwort zur Reauthentifizierung</span><input required type="password" autoComplete="current-password" value={form.current_password} onChange={(event) => setForm({ ...form, current_password: event.target.value })} /></label>
        </div>
        <div className="form-actions form-actions-end"><button className="primary-button" disabled={saving}>{saving ? "Wird erstellt …" : "Plattformadmin erstellen"}</button></div>
      </form>
    </section>

    <section className="card table-card management-table">
      <div className="table-heading"><div><p className="card-kicker">Plattformzugriff</p><h2>Bestehende Plattformkonten</h2></div><span>{admins.length} Konten</span></div>
      <table><thead><tr><th>Name</th><th>Benutzername</th><th>Rolle</th><th>Status</th><th>Letzter Login</th><th>Aktionen</th></tr></thead><tbody>{admins.map((admin) => <tr key={admin.id}>
        <td><strong>{admin.display_name}</strong><small>{admin.email ?? "Keine E-Mail"}</small></td>
        <td>{admin.username}</td>
        <td>{admin.platform_role === "owner" ? "Plattforminhaber" : "Plattformadmin"}</td>
        <td><span className={`account-status ${!admin.is_active ? "inactive" : admin.must_change_password ? "pending" : "active"}`}>{adminStatus(admin)}</span></td>
        <td>{admin.last_login_at ? new Date(admin.last_login_at).toLocaleString("de-DE") : "Noch nie"}</td>
        <td>{admin.platform_role !== "owner" && <button type="button" onClick={() => setPendingAdmin(admin)}>{admin.is_active ? "Deaktivieren" : "Aktivieren"}</button>}</td>
      </tr>)}</tbody></table>
    </section>

    {pendingAdmin && <div className="dialog-backdrop"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-confirm-title"><h2 id="admin-confirm-title">Plattformadmin {pendingAdmin.is_active ? "deaktivieren" : "aktivieren"}</h2><p>Die Änderung widerruft aktive Sitzungen von {pendingAdmin.display_name}.</p><form className="admin-form" onSubmit={confirmToggle}><label className="form-field"><span>Aktuelles Owner-Passwort</span><input autoFocus required type="password" autoComplete="current-password" value={reauthPassword} onChange={(event) => setReauthPassword(event.target.value)} /></label><div className="form-actions form-actions-end"><button type="button" onClick={() => { setPendingAdmin(null); setReauthPassword(""); }}>Abbrechen</button><button className="primary-button">Bestätigen</button></div></form></section></div>}
  </div>;
}
