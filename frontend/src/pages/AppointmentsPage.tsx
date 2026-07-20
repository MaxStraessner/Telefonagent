import { EmptyState } from "../components/AsyncState";
import { Icon } from "../components/Icon";
import { DataPage, PageHeader } from "./shared";

export function AppointmentsPage() {
  return <DataPage>{(data) => <div className="page"><PageHeader eyebrow="Planung" title="Termine" description="Alle Termine des aktiven Unternehmens auf einen Blick." />
    <section className="card content-card">{data.appointments.length === 0 ? <EmptyState icon={<Icon name="calendar" size={28} />} title="Noch keine Testtermine vorhanden">Der aktuelle Sprachagent erfasst Wünsche nur im flüchtigen Gespräch und erstellt noch keine Termine.</EmptyState> : <>
      <div className="desktop-table"><table><thead><tr><th>Datum & Uhrzeit</th><th>Leistung</th><th>Mitarbeiter</th><th>Kunde</th><th>Quelle</th><th>Status</th></tr></thead><tbody>{data.appointments.map((item) => <tr key={item.id}><td>{new Date(item.starts_at).toLocaleString("de-DE")}</td><td>{item.service?.name ?? "—"}</td><td>{item.staff_member?.display_name ?? "—"}</td><td>{item.customer_name}</td><td>{item.source}</td><td>{item.status}</td></tr>)}</tbody></table></div>
      <div className="mobile-list">{data.appointments.map((item) => <article className="list-card" key={item.id}><strong>{item.service?.name ?? "Termin"}</strong><span>{new Date(item.starts_at).toLocaleString("de-DE")}</span><span>{item.customer_name}</span></article>)}</div>
    </>}</section><p className="page-footnote">Termine können in diesem Entwicklungsstand weder angelegt noch verändert oder gelöscht werden.</p>
  </div>}</DataPage>;
}

