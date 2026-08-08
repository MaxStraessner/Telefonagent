import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { PlatformDashboard } from "../types/api";
import { PageHeader } from "./shared";

export function PlatformHomePage() {
  const [data, setData] = useState<PlatformDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.platformDashboard().then(setData).catch((cause) => setError(cause instanceof ApiError ? cause.message : "Dashboard konnte nicht geladen werden.")); }, []);
  return <div className="page platform-page">
    <PageHeader eyebrow="Plattform" title="Dashboard" description="Unternehmen, Onboarding und Zugriffsstatus auf einen Blick." action={<Link className="primary-button" to="/plattform/unternehmen/neu">Unternehmen anlegen</Link>} />
    {error && <p className="form-error" role="alert">{error}</p>}
    {!data ? <p>Lade Kennzahlen …</p> : <div className="stats-grid">
      <article className="stat-card"><span>Unternehmen</span><strong>{data.companies_total}</strong></article>
      <article className="stat-card"><span>Aktiv</span><strong>{data.companies_active}</strong></article>
      <article className="stat-card"><span>Testphase</span><strong>{data.companies_trial}</strong></article>
      <article className="stat-card"><span>Gesperrt</span><strong>{data.companies_suspended + data.companies_archived}</strong></article>
      <article className="stat-card"><span>Aktive Benutzer</span><strong>{data.active_company_users}</strong></article>
      <article className="stat-card"><span>Offene Einladungen</span><strong>{data.pending_invitations}</strong></article>
    </div>}
    <div className="page-actions"><Link className="table-link" to="/plattform/unternehmen">Alle Unternehmen anzeigen</Link></div>
  </div>;
}
