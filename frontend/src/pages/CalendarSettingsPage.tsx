import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { AppointmentTypeWrite, BookingConfiguration, CalendarAppointmentType, CalendarConnectionsOverview, CalendarProviderConfiguration, ExternalCalendar } from "../types/api";
import { PageHeader } from "./shared";

type Tab = "providers" | "calendars" | "availability" | "appointment-types";
const weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
const emptyAppointment: AppointmentTypeWrite = { name: "", description: "", duration_minutes: 30, buffer_before_minutes: null, buffer_after_minutes: null, location_type: "phone", location_text: "", is_active: true };

function readableError(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "Die Kalenderdaten konnten nicht verarbeitet werden.";
}

export function CalendarSettingsPage() {
  const [tab, setTab] = useState<Tab>("providers");
  const [overview, setOverview] = useState<CalendarConnectionsOverview | null>(null);
  const [configuration, setConfiguration] = useState<BookingConfiguration | null>(null);
  const [appointmentTypes, setAppointmentTypes] = useState<CalendarAppointmentType[]>([]);
  const [editingType, setEditingType] = useState<CalendarAppointmentType | null>(null);
  const [typeDraft, setTypeDraft] = useState<AppointmentTypeWrite>(emptyAppointment);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [connections, config, types] = await Promise.all([api.calendarConnections(), api.bookingConfiguration(), api.appointmentTypes()]);
      config.business_hours = config.business_hours.map((item) => ({ ...item, start_time: item.start_time.slice(0, 5), end_time: item.end_time.slice(0, 5) }));
      setOverview(connections); setConfiguration(config); setAppointmentTypes(types);
    } catch (loadError) { setError(readableError(loadError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("calendar_oauth") === "success") setNotice("Die Kalenderverbindung wurde erfolgreich hergestellt und synchronisiert.");
    if (params.get("calendar_oauth") === "error") setError(`Die Kalenderverbindung ist fehlgeschlagen (${params.get("error_code") ?? "oauth_error"}).`);
  }, []);

  const calendars = useMemo(() => overview?.connections.flatMap((connection) => connection.calendars.map((calendar) => ({ ...calendar, provider: connection.provider, account: connection.account_email }))) ?? [], [overview]);

  async function run(key: string, operation: () => Promise<void>, success: string) {
    setBusy(key); setError(null); setNotice(null);
    try { await operation(); setNotice(success); }
    catch (operationError) { setError(readableError(operationError)); }
    finally { setBusy(null); }
  }

  async function connect(provider: CalendarProviderConfiguration) {
    await run(`connect-${provider.provider}`, async () => {
      const value = await api.startCalendarOAuth(provider.provider);
      window.location.assign(value.authorization_url);
    }, "Weiterleitung zum Kalenderanbieter gestartet.");
  }

  function updateCalendar(id: string, values: Partial<ExternalCalendar>) {
    setOverview((current) => current ? ({ ...current, connections: current.connections.map((connection) => ({ ...connection, calendars: connection.calendars.map((calendar) => calendar.id === id ? { ...calendar, ...values } : (values.is_selected_for_booking ? { ...calendar, is_selected_for_booking: false } : calendar)) })) }) : current);
  }

  async function saveCalendars() {
    await run("save-calendars", async () => {
      await api.saveCalendarSelection(calendars.map((item) => ({ id: item.id, is_selected_for_availability: item.is_selected_for_availability, is_selected_for_booking: item.is_selected_for_booking })));
      await load();
    }, "Die wirksame Kalenderauswahl wurde gespeichert.");
  }

  async function saveConfiguration() {
    if (!configuration) return;
    await run("save-configuration", async () => { setConfiguration(await api.saveBookingConfiguration(configuration)); }, "Geschäftszeiten und Buchungsregeln wurden gespeichert.");
  }

  function addWindow(weekday: number) {
    setConfiguration((current) => current ? ({ ...current, business_hours: [...current.business_hours, { weekday, start_time: "09:00", end_time: "17:00", is_active: true }] }) : current);
  }

  async function saveAppointmentType() {
    await run("save-type", async () => {
      if (editingType) await api.updateAppointmentType(editingType.id, typeDraft);
      else await api.createAppointmentType(typeDraft);
      setEditingType(null); setTypeDraft(emptyAppointment); setAppointmentTypes(await api.appointmentTypes());
    }, editingType ? "Terminart aktualisiert." : "Terminart angelegt.");
  }

  if (loading) return <div className="page"><div className="center-state" aria-label="Kalenderdaten werden geladen"><div className="spinner" /><p>Kalenderintegration wird geladen …</p></div></div>;

  return <div className="page calendar-page">
    <PageHeader eyebrow="Einstellungen · Integrationen" title="Kalender" description="Google Kalender und Microsoft Outlook sicher verbinden, Verfügbarkeit steuern und echte Terminbuchungen ermöglichen." />
    {notice && <div className="notice" role="status"><span>✓</span><p>{notice}</p><button onClick={() => setNotice(null)} aria-label="Hinweis schließen">×</button></div>}
    {error && <div className="notice error" role="alert"><span>!</span><p>{error}</p><button onClick={() => setError(null)} aria-label="Fehler schließen">×</button></div>}
    <div className="calendar-tabs" role="tablist">
      {([[
        "providers", "Anbieter"
      ], ["calendars", "Kalenderauswahl"], ["availability", "Verfügbarkeit"], ["appointment-types", "Terminarten"]] as const).map(([value, label]) => <button key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)} role="tab" aria-selected={tab === value}>{label}</button>)}
    </div>

    {tab === "providers" && <div className="calendar-provider-grid">{overview?.providers.map((provider) => {
      const connections = overview.connections.filter((item) => item.provider === provider.provider);
      const connected = connections.find((item) => item.connection_status === "connected");
      const connection = connected ?? connections[0];
      return <section className="card calendar-provider-card" key={provider.provider}>
        <div className={`provider-mark ${provider.provider}`}>{provider.provider === "google" ? "G" : "M"}</div>
        <div className="provider-title"><div><h2>{provider.label}</h2><p>{connection?.account_email || "Noch kein Konto verbunden"}</p></div><StatusBadge active={Boolean(connected)}>{!provider.configured ? "Nicht konfiguriert" : connected ? "Verbunden" : connection?.connection_status === "reauthorization_required" ? "Erneut verbinden" : "Nicht verbunden"}</StatusBadge></div>
        {connection?.last_successful_request_at && <small>Letzter erfolgreicher Zugriff: {new Date(connection.last_successful_request_at).toLocaleString("de-DE")}</small>}
        {!provider.configured && <div className="provider-config-warning"><strong>Anbieter noch nicht konfiguriert</strong><span>Serverseitig fehlen: {provider.missing_configuration.join(", ")}</span></div>}
        <div className="calendar-actions">
          <button className="button primary" disabled={!provider.configured || busy !== null} onClick={() => void connect(provider)}>{connected ? "Erneut verbinden" : "Verbinden"}</button>
          {connected && <button className="button secondary" disabled={busy !== null} onClick={() => void run(`test-${connected.id}`, async () => { const result = await api.testCalendarConnection(connected.id); setNotice(`Verbindung erfolgreich getestet: ${result.calendars_found} Kalender gefunden.`); }, "Verbindung erfolgreich getestet.")}>Verbindung testen</button>}
          {connection && <button className="button danger-outline" disabled={busy !== null} onClick={() => { if (window.confirm("Kalenderverbindung wirklich trennen?")) void run(`disconnect-${connection.id}`, async () => { await api.disconnectCalendar(connection.id); await load(); }, "Kalenderverbindung getrennt."); }}>Verbindung trennen</button>}
        </div>
      </section>;
    })}</div>}

    {tab === "calendars" && <section className="card calendar-section">
      <div className="section-heading"><div><h2>Wirksame Kalenderauswahl</h2><p>Mehrere Kalender dürfen Zeiten blockieren. Genau ein beschreibbarer Kalender nimmt neue Termine auf.</p></div><button className="button primary" disabled={!calendars.length || busy !== null} onClick={() => void saveCalendars()}>Auswahl speichern</button></div>
      {!calendars.length ? <div className="calendar-empty"><strong>Noch keine Kalender verfügbar</strong><p>Verbinde zuerst einen konfigurierten Anbieter. Es werden keine Beispielkalender simuliert.</p></div> : <div className="calendar-list">{calendars.map((calendar) => <div className="calendar-row" key={calendar.id}>
        <div><strong>{calendar.calendar_name}</strong><span>{calendar.provider === "google" ? "Google" : "Microsoft"} · {calendar.account} · {calendar.calendar_timezone}</span><small>{calendar.is_primary ? "Hauptkalender" : "Zusätzlicher Kalender"} · {calendar.can_write ? "Schreibzugriff" : "Nur Lesen"}</small></div>
        <label><input type="checkbox" checked={calendar.is_selected_for_availability} onChange={(event) => updateCalendar(calendar.id, { is_selected_for_availability: event.target.checked })} /> Verfügbarkeit prüfen</label>
        <label><input type="radio" name="booking-calendar" checked={calendar.is_selected_for_booking} disabled={!calendar.can_write} onChange={() => updateCalendar(calendar.id, { is_selected_for_booking: true })} /> Zielkalender</label>
      </div>)}</div>}
      <div className="calendar-refresh-actions">{overview?.connections.filter((item) => item.connection_status === "connected").map((connection) => <button className="text-button" key={connection.id} disabled={busy !== null} onClick={() => void run(`refresh-${connection.id}`, async () => { await api.refreshCalendars(connection.id); await load(); }, "Kalenderliste aktualisiert.")}>{connection.account_email || connection.display_name} aktualisieren</button>)}</div>
    </section>}

    {tab === "availability" && configuration && <section className="card calendar-section">
      <div className="section-heading"><div><h2>Verfügbarkeit und Buchungsregeln</h2><p>Diese Werte werden serverseitig bei jeder Suche und unmittelbar vor jeder Buchung angewendet.</p></div><button className="button primary" disabled={busy !== null} onClick={() => void saveConfiguration()}>Regeln speichern</button></div>
      <div className="calendar-rule-grid">
        <label>Zeitzone<input value={configuration.timezone} onChange={(event) => setConfiguration({ ...configuration, timezone: event.target.value })} /></label>
        <label>Startzeitraster (Min.)<input type="number" min="5" max="120" value={configuration.slot_interval_minutes} onChange={(event) => setConfiguration({ ...configuration, slot_interval_minutes: Number(event.target.value) })} /></label>
        <label>Mindestvorlauf (Min.)<input type="number" min="0" value={configuration.minimum_notice_minutes} onChange={(event) => setConfiguration({ ...configuration, minimum_notice_minutes: Number(event.target.value) })} /></label>
        <label>Buchungshorizont (Tage)<input type="number" min="1" value={configuration.maximum_booking_horizon_days} onChange={(event) => setConfiguration({ ...configuration, maximum_booking_horizon_days: Number(event.target.value) })} /></label>
        <label>Standardpuffer vorher (Min.)<input type="number" min="0" value={configuration.buffer_before_minutes} onChange={(event) => setConfiguration({ ...configuration, buffer_before_minutes: Number(event.target.value) })} /></label>
        <label>Standardpuffer nachher (Min.)<input type="number" min="0" value={configuration.buffer_after_minutes} onChange={(event) => setConfiguration({ ...configuration, buffer_after_minutes: Number(event.target.value) })} /></label>
        <label>Maximale Vorschläge<input type="number" min="1" max="10" value={configuration.maximum_suggestions_per_request} onChange={(event) => setConfiguration({ ...configuration, maximum_suggestions_per_request: Number(event.target.value) })} /></label>
      </div>
      <div className="calendar-hours"><h3>Geschäftszeiten</h3>{weekdays.map((label, weekday) => {
        const windows = configuration.business_hours.map((item, index) => ({ item, index })).filter(({ item }) => item.weekday === weekday);
        return <div className="calendar-day" key={weekday}><strong>{label}</strong><div>{windows.length ? windows.map(({ item, index }) => <div className="calendar-window" key={index}>
          <input aria-label={`${label} Beginn`} type="time" value={item.start_time.slice(0, 5)} onChange={(event) => setConfiguration({ ...configuration, business_hours: configuration.business_hours.map((value, itemIndex) => itemIndex === index ? { ...value, start_time: event.target.value } : value) })} />
          <span>bis</span><input aria-label={`${label} Ende`} type="time" value={item.end_time.slice(0, 5)} onChange={(event) => setConfiguration({ ...configuration, business_hours: configuration.business_hours.map((value, itemIndex) => itemIndex === index ? { ...value, end_time: event.target.value } : value) })} />
          <button className="text-button danger" aria-label={`${label} Zeitfenster entfernen`} onClick={() => setConfiguration({ ...configuration, business_hours: configuration.business_hours.filter((_, itemIndex) => itemIndex !== index) })}>Entfernen</button>
        </div>) : <small>Geschlossen</small>}<button className="text-button" onClick={() => addWindow(weekday)}>+ Zeitfenster</button></div></div>;
      })}</div>
    </section>}

    {tab === "appointment-types" && <div className="calendar-type-layout"><section className="card calendar-section">
      <div className="section-heading"><div><h2>Terminarten</h2><p>Keine Beispieldaten: Nur hier angelegte aktive Terminarten stehen dem Telefonagenten zur Verfügung.</p></div></div>
      {!appointmentTypes.length ? <div className="calendar-empty"><strong>Noch keine Terminart angelegt</strong><p>Lege rechts die erste wirksame Terminart an.</p></div> : <div className="appointment-type-list">{appointmentTypes.map((item) => <article key={item.id}><div><strong>{item.name}</strong><span>{item.duration_minutes} Minuten · {item.location_type} · {item.is_active ? "Aktiv" : "Inaktiv"}</span><p>{item.description || "Keine Beschreibung"}</p></div><div><button className="text-button" onClick={() => { setEditingType(item); setTypeDraft({ name: item.name, description: item.description, duration_minutes: item.duration_minutes, buffer_before_minutes: item.buffer_before_minutes, buffer_after_minutes: item.buffer_after_minutes, location_type: item.location_type, location_text: item.location_text, is_active: item.is_active }); }}>Bearbeiten</button><button className="text-button danger" onClick={() => { if (window.confirm("Terminart wirklich löschen?")) void run(`delete-${item.id}`, async () => { await api.deleteAppointmentType(item.id); setAppointmentTypes(await api.appointmentTypes()); }, "Terminart gelöscht."); }}>Löschen</button></div></article>)}</div>}
    </section><section className="card calendar-section appointment-type-form"><h2>{editingType ? "Terminart bearbeiten" : "Terminart anlegen"}</h2>
      <label>Name<input value={typeDraft.name} onChange={(event) => setTypeDraft({ ...typeDraft, name: event.target.value })} /></label>
      <label>Dauer (Minuten)<input type="number" min="5" value={typeDraft.duration_minutes} onChange={(event) => setTypeDraft({ ...typeDraft, duration_minutes: Number(event.target.value) })} /></label>
      <label>Beschreibung<textarea value={typeDraft.description} onChange={(event) => setTypeDraft({ ...typeDraft, description: event.target.value })} /></label>
      <div className="calendar-form-pair"><label>Puffer vorher<input type="number" min="0" value={typeDraft.buffer_before_minutes ?? ""} placeholder="Standard" onChange={(event) => setTypeDraft({ ...typeDraft, buffer_before_minutes: event.target.value === "" ? null : Number(event.target.value) })} /></label><label>Puffer nachher<input type="number" min="0" value={typeDraft.buffer_after_minutes ?? ""} placeholder="Standard" onChange={(event) => setTypeDraft({ ...typeDraft, buffer_after_minutes: event.target.value === "" ? null : Number(event.target.value) })} /></label></div>
      <label>Terminform<select value={typeDraft.location_type} onChange={(event) => setTypeDraft({ ...typeDraft, location_type: event.target.value as AppointmentTypeWrite["location_type"] })}><option value="phone">Telefon</option><option value="onsite">Vor Ort</option><option value="video">Video</option><option value="custom">Individuell</option></select></label>
      <label>Ort oder Hinweis<input value={typeDraft.location_text} onChange={(event) => setTypeDraft({ ...typeDraft, location_text: event.target.value })} /></label>
      <label className="agent-toggle"><input type="checkbox" checked={typeDraft.is_active} onChange={(event) => setTypeDraft({ ...typeDraft, is_active: event.target.checked })} /><span><strong>Aktiv</strong><small>Nur aktive Terminarten kann der Agent anbieten.</small></span></label>
      <div className="calendar-actions"><button className="button primary" disabled={!typeDraft.name.trim() || busy !== null} onClick={() => void saveAppointmentType()}>{editingType ? "Änderungen speichern" : "Terminart anlegen"}</button>{editingType && <button className="button secondary" onClick={() => { setEditingType(null); setTypeDraft(emptyAppointment); }}>Abbrechen</button>}</div>
    </section></div>}
  </div>;
}
