import { useState } from "react";
import { API_BASE_URL } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { DataPage, PageHeader } from "./shared";

export function SystemPage() {
  const [open, setOpen] = useState(false);
  return <DataPage>{(data) => <div className="page"><PageHeader eyebrow="Technik" title="System" description="Transparenter Status der lokalen Plattformkomponenten." />
    <div className="grid system-grid">
      <section className="card system-card"><span>Backend</span><h2>Version {data.platformStatus.backend_version}</h2><StatusBadge active={data.health.status === "healthy"}>Erreichbar</StatusBadge></section>
      <section className="card system-card"><span>Datenbank</span><h2>{data.platformStatus.database_connected ? "Verbunden" : "Nicht erreichbar"}</h2><StatusBadge active={data.platformStatus.database_connected}>{data.health.database}</StatusBadge></section>
      <section className="card system-card"><span>Frontend</span><h2>Version {__APP_VERSION__}</h2><StatusBadge active>Bereit</StatusBadge></section>
      <section className="card system-card"><span>Umgebung</span><h2>{data.platformStatus.environment}</h2><StatusBadge active>Lokaler Modus</StatusBadge></section>
    </div>
    <section className="card integrations"><h2>Integrationen</h2><div className="integration-row"><div><strong>OpenAI Realtime</strong><span>Sprachverbindung</span></div><StatusBadge active={data.platformStatus.realtime_voice_configured}>{data.platformStatus.realtime_voice_configured ? "Konfiguriert" : "Nicht eingerichtet"}</StatusBadge></div><div className="integration-row"><div><strong>Telefonie</strong><span>SIP / Rufnummern</span></div><StatusBadge active={data.platformStatus.telephony_configured}>Nicht eingerichtet</StatusBadge></div><div className="integration-row"><div><strong>Kalender</strong><span>Externer Provider</span></div><StatusBadge active={data.platformStatus.calendar_configured}>Lokaler Testmodus</StatusBadge></div></section>
    <section className="card diagnostics"><button className="diagnostics-toggle" aria-expanded={open} onClick={() => setOpen(!open)}><span><strong>Diagnoseinformationen</strong><small>Keine geheimen Konfigurationswerte</small></span><b>{open ? "−" : "+"}</b></button>{open && <dl><div><dt>API-Basisadresse</dt><dd>{API_BASE_URL}</dd></div><div><dt>Backendstatus</dt><dd>{data.health.status}</dd></div><div><dt>Datenbankstatus</dt><dd>{data.health.database}</dd></div><div><dt>Aktiver Mandant</dt><dd>{data.tenant.slug}</dd></div></dl>}</section>
  </div>}</DataPage>;
}

