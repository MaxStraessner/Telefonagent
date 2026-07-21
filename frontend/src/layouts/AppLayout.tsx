import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Icon } from "../components/Icon";
import { usePersistentSetting } from "../hooks/usePersistentSetting";
import { usePlatformData } from "../api/PlatformDataProvider";

const navigation = [
  ["/", "Übersicht", "home"], ["/testgespraech", "Testgespräch", "call"],
  ["/ki-konfigurieren", "KI konfigurieren", "mic"],
  ["/termine", "Termine", "calendar"], ["/leistungen", "Leistungen", "services"],
  ["/mitarbeiter", "Mitarbeiter", "staff"], ["/unternehmen", "Unternehmen", "company"],
  ["/system", "System", "system"],
] as const;

export function AppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = usePersistentSetting<"light" | "dark">("telefonagent-theme", "light");
  const { data } = usePlatformData();
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
      <nav aria-label="Hauptnavigation">{navigation.map(([path, label, icon]) => <NavLink to={path} end={path === "/"} onClick={() => setMenuOpen(false)} key={path}><Icon name={icon} /><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-footer">
        <button className="theme-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")}><Icon name={theme === "light" ? "moon" : "sun"} /><span>{theme === "light" ? "Dunkler Modus" : "Heller Modus"}</span></button>
        <div className="tenant-chip"><span className="avatar">{data?.tenant.name.charAt(0) ?? "T"}</span><div><strong>{data?.tenant.name ?? "Unternehmen"}</strong><small>Lokaler Testmandant</small></div></div>
      </div>
    </aside>
    <main className="main-content"><Outlet /></main>
  </div>;
}

