import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { AuditEntry } from "../types/api";
import { PageHeader } from "./shared";

export function AuditPage({ company = false }: { company?: boolean }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { (company ? api.ownCompanyAudit() : api.platformAudit()).then(setEntries).catch((cause) => setError(cause instanceof ApiError ? cause.message : "Auditprotokoll konnte nicht geladen werden.")); }, [company]);
  return <div className={`page ${company ? "" : "platform-page"}`}><PageHeader eyebrow={company ? "Unternehmen" : "Plattform"} title="Auditprotokoll" description="Sicherheitsrelevante Verwaltungsaktionen mit redigierten Metadaten." />
    {error && <p className="form-error" role="alert">{error}</p>}
    <section className="card table-card management-table"><div className="table-heading"><div><p className="card-kicker">Nachvollziehbarkeit</p><h2>Verwaltungsereignisse</h2></div><span>{entries.length} Einträge</span></div><table><thead><tr><th>Zeitpunkt</th><th>Aktion</th><th>Ziel</th><th>Ergebnis</th></tr></thead><tbody>{entries.map((entry) => <tr key={entry.id}><td>{new Date(entry.created_at).toLocaleString("de-DE")}</td><td><code>{entry.action}</code></td><td>{entry.target_type}{entry.target_id ? ` · ${entry.target_id}` : ""}</td><td><span className={`account-status ${entry.outcome === "success" ? "active" : "inactive"}`}>{entry.outcome === "success" ? "Erfolgreich" : entry.outcome}</span></td></tr>)}</tbody></table>{entries.length === 0 && <p className="empty-state">Noch keine Audit-Einträge vorhanden.</p>}</section>
  </div>;
}
