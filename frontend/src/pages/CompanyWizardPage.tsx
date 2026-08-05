import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { CompanyCreate } from "../types/api";
import { PageHeader } from "./shared";

const initial: CompanyCreate = {
  slug: "", name: "", legal_name: null, industry: "services", timezone: "Europe/Berlin",
  contact_name: null, contact_email: null, contact_phone: null, status: "trial", is_demo: false,
  first_admin: { username: "", display_name: "", email: "", delivery: "invitation" },
};

export function CompanyWizardPage() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();
  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    try { const company = await api.createCompany(form); navigate(`/plattform/unternehmen/${company.id}`, { replace: true }); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Unternehmen konnte nicht angelegt werden."); }
    finally { setSaving(false); }
  }
  return <div className="page"><PageHeader eyebrow="Unternehmenswizard" title="Unternehmen anlegen" description={`Schritt ${step} von 2`} />
    {error && <p className="form-error" role="alert">{error}</p>}
    <form className="card settings-form wizard-card" onSubmit={submit}>
      {step === 1 ? <>
        <h2>Unternehmensdaten</h2>
        <label>Anzeigename<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label>Rechtlicher Name<input value={form.legal_name ?? ""} onChange={(event) => setForm({ ...form, legal_name: event.target.value || null })} /></label>
        <label>Slug<input required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={form.slug} onChange={(event) => setForm({ ...form, slug: event.target.value.toLowerCase() })} /></label>
        <label>Branche<input required value={form.industry} onChange={(event) => setForm({ ...form, industry: event.target.value })} /></label>
        <label>Kontaktname<input value={form.contact_name ?? ""} onChange={(event) => setForm({ ...form, contact_name: event.target.value || null })} /></label>
        <label>Kontakt-E-Mail<input type="email" value={form.contact_email ?? ""} onChange={(event) => setForm({ ...form, contact_email: event.target.value || null })} /></label>
        <div className="form-actions"><button className="primary-button" type="button" onClick={() => setStep(2)}>Weiter</button></div>
      </> : <>
        <h2>Erster Unternehmensadministrator</h2>
        <p>Die Einladung wird erst nach der atomaren Anlage des Unternehmens gültig.</p>
        <label>Name<input required value={form.first_admin.display_name} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, display_name: event.target.value } })} /></label>
        <label>Benutzername<input required value={form.first_admin.username} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, username: event.target.value } })} /></label>
        <label>E-Mail<input required type="email" value={form.first_admin.email} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, email: event.target.value } })} /></label>
        <label>Bereitstellung<select value={form.first_admin.delivery} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, delivery: event.target.value as "invitation" | "temporary_password", temporary_password: null } })}><option value="invitation">Sichere E-Mail-Einladung</option><option value="temporary_password">Temporäres Startpasswort</option></select></label>
        {form.first_admin.delivery === "temporary_password" && <label>Temporäres Startpasswort<input required minLength={15} type="password" autoComplete="new-password" value={form.first_admin.temporary_password ?? ""} onChange={(event) => setForm({ ...form, first_admin: { ...form.first_admin, temporary_password: event.target.value } })} /></label>}
        <div className="form-actions"><button type="button" onClick={() => setStep(1)}>Zurück</button><button className="primary-button" disabled={saving}>{saving ? "Wird angelegt …" : form.first_admin.delivery === "invitation" ? "Unternehmen und Einladung anlegen" : "Unternehmen mit Startkonto anlegen"}</button></div>
      </>}
    </form>
  </div>;
}
