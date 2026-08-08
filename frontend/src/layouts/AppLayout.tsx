import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Icon } from "../components/Icon";
import { usePersistentSetting } from "../hooks/usePersistentSetting";
import { usePlatformData } from "../api/PlatformDataProvider";
import { useAuth } from "../api/AuthProvider";

const navigation = [
  ["/", "Übersicht", "home", null], ["/testgespraech", "Testgespräch", "call", null],
  ["/ki-konfigurieren", "KI konfigurieren", "mic", "company.manage"],
  ["/kalender", "Kalenderintegration", "calendar", "company.manage"],
  ["/termine", "Termine", "calendar", null], ["/leistungen", "Leistungen", "services", "company.manage"],
  ["/mitarbeiter", "Mitarbeiter", "staff", "company.manage"], ["/konten", "Konten", "staff", "company.users.manage"], ["/unternehmen", "Unternehmen", "company", "company.manage"],
  ["/audit", "Auditprotokoll", "system", "company.audit.read"], ["/system", "System", "system", "company.manage"],
] as const;

export function AppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = usePersistentSetting<"light" | "dark">("telefonagent-theme", "light");
  const { data } = usePlatformData();
  const { session, logout, clearCompanyContext } = useAuth();
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);

  return <div className="app-shell">
    <header className="mobile-header">
      <button className="icon-button" aria-label="Navigation öffnen" onClick={() => setMenuOpen(true)}><Icon name="menu" /></button>
      <div className="brand compact"><span className="brand-mark">T</span><span>Telefonagent</span></div>
      <button className="icon-button" aria-label="Farbschema wechseln" onClick={() => setTheme(theme === "light" ? "dark" : "light")}><Icon name={theme === "light" ? "moon" : "sun"} /></button>
    </header>
    {menuOpen && <button className="scrim" aria-label="Navigation schließen" onClick={() => setMenuOpen(false)} />}
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
      <div className="sidebar-top">
        <div className="brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Platform</small></div></div>
        <button className="icon-button close-menu" aria-label="Navigation schließen" onClick={() => setMenuOpen(false)}><Icon name="close" /></button>
      </div>
      <nav aria-label="Hauptnavigation">{navigation.filter(([, , , permission]) => !permission || (session?.permissions ?? []).includes(permission)).map(([path, label, icon]) => <NavLink to={path} end={path === "/"} onClick={() => setMenuOpen(false)} key={path}><Icon name={icon} /><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-footer">
        <button className="theme-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")}><Icon name={theme === "light" ? "moon" : "sun"} /><span>{theme === "light" ? "Dunkler Modus" : "Heller Modus"}</span></button>
        <div className="tenant-chip"><span className="avatar">{data?.tenant.name.charAt(0) ?? "T"}</span><div><strong>{data?.tenant.name ?? session?.tenant?.name ?? "Unternehmen"}</strong><small>{session?.user.display_name} · {session?.user.role ?? session?.user.platform_role}</small></div></div>
        <button className="theme-button" onClick={() => void logout()}><Icon name="close" /><span>Abmelden</span></button>
      </div>
    </aside>
    <main className="main-content">
      <div className="context-banner company-context"><strong>Aktiver Unternehmenskontext: {session?.active_company?.name ?? session?.tenant?.name}</strong><span>Alle Fachfunktionen verwenden ausschließlich diesen Kontext.</span>{session?.user.platform_role && <><NavLink to="/plattform">Plattformverwaltung</NavLink><button type="button" onClick={() => void clearCompanyContext()}>Kontext verlassen</button></>}</div>
      <Outlet />
    </main>
  </div>;
}

