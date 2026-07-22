import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Service } from "../types/api";
import { DataPage, PageHeader } from "./shared";

type ServiceDraft = Omit<Service, "id">;
const emptyDraft: ServiceDraft = { name: "", description: "", duration_minutes: 30, is_active: true };

export function ServicesPage() {
  return <DataPage>{(data) => <ServicesContent initialServices={data.services} />}</DataPage>;
}

function ServicesContent({ initialServices }: { initialServices: Service[] }) {
  const [services, setServices] = useState(initialServices);
  const [editing, setEditing] = useState<Service | null>(null);
  const [draft, setDraft] = useState<ServiceDraft>(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function beginEdit(item: Service) {
    setEditing(item);
    setDraft({ name: item.name, description: item.description, duration_minutes: item.duration_minutes, is_active: item.is_active });
    setError(null);
  }

  async function save() {
    setBusy(true); setError(null); setNotice(null);
    try {
      if (editing) await api.updateService(editing.id, draft);
      else await api.createService(draft);
      setServices(await api.services());
      setEditing(null); setDraft(emptyDraft);
      setNotice(editing ? "Leistung aktualisiert." : "Leistung angelegt.");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Die Leistung konnte nicht gespeichert werden.");
    } finally { setBusy(false); }
  }

  async function toggleActive(item: Service) {
    setBusy(true); setError(null); setNotice(null);
    try {
      await api.updateService(item.id, { name: item.name, description: item.description, duration_minutes: item.duration_minutes, is_active: !item.is_active });
      setServices(await api.services());
      setNotice(item.is_active ? "Leistung deaktiviert. Bestehende Termine bleiben erhalten." : "Leistung aktiviert.");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Der Aktivstatus konnte nicht geändert werden.");
    } finally { setBusy(false); }
  }

  return <div className="page">
    <PageHeader eyebrow="Terminverwaltung" title="Leistungen" description="Buchbare Angebote als Stammdaten verwalten. Dauer und Bezeichnung werden in Terminarten übernommen." />
    {notice && <div className="notice" role="status"><span>✓</span><p>{notice}</p></div>}
    {error && <div className="notice error" role="alert"><span>!</span><p>{error}</p></div>}
    <div className="calendar-type-layout">
      <section className="card calendar-section">
        <div className="section-heading"><div><h2>Vorhandene Leistungen</h2><p>Deaktivierte Leistungen bleiben historisch sichtbar, können aber nicht neu gebucht werden.</p></div></div>
        {!services.length ? <div className="calendar-empty"><strong>Noch keine Leistung angelegt</strong><p>Lege rechts die erste buchbare Leistung an.</p></div> : <div className="appointment-type-list">{services.map((item) => <article key={item.id}>
          <div><strong>{item.name}</strong><span>{item.duration_minutes} Minuten · {item.is_active ? "Aktiv" : "Inaktiv"}</span><p>{item.description || "Keine Beschreibung"}</p></div>
          <div><button className="text-button" onClick={() => beginEdit(item)}>Bearbeiten</button><button className={`text-button ${item.is_active ? "danger" : ""}`} disabled={busy} onClick={() => void toggleActive(item)}>{item.is_active ? "Deaktivieren" : "Aktivieren"}</button></div>
        </article>)}</div>}
      </section>
      <section className="card calendar-section appointment-type-form">
        <h2>{editing ? "Leistung bearbeiten" : "Leistung anlegen"}</h2>
        <label>Name<input value={draft.name} maxLength={150} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
        <label>Beschreibung<textarea rows={4} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
        <label>Dauer in Minuten<input type="number" min={5} max={720} step={5} value={draft.duration_minutes} onChange={(event) => setDraft({ ...draft, duration_minutes: Number(event.target.value) })} /></label>
        <label className="agent-toggle"><input type="checkbox" checked={draft.is_active} onChange={(event) => setDraft({ ...draft, is_active: event.target.checked })} /><span><strong>Aktiv</strong><small>Nur aktive Leistungen können neuen Terminarten zugeordnet werden.</small></span></label>
        <div className="calendar-actions"><button className="button primary" disabled={busy || !draft.name.trim() || draft.duration_minutes < 5} onClick={() => void save()}>{editing ? "Änderungen speichern" : "Leistung anlegen"}</button>{editing && <button className="button secondary" onClick={() => { setEditing(null); setDraft(emptyDraft); }}>Abbrechen</button>}</div>
      </section>
    </div>
  </div>;
}
