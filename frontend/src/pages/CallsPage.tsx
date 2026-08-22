import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { EmptyState, ErrorState } from "../components/AsyncState";
import type { CallCategory, CallListItem, CallPriority } from "../types/api";
import { PageHeader } from "./shared";

export const callCategoryLabels: Record<CallCategory, string> = {
  appointment: "Termin",
  callback: "Rückruf",
  price_request: "Preisanfrage",
  general_question: "Allgemeine Frage",
  complaint: "Beschwerde",
  new_customer: "Neukunde",
  existing_customer: "Bestandskunde",
  other: "Sonstiges",
};

export const callPriorityLabels: Record<CallPriority, string> = {
  normal: "Normal",
  important: "Wichtig",
  urgent: "Dringend",
};

export function formatCallDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${remainder.toString().padStart(2, "0")} Min.`;
}

function analysisText(call: CallListItem): string {
  if (call.analysis_status === "failed") return "Analyse fehlgeschlagen";
  if (call.analysis_status !== "completed") return "Analyse ausstehend";
  return call.result || "Kein Ergebnis festgehalten";
}

export function CallsPage() {
  const [calls, setCalls] = useState<CallListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    return api.calls(signal)
      .then(setCalls)
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof ApiError ? cause.message : "Anrufe konnten nicht geladen werden.");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return <div className="page calls-page">
    <PageHeader eyebrow="Kommunikation" title="Anrufe" description="Alle abgeschlossenen Telefonate mit Ergebnis, Handlungsbedarf und vollständigem Gesprächsverlauf." />
    {loading && <div className="center-state card"><div className="spinner" /><p>Anrufe werden geladen …</p></div>}
    {!loading && error && <ErrorState message={error} retry={() => void load()} />}
    {!loading && !error && calls.length === 0 && <section className="card"><EmptyState icon="☎" title="Noch keine Anrufe">Abgeschlossene Telefonate erscheinen hier automatisch.</EmptyState></section>}
    {!loading && !error && calls.length > 0 && <>
      <section className="card table-card call-history-table desktop-table">
        <div className="table-heading"><div><p className="card-kicker">Anruf-Inbox</p><h2>Neueste Anrufe</h2></div><span>{calls.length} Anrufe</span></div>
        <table><thead><tr><th>Zeitpunkt</th><th>Anrufer</th><th>Anliegen</th><th>Ergebnis</th><th>Priorität</th><th>Dauer</th><th>Status</th></tr></thead>
          <tbody>{calls.map((call) => <tr key={call.id}>
            <td><Link to={`/anrufe/${call.id}`}>{new Date(call.started_at).toLocaleString("de-DE")}</Link></td>
            <td><Link to={`/anrufe/${call.id}`}><strong>{call.caller_name || "Unbekannt"}</strong><small>{call.caller_phone || "Nummer unbekannt"}</small></Link></td>
            <td>{call.category ? callCategoryLabels[call.category] : "Noch nicht ausgewertet"}</td>
            <td>{analysisText(call)}</td>
            <td><span className={`call-priority ${call.priority}`}>{callPriorityLabels[call.priority]}</span></td>
            <td>{formatCallDuration(call.duration_seconds)}</td>
            <td><span className={`account-status ${call.status === "done" ? "active" : "pending"}`}>{call.status === "done" ? "Erledigt" : "Offen"}</span></td>
          </tr>)}</tbody>
        </table>
      </section>
      <div className="mobile-list call-mobile-list">{calls.map((call) => <Link className="card call-list-card" to={`/anrufe/${call.id}`} key={call.id}>
        <div><strong>{call.caller_name || "Unbekannt"}</strong><span className={`account-status ${call.status === "done" ? "active" : "pending"}`}>{call.status === "done" ? "Erledigt" : "Offen"}</span></div>
        <small>{call.caller_phone || "Nummer unbekannt"} · {new Date(call.started_at).toLocaleString("de-DE")}</small>
        <p>{call.category ? callCategoryLabels[call.category] : "Noch nicht ausgewertet"}</p>
        <span>{analysisText(call)}</span>
      </Link>)}</div>
    </>}
  </div>;
}
