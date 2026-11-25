import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api } from "../api/client";
import { usePlatformData } from "../api/PlatformDataProvider";
import type { CompanyDetail } from "../types/api";
import { accountErrorMessage, DataPage, industryLabels, PageHeader } from "./shared";

export function CompanyPage() {
  const { refresh } = useAuth();
  const { retry } = usePlatformData();
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [savedName, setSavedName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.ownCompany().then((profile) => {
      setCompany(profile);
      setSavedName(profile.name);
    }).catch((cause) => setError(accountErrorMessage(cause, "Unternehmensprofil konnte nicht geladen werden.")));
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!company || saving) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const renamed = company.name.trim() !== savedName;
      const saved = await api.updateOwnCompany({
        ...(renamed && { name: company.name.trim() }),
        contact_name: company.contact_name,
        contact_email: company.contact_email,
        contact_phone: company.contact_phone,
        timezone: company.timezone,
      });
      setCompany(saved);
      setSavedName(saved.name);
      setMessage("Unternehmensprofil gespeichert.");
      retry();
      if (renamed) {
        await refresh().catch(() => setError("Der neue Name erscheint in der Navigation nach dem Neuladen."));
      }
    } catch (cause) {
      setError(accountErrorMessage(cause, "Unternehmensprofil konnte nicht gespeichert werden."));
    } finally {
      setSaving(false);
    }
  }

  return <DataPage>{({ tenant }) => {
    const location = tenant.primary_location;
    return <div className="page">
      <PageHeader eyebrow="Unternehmensprofil" title="Unternehmen" description="Verwalten Sie den Namen, die Kontaktdaten und die Zeitzone Ihres Unternehmens." />
      {error && <p className="form-error" role="alert">{error}</p>}
      {message && <p className="form-success" role="status">{message}</p>}
      <div className="grid company-grid">
        <section className="card detail-card">
          <h2>Name und Kontakt</h2>
          {company ? <form className="settings-form" onSubmit={save}>
            <label>Unternehmensname oder eigener Name
              <input required maxLength={200} value={company.name} onChange={(event) => setCompany({ ...company, name: event.target.value })} aria-describedby="company-name-hint" />
            </label>
            <small id="company-name-hint">Als Einzelunternehmen können Sie Ihren Vor- und Nachnamen eintragen. Wenn Sie den Namen ändern, übernimmt ihn der Telefonassistent für neue Gespräche.</small>
            <label>Kontaktname<input value={company.contact_name ?? ""} onChange={(event) => setCompany({ ...company, contact_name: event.target.value || null })} /></label>
            <label>Kontakt-E-Mail<input type="email" value={company.contact_email ?? ""} onChange={(event) => setCompany({ ...company, contact_email: event.target.value || null })} /></label>
            <label>Telefon<input value={company.contact_phone ?? ""} onChange={(event) => setCompany({ ...company, contact_phone: event.target.value || null })} /></label>
            <label>Zeitzone<input required value={company.timezone} onChange={(event) => setCompany({ ...company, timezone: event.target.value })} /></label>
            <button className="primary-button" disabled={saving || !company.name.trim()}>{saving ? "Wird gespeichert …" : "Änderungen speichern"}</button>
          </form> : !error && <p>Lade Profil …</p>}
        </section>
        <section className="card detail-card"><h2>Plattformstammdaten</h2><dl>
          <div><dt>Rechtlicher Name</dt><dd>{company?.legal_name ?? "Nicht hinterlegt"}</dd></div>
          <div><dt>Branche</dt><dd>{industryLabels[tenant.industry] ?? tenant.industry}</dd></div>
          <div><dt>Status</dt><dd><span className="status-badge ready"><span className="status-dot" />{tenant.status === "active" ? "Aktiv" : tenant.status}</span></dd></div>
        </dl></section>
        <section className="card detail-card"><h2>Primärer Standort</h2><dl>
          <div><dt>Bezeichnung</dt><dd>{location?.name ?? "Nicht hinterlegt"}</dd></div>
          <div><dt>Adresse</dt><dd>{location?.street || location?.city ? [location.street, location.postal_code, location.city].filter(Boolean).join(", ") : "Noch nicht hinterlegt"}</dd></div>
          <div><dt>Land</dt><dd>{location?.country_code ?? "—"}</dd></div>
          <div><dt>Zeitzone</dt><dd>{location?.timezone ?? tenant.timezone}</dd></div>
        </dl></section>
        <section className="card detail-card assistant-card">
          <div className="assistant-avatar">{tenant.settings.assistant_name.charAt(0)}</div>
          <div><span className="eyebrow">Digitaler Assistent</span><h2>{tenant.settings.assistant_name}</h2>
            <p>Standardsprache {tenant.settings.default_language.toUpperCase()}</p>
          </div>
          <blockquote><small>Begrüßung im Unternehmensprofil</small><p>„{tenant.settings.welcome_message}“</p></blockquote>
          <Link to="/ki-konfigurieren">Betriebsmodus und Begrüßung einrichten</Link>
        </section>
      </div>
    </div>;
  }}</DataPage>;
}
