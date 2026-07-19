import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { DataPage, PageHeader } from "./shared";

export function StaffPage() {
  return <DataPage>{(data) => <div className="page"><PageHeader eyebrow="Team" title="Mitarbeiter" description="Das vorbereitete Team des aktiven Unternehmens." />
    <div className="grid staff-grid">{data.staff.map((member) => <article className="card staff-card" key={member.id}><div className="staff-avatar"><Icon name="staff" size={28} /></div><div><h2>{member.display_name}</h2><p>{member.role_name}</p></div><StatusBadge active={member.is_active}>{member.is_active ? "Aktiv" : "Inaktiv"}</StatusBadge></article>)}</div>
    <p className="page-footnote">Verfügbarkeiten und Leistungszuordnungen sind bewusst noch nicht umgesetzt.</p></div>}</DataPage>;
}

