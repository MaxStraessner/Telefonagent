import { DataPage, industryLabels, PageHeader } from "./shared";

export function CompanyPage() {
  return <DataPage>{(data) => { const tenant = data.tenant; const location = tenant.primary_location; return <div className="page"><PageHeader eyebrow="Mandantenprofil" title="Unternehmen" description="Serverseitig geladene Stammdaten des aktiven Testmandanten." />
    <div className="grid company-grid"><section className="card detail-card"><h2>Unternehmensdaten</h2><dl><div><dt>Name</dt><dd>{tenant.name}</dd></div><div><dt>Branche</dt><dd>{industryLabels[tenant.industry] ?? tenant.industry}</dd></div><div><dt>Zeitzone</dt><dd>{tenant.timezone}</dd></div><div><dt>Status</dt><dd><span className="status-badge ready"><span className="status-dot" />{tenant.status === "active" ? "Aktiv" : tenant.status}</span></dd></div></dl></section>
    <section className="card detail-card"><h2>Primärer Standort</h2><dl><div><dt>Bezeichnung</dt><dd>{location?.name ?? "Nicht hinterlegt"}</dd></div><div><dt>Adresse</dt><dd>{location?.street || location?.city ? [location.street, location.postal_code, location.city].filter(Boolean).join(", ") : "Noch nicht hinterlegt"}</dd></div><div><dt>Land</dt><dd>{location?.country_code ?? "—"}</dd></div><div><dt>Zeitzone</dt><dd>{location?.timezone ?? tenant.timezone}</dd></div></dl></section>
    <section className="card detail-card assistant-card"><div className="assistant-avatar">{tenant.settings.assistant_name.charAt(0)}</div><div><span className="eyebrow">Digitaler Assistent</span><h2>{tenant.settings.assistant_name}</h2><p>Standardsprache: {tenant.settings.default_language.toUpperCase()}</p></div><blockquote>„{tenant.settings.welcome_message}“</blockquote></section></div>
    <p className="page-footnote">Die Daten sind schreibgeschützt. Eine Bearbeitungsfunktion ist noch nicht Bestandteil der Plattformbasis.</p></div>; }}</DataPage>;
}

