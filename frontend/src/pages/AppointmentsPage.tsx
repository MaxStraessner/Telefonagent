import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CalendarAgenda, CalendarEntry } from "../types/api";
import { PageHeader } from "./shared";

function startOfWeek(value: Date) {
  const result = new Date(value);
  const weekday = (result.getDay() + 6) % 7;
  result.setDate(result.getDate() - weekday);
  result.setHours(0, 0, 0, 0);
  return result;
}

function addDays(value: Date, days: number) {
  const result = new Date(value); result.setDate(result.getDate() + days); return result;
}

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function AppointmentsPage() {
  const [week, setWeek] = useState(() => startOfWeek(new Date()));
  const [agenda, setAgenda] = useState<CalendarAgenda | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const weekEnd = useMemo(() => addDays(week, 7), [week]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    api.calendarAgenda(week.toISOString(), weekEnd.toISOString(), controller.signal)
      .then(setAgenda)
      .catch((reason) => setError(reason instanceof ApiError ? reason.message : "Termine konnten nicht geladen werden."))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [week, weekEnd]);

  const days = Array.from({ length: 7 }, (_, index) => addDays(week, index));
  return <div className="page appointments-page">
    <PageHeader eyebrow="Planung" title="Termine" description="Plattformbuchungen und weitere Ereignisse der ausgewählten Kalender – ohne doppelte Einträge." />
    <section className="card calendar-section">
      <div className="section-heading"><div><h2>Wochenansicht</h2><p>{week.toLocaleDateString("de-DE")} – {addDays(weekEnd, -1).toLocaleDateString("de-DE")}</p></div><div className="section-actions"><button className="button secondary" onClick={() => setWeek(addDays(week, -7))}>← Vorherige</button><button className="button secondary" onClick={() => setWeek(startOfWeek(new Date()))}>Heute</button><button className="button secondary" onClick={() => setWeek(addDays(week, 7))}>Nächste →</button></div></div>
      {loading && <div className="center-state"><div className="spinner" /><p>Termine werden geladen …</p></div>}
      {error && <div className="notice error" role="alert"><span>!</span><p>{error}</p></div>}
      {!loading && !error && agenda && !agenda.calendar_connected && <div className="notice warning" role="status"><span>!</span><p>Es ist kein Kalender für die Verfügbarkeitsprüfung ausgewählt. Neue Termine können erst nach der Kalenderauswahl gebucht werden.</p></div>}
      {!loading && !error && agenda && <div className="week-grid">{days.map((day) => <section key={day.toISOString()} className={sameDay(day, new Date()) ? "today" : ""}><header><strong>{day.toLocaleDateString("de-DE", { weekday: "short" })}</strong><span>{day.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}</span></header><div>{agenda.entries.filter((entry) => sameDay(new Date(entry.start_at), day)).map((entry) => <AppointmentCard key={entry.id} entry={entry} />)}</div></section>)}</div>}
      {!loading && !error && agenda?.calendar_connected && agenda.entries.length === 0 && <div className="calendar-empty"><strong>Keine Termine in dieser Woche</strong><p>Wechsle die Woche oder buche einen Termin über das Testgespräch.</p></div>}
    </section>
    {!loading && !error && agenda && agenda.entries.length > 0 && <section className="card calendar-section"><div className="section-heading"><div><h2>Agenda</h2><p>Alle Details der ausgewählten Woche.</p></div></div><div className="appointment-agenda">{agenda.entries.map((entry) => <AppointmentDetail key={entry.id} entry={entry} />)}</div></section>}
  </div>;
}

function AppointmentCard({ entry }: { entry: CalendarEntry }) {
  return <article className={`week-event ${entry.kind}`} title={`${entry.calendar_name} · ${entry.sync_status}`}><time>{new Date(entry.start_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</time><strong>{entry.service_name}</strong><small>{entry.kind === "platform" ? entry.customer_name : "Externer Kalendereintrag"}</small></article>;
}

function AppointmentDetail({ entry }: { entry: CalendarEntry }) {
  return <details className="appointment-detail"><summary><span><strong>{entry.service_name}</strong><small>{new Date(entry.start_at).toLocaleString("de-DE")} · {entry.duration_minutes} Minuten</small></span><span className={`status-badge ${entry.sync_status === "synced" ? "ready" : "pending"}`}>{entry.kind === "external" ? "Extern" : entry.sync_status}</span></summary><dl>
    <div><dt>Kunde</dt><dd>{entry.customer_name || "—"}</dd></div><div><dt>Terminformat</dt><dd>{entry.appointment_format}</dd></div><div><dt>Ort</dt><dd>{entry.location || "—"}</dd></div><div><dt>Status</dt><dd>{entry.status}</dd></div><div><dt>Puffer</dt><dd>{entry.buffer_before_minutes} Min. vorher · {entry.buffer_after_minutes} Min. nachher</dd></div><div><dt>Quelle</dt><dd>{entry.source}</dd></div><div><dt>Kalender</dt><dd>{entry.calendar_provider} · {entry.calendar_name}</dd></div><div><dt>Synchronisation</dt><dd>{entry.sync_status}</dd></div>{entry.created_at && <div><dt>Erstellt</dt><dd>{new Date(entry.created_at).toLocaleString("de-DE")}</dd></div>}
  </dl></details>;
}
