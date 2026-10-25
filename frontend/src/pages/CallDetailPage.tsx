import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { useAuth } from "../api/AuthProvider";
import { ErrorState } from "../components/AsyncState";
import type { CallDetail } from "../types/api";
import { callCategoryLabels, callModeLabel, callPriorityLabels, callStatusLabel, formatCallDuration } from "./CallsPage";
import { PageHeader } from "./shared";

export function CallDetailPage() {
  const { session } = useAuth();
  const { callId = "" } = useParams();
  const [call, setCall] = useState<CallDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true); setError(null);
    return api.call(callId, signal).then(setCall).catch((cause) => {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof ApiError ? cause.message : "Der Anruf konnte nicht geladen werden.");
    }).finally(() => setLoading(false));
  }, [callId]);

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);

  async function toggleStatus() {
    if (!call) return;
    setSaving(true); setError(null);
    try { setCall(await api.updateCallStatus(call.id, call.status === "open" ? "done" : "open")); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Der Status konnte nicht gespeichert werden."); }
    finally { setSaving(false); }
  }

  async function retryAnalysis() {
    if (!call) return;
    setSaving(true); setError(null);
    try { await api.retryCallAnalysis(call.id); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Die Analyse konnte nicht wiederholt werden."); }
    finally { setSaving(false); }
  }

  async function deleteContent() {
    if (!call) return;
    setSaving(true); setError(null);
    try { await api.deleteCallContent(call.id); setConfirmDelete(false); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Inhalte konnten nicht gelöscht werden."); }
    finally { setSaving(false); }
  }

  if (loading) return <div className="page"><div className="center-state card"><div className="spinner" /><p>Anruf wird geladen …</p></div></div>;
  if (!call) return <div className="page"><ErrorState message={error ?? "Anruf nicht gefunden."} retry={() => void load()} /></div>;

  const action = <div className="call-detail-actions"><Link className="button secondary" to="/anrufe">← Zurück</Link><button className="button primary" disabled={saving} onClick={() => void toggleStatus()}>{call.status === "open" ? "Als erledigt markieren" : "Wieder öffnen"}</button></div>;
  return <div className="page call-detail-page">
    <PageHeader eyebrow="Anrufdetail" title={call.caller_name || "Unbekannter Anrufer"} description={`${call.caller_phone || "Nummer unbekannt"} · ${new Date(call.started_at).toLocaleString("de-DE")}`} action={action} />
    {error && <div className="notice error" role="alert"><span>!</span><p>{error}</p></div>}
    <section className="grid call-detail-grid">
      <article className="card detail-card"><h2>Metadaten</h2><dl>
        <div><dt>Anrufer</dt><dd>{call.caller_name || "Unbekannt"}</dd></div>
        <div><dt>Telefonnummer</dt><dd>{call.caller_phone || "—"}</dd></div>
        <div><dt>Datum</dt><dd>{new Date(call.started_at).toLocaleDateString("de-DE")}</dd></div>
        <div><dt>Uhrzeit</dt><dd>{new Date(call.started_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</dd></div>
        <div><dt>Dauer</dt><dd>{formatCallDuration(call.duration_seconds)}</dd></div>
        <div><dt>Modus</dt><dd>{callModeLabel(call.operating_mode)}</dd></div>
        <div><dt>Sprachmodell</dt><dd>{call.voice_model || "Nicht erfasst"}</dd></div>
        <div><dt>Telefonstatus</dt><dd>{callStatusLabel(call.call_status)}</dd></div>
        <div><dt>Dokumentation</dt><dd>{call.documentation_status === "incomplete" ? "Unvollständig" : call.documentation_status === "deleted" ? "Inhalte gelöscht" : call.documentation_status === "recording" ? "Gespräch läuft" : call.documentation_status === "complete" ? "Abgeschlossen" : "Bestandsdaten"}</dd></div>
        <div><dt>Rückruf</dt><dd>{call.callback_required === true ? "Gewünscht" : call.callback_required === false ? "Nicht gewünscht" : "Unbekannt"}</dd></div>
        {(call.error_code || call.analysis_error_code) && <div><dt>Verarbeitungsfehler</dt><dd>{call.error_code || call.analysis_error_code}</dd></div>}
        <div><dt>Status</dt><dd><span className={`account-status ${call.status === "done" ? "active" : "pending"}`}>{call.status === "done" ? "Erledigt" : "Offen"}</span></dd></div>
      </dl></article>
      <article className="card call-analysis-card"><div className="section-heading"><div><p className="card-kicker">KI-Auswertung</p><h2>Gesprächszusammenfassung</h2></div><span className={`call-priority ${call.priority}`}>{callPriorityLabels[call.priority]}</span></div>
        {call.analysis_status === "failed" && <div className="notice error" role="status"><span>!</span><p>Die Auswertung ist fehlgeschlagen. Das Transkript wurde trotzdem gespeichert.</p><button className="button secondary" disabled={saving} onClick={() => void retryAnalysis()}>Erneut analysieren</button></div>}
        {call.content_deleted_at && <p role="status">Die Gesprächsinhalte wurden gelöscht.</p>}
        {!call.content_deleted_at && call.analysis_status !== "completed" && call.analysis_status !== "failed" && <div className="notice warning" role="status"><span>!</span><p>Die Auswertung wird vorbereitet.</p></div>}
        <dl className="analysis-list">
          <div><dt>Zusammenfassung</dt><dd>{call.summary || "Noch keine Zusammenfassung vorhanden."}</dd></div>
          <div><dt>Anliegen</dt><dd>{call.category ? callCategoryLabels[call.category] : "Noch nicht ausgewertet"}</dd></div>
          <div><dt>Hauptanliegen</dt><dd>{call.analysis_details?.main_concern || "Nicht erfasst"}</dd></div>
          <div><dt>Genannte Kontaktdaten</dt><dd>{[call.analysis_details?.contact_phone, call.analysis_details?.contact_email].filter(Boolean).join(" · ") || "Nicht erfasst"}</dd></div>
          <div><dt>Terminwunsch</dt><dd>{call.analysis_details?.appointment_request || "Nicht erfasst"}</dd></div>
          <div><dt>Ergebnis</dt><dd>{call.result || "Noch kein Ergebnis vorhanden."}</dd></div>
          <div className={call.action_required ? "action-required" : ""}><dt>Handlungsbedarf</dt><dd>{call.action_required ? "Ja – Bearbeitung erforderlich" : "Nein"}</dd></div>
          <div><dt>Aufgaben</dt><dd>{call.action_items.length ? <ul>{call.action_items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : "Keine Aufgaben"}</dd></div>
          <div><dt>Priorität</dt><dd>{callPriorityLabels[call.priority]}</dd></div>
        </dl>
      </article>
    </section>
    <section className="card call-transcript-card"><div className="section-heading"><div><p className="card-kicker">Gesprächsverlauf</p><h2>Vollständiges Transkript</h2></div><span>{call.transcript.length} Beiträge</span></div>
      {call.transcript.length === 0 ? <p className="empty-state">Für diesen Anruf ist kein Transkript verfügbar.</p> : <div className="call-transcript">{call.transcript.map((turn, index) => <article className={`call-transcript-turn ${turn.speaker}`} key={`${index}-${turn.speaker}`}><strong>{turn.speaker === "agent" ? "Agent" : "Anrufer"}</strong>{turn.start_ms != null && <small>{(turn.start_ms / 1000).toFixed(1)} s</small>}{turn.state === "provisional" && <small>Vorläufig · nicht abschließend bestätigt</small>}{turn.state === "interrupted" && <small>Unterbrochen · möglicherweise nicht vollständig gehört</small>}<p>{turn.text}</p></article>)}</div>}
    </section>
    <section className="card detail-card"><h2>Ausgeführte Aktionen und Termine</h2>
      <p>Diese Angaben stammen aus den tatsächlichen Werkzeug- und Buchungsdatensätzen. Ein Terminwunsch allein bestätigt keine Buchung.</p>
      {(call.actions ?? []).length ? <ul>{call.actions?.map((item, index) => <li key={index}>{item.tool_name}: {item.success === true ? "Erfolgreich" : item.success === false ? "Fehlgeschlagen" : item.status}{item.error_code ? ` · ${item.error_code}` : ""}</li>)}</ul> : <p>Keine Aktionen protokolliert.</p>}
      {(call.appointments ?? []).length ? <ul>{call.appointments?.map((item) => <li key={item.id}><Link to="/termine">{item.service_name}</Link> · {new Date(item.start_at).toLocaleString("de-DE")} · {item.status === "confirmed" && item.external_event_id ? "Buchung bestätigt" : `Buchungsstatus: ${item.status}`}</li>)}</ul> : <p>Keine Terminbuchung verknüpft.</p>}
    </section>
    {session?.permissions.includes("company.manage") && call.ended_at && !call.content_deleted_at && <section className="card detail-card"><h2>Gesprächsinhalte löschen</h2><p>Entfernt Transkript, Auswertung und Anruferkontaktdaten. Separat gespeicherte Termine und technische Anrufmetadaten bleiben erhalten.</p>{confirmDelete ? <div className="call-detail-actions"><button className="button secondary" disabled={saving} onClick={() => setConfirmDelete(false)}>Abbrechen</button><button className="button primary" disabled={saving} onClick={() => void deleteContent()}>Inhalte endgültig löschen</button></div> : <button className="button secondary" onClick={() => setConfirmDelete(true)}>Gesprächsinhalte löschen …</button>}</section>}
  </div>;
}
