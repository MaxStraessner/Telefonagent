import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { DataPage, industryLabels, PageHeader } from "./shared";

export function OverviewPage() {
  return <DataPage>{(data) => <div className="page">
    <PageHeader eyebrow="Plattformübersicht" title={`Guten Tag bei ${data.tenant.name}`} description={`${industryLabels[data.tenant.industry] ?? data.tenant.industry} · ${data.tenant.timezone}`} action={<Link className="button primary" to="/testgespraech">Testbereich öffnen <Icon name="arrow" /></Link>} />
    <section className="hero-panel">
      <div><StatusBadge active={data.platformStatus.database_connected}>System betriebsbereit</StatusBadge><h2>Ihr digitaler Assistent ist vorbereitet.</h2><p>Die Plattformbasis steht. Die Sprachverbindung folgt im nächsten Entwicklungsschritt und ist klar als noch nicht eingerichtet markiert.</p></div>
      <div className="hero-orb"><span>{data.tenant.settings.assistant_name.charAt(0)}</span><i /><i /><i /></div>
    </section>
    <section aria-labelledby="kennzahlen"><div className="section-heading"><h2 id="kennzahlen">Auf einen Blick</h2><span>Lokale Testumgebung</span></div>
      <div className="grid metrics-grid">
        <article className="card metric"><span>Aktive Leistungen</span><strong>{data.services.filter((item) => item.is_active).length}</strong><small>Aus PostgreSQL geladen</small></article>
        <article className="card metric"><span>Aktive Mitarbeiter</span><strong>{data.staff.filter((item) => item.is_active).length}</strong><small>Für spätere Planung bereit</small></article>
        <article className="card metric"><span>Aktuelle Termine</span><strong>{data.appointments.length}</strong><small>Mandantenbezogene Ansicht</small></article>
      </div>
    </section>
    <section aria-labelledby="status"><div className="section-heading"><h2 id="status">Verbindungsstatus</h2></div>
      <div className="grid status-grid">
        <article className="card status-card"><span className="status-icon success"><Icon name="check" /></span><div><h3>Datenbank</h3><p>Bereit</p></div><StatusBadge active>Verbunden</StatusBadge></article>
        <article className="card status-card"><span className="status-icon"><Icon name="call" /></span><div><h3>Sprachagent</h3><p>Noch nicht eingerichtet</p></div><StatusBadge active={false}>Vorbereitet</StatusBadge></article>
        <article className="card status-card"><span className="status-icon"><Icon name="system" /></span><div><h3>Telefonie</h3><p>Noch nicht eingerichtet</p></div><StatusBadge active={false}>Vorbereitet</StatusBadge></article>
        <article className="card status-card"><span className="status-icon success"><Icon name="calendar" /></span><div><h3>Kalender</h3><p>Lokaler Testmodus</p></div><StatusBadge active>Testmodus</StatusBadge></article>
      </div>
    </section>
  </div>}</DataPage>;
}

