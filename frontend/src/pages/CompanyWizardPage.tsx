import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { CompanyCreate } from "../types/api";
import { accountErrorMessage, PageHeader } from "./shared";

const initial: CompanyCreate = {
  slug: "",
  name: "",
  legal_name: null,
  industry: "services",
  timezone: "Europe/Berlin",
  contact_name: null,
  contact_email: null,
  contact_phone: null,
  status: "trial",
  is_demo: false,
  first_admin: {
    username: "",
    display_name: "",
    email: null,
    delivery: "temporary_password",
    temporary_password: "",
  },
};

export function CompanyWizardPage() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (step === 1) {
      setStep(2);
      return;
    }
    setSaving(true);
    try {
      const company = await api.createCompany(form);
      navigate(`/plattform/unternehmen/${company.id}`, { replace: true });
    } catch (cause) {
      setError(accountErrorMessage(cause, "Unternehmen konnte nicht angelegt werden."));
    } finally {
      setSaving(false);
    }
  }

  return <div className="page platform-page">
    <PageHeader eyebrow="Unternehmenswizard" title="Unternehmen anlegen" description="Unternehmensdaten und ersten Administrator sicher in einem Vorgang anlegen." />
    <ol className="wizard-progress" aria-label="Fortschritt">
      <li className={step >= 1 ? "active" : ""}><span>1</span><div><strong>Unternehmen</strong><small>Stammdaten und Kontakt</small></div></li>
      <li className={step >= 2 ? "active" : ""}><span>2</span><div><strong>Administrator</strong><small>Direktes Startkonto</small></div></li>
    </ol>
    {error && <p className="form-error" role="alert">{error}</p>}

    <form className="card admin-card admin-form wizard-card" onSubmit={submit}>
      <div className="card-heading"><div><p className="card-kicker">Schritt {step} von 2</p><h2>{step === 1 ? "Unternehmensdaten" : "Erster Unternehmensadministrator"}</h2><p>{step === 1 ? "Pflichtangaben und optionale Kontaktdaten sind klar getrennt." : "Der Administrator wird direkt angelegt und muss sein Startpasswort beim ersten Login ändern."}</p></div></div>
      {step === 1 ? <>
        <fieldset className="form-section">
          <legend>Unternehmen</legend>
          <div className="form-grid">
            <label className="form-field"><span>Anzeigename</span><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
            <label className="form-field"><span>Rechtlicher Name <small>optional</small></span><input value={form.legal_name ?? ""} onChange={(event) => setForm({ ...form, legal_name: event.target.value || null })} /></label>
            <label className="form-field"><span>Slug</span><input required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" title="Nur Kleinbuchstaben, Zahlen und Bindestriche" value={form.slug} onChange={(event) => setForm({ ...form, slug: event.target.value.toLowerCase() })} /><small>Zum Beispiel „salon-haarkunst“</small></label>
            <label className="form-field"><span>Branche</span><input required value={form.industry} onChange={(event) => setForm({ ...form, industry: event.target.value })} /></label>
          </div>
        </fieldset>
        <fieldset className="form-section">
          <legend>Ansprechpartner</legend>
          <div className="form-grid">
            <label className="form-field"><span>Kontaktname <small>optional</small></span><input value={form.contact_name ?? ""} onChange={(event) => setForm({ ...form, contact_name: event.target.value || null })} /></label>
            <label className="form-field"><span>Kontakt-E-Mail <small>optional</small></span><input type="email" value={form.contact_email ?? ""} onChange={(event) => setForm({ ...form, contact_email: event.target.value || null })} /></label>
            <label className="form-field"><span>Kontakttelefon <small>optional</small></span><input type="tel" value={form.contact_phone ?? ""} onChange={(event) => setForm({ ...form, contact_phone: event.target.value || null })} /></label>
          </div>
        </fieldset>
        <div className="form-actions form-actions-end"><button className="primary-button">Weiter</button></div>
      </> : <>
        <fieldset className="form-section">
          <legend>Startkonto</legend>
          <div className="form-grid">
            <label className="form-field"><span>Name</span><input required value={form.first_admin.display_name} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, display_name: event.target.value } })} /></label>
            <label className="form-field"><span>Benutzername</span><input required autoComplete="off" value={form.first_admin.username} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, username: event.target.value } })} /></label>
            <label className="form-field"><span>Startpasswort</span><input required minLength={15} maxLength={128} type="password" autoComplete="new-password" value={form.first_admin.temporary_password ?? ""} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, temporary_password: event.target.value } })} /><small>Mindestens 15 Zeichen</small></label>
            <label className="form-field"><span>E-Mail <small>optional</small></span><input type="email" value={form.first_admin.email ?? ""} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, email: event.target.value || null } })} /></label>
          </div>
        </fieldset>
        <div className="form-actions form-actions-between"><button type="button" onClick={() => setStep(1)}>Zurück</button><button className="primary-button" disabled={saving}>{saving ? "Wird angelegt …" : "Unternehmen und Startkonto anlegen"}</button></div>
      </>}
    </form>
  </div>;
}
