import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { DataPage, PageHeader } from "./shared";

export function ServicesPage() {
  return <DataPage>{(data) => <div className="page"><PageHeader eyebrow="Angebot" title="Leistungen" description="Vorbereitete Leistungen für den aktiven Testmandanten." />
    <div className="grid item-grid">{data.services.map((service) => <article className="card item-card" key={service.id}><div className="item-icon"><Icon name="services" /></div><div className="item-card-head"><h2>{service.name}</h2><StatusBadge active={service.is_active}>{service.is_active ? "Aktiv" : "Inaktiv"}</StatusBadge></div><p>{service.description}</p><div className="item-meta"><span>Dauer</span><strong>{service.duration_minutes} Minuten</strong></div></article>)}</div>
    <p className="page-footnote">Bearbeitung und Zuordnung zu Mitarbeitern folgen mit der späteren Terminlogik.</p></div>}</DataPage>;
}

