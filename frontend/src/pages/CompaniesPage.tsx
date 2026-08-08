import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { CompanyStatus, CompanySummary } from "../types/api";
import { PageHeader } from "./shared";

const labels: Record<CompanyStatus, string> = { trial: "Testphase", active: "Aktiv", suspended: "Gesperrt", archived: "Archiviert" };

export function CompaniesPage() {
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const timeout = window.setTimeout(() => {
      api.companies(search, status).then(setCompanies).catch((cause) => setError(cause instanceof ApiError ? cause.message : "Unternehmen konnten nicht geladen werden."));
    }, 150);
    return () => window.clearTimeout(timeout);
  }, [search, status]);
  return <div className="page platform-page">
    <PageHeader eyebrow="Plattform" title="Unternehmen" description="Unternehmen durchsuchen, Status prüfen und sicher in den gewünschten Kontext wechseln." action={<Link className="primary-button" to="/plattform/unternehmen/neu">Unternehmen anlegen</Link>} />
    <div className="toolbar"><input aria-label="Unternehmen suchen" placeholder="Name oder Slug durchsuchen" value={search} onChange={(event) => setSearch(event.target.value)} /><select aria-label="Status filtern" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Alle Status</option><option value="trial">Testphase</option><option value="active">Aktiv</option><option value="suspended">Gesperrt</option><option value="archived">Archiviert</option></select></div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <section className="card table-card management-table"><div className="table-heading"><div><p className="card-kicker">Verzeichnis</p><h2>Unternehmen</h2></div><span>{companies.length} Einträge</span></div><table><thead><tr><th>Unternehmen</th><th>Status</th><th>Benutzer</th><th>Onboarding</th><th>Aktionen</th></tr></thead><tbody>{companies.map((company) => <tr key={company.id}><td><strong>{company.name}</strong><small>{company.slug}</small></td><td><span className={`status-pill ${company.status}`}>{labels[company.status]}</span></td><td>{company.active_user_count}</td><td>{company.onboarding_complete ? "Vollständig" : "Primäradmin ausstehend"}</td><td><Link className="table-link" to={`/plattform/unternehmen/${company.id}`}>Öffnen</Link></td></tr>)}</tbody></table>{companies.length === 0 && <p className="empty-state">Keine Unternehmen gefunden.</p>}</section>
  </div>;
}
