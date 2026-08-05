import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";

export function PlatformLayout() {
  const { session, logout, clearCompanyContext } = useAuth();
  const isOwner = session?.user.platform_role === "owner";
  return <div className="platform-shell">
    <header className="platform-header">
      <div className="brand compact"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Plattformverwaltung</small></div></div>
      <div className="platform-user"><span>{session?.user.display_name}</span><button type="button" onClick={() => void logout()}>Abmelden</button></div>
    </header>
    {session?.active_company && <div className="context-banner">
      <strong>Aktiver Unternehmenskontext: {session.active_company.name}</strong>
      <span>Fachdatenzugriffe sind auf dieses Unternehmen begrenzt.</span>
      <NavLink to="/">Unternehmensbereich öffnen</NavLink>
      <button type="button" onClick={() => void clearCompanyContext()}>Kontext verlassen</button>
    </div>}
    <div className="platform-body">
      <aside className="platform-nav"><nav aria-label="Plattformnavigation">
        <NavLink to="/plattform" end>Dashboard</NavLink>
        <NavLink to="/plattform/unternehmen">Unternehmen</NavLink>
        {isOwner && <NavLink to="/plattform/administratoren">Plattformadmins</NavLink>}
        <NavLink to="/plattform/audit">Auditprotokoll</NavLink>
      </nav></aside>
      <main className="platform-content"><Outlet /></main>
    </div>
  </div>;
}
