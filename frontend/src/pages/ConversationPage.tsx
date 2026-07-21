import { useState } from "react";
import { Icon } from "../components/Icon";
import { usePersistentSetting } from "../hooks/usePersistentSetting";
import { initialConversation } from "../features/conversation/state";
import { DataPage, PageHeader } from "./shared";

export function ConversationPage() {
  const [notice, setNotice] = useState(false);
  const [mode, setMode] = usePersistentSetting<"test" | "presentation">("telefonagent-display-mode", "test");
  const [session, setSession] = useState(initialConversation);
  return <DataPage>{(data) => <div className={`page conversation-page ${mode}`}>
    <PageHeader eyebrow="Browser-Testumgebung" title={`Gespräch mit ${data.tenant.settings.assistant_name}`} description={`${data.tenant.name} · Sprachverbindung noch nicht eingerichtet`} action={<div className="segmented" aria-label="Darstellungsmodus"><button className={mode === "test" ? "active" : ""} onClick={() => setMode("test")}>Testmodus</button><button className={mode === "presentation" ? "active" : ""} onClick={() => setMode("presentation")}>Präsentation</button></div>} />
    {notice && <div className="notice" role="status"><span>i</span><p>Die Sprachverbindung wird im nächsten Entwicklungsschritt eingerichtet.</p><button aria-label="Hinweis schließen" onClick={() => setNotice(false)}>×</button></div>}
    <div className="conversation-grid">
      <section className="card call-stage">
        <span className="status-badge pending"><span className="status-dot" />Nicht eingerichtet</span>
        <div className="voice-visual" aria-label="Mikrofon inaktiv"><i /><i /><span><Icon name="mic" size={32} /></span><i /><i /></div>
        <h2>{data.tenant.settings.assistant_name}</h2><p>Bereit für die spätere Realtime-Verbindung</p>
        <div className="call-actions">
          <button className="button primary large" onClick={() => setNotice(true)}><Icon name="call" /> Testgespräch starten</button>
          <button className="button secondary" disabled onClick={() => setSession({ ...session, state: "ended" })}>Gespräch beenden</button>
          <button className="button ghost" disabled onClick={() => setSession({ ...session, muted: !session.muted })}>Mikrofon stummschalten</button>
        </div>
        <small>Es wird weder Audio aufgenommen noch eine externe Verbindung aufgebaut.</small>
      </section>
      <aside className="appointment-preview card"><div className="section-heading"><h2>Erkannte Termindaten</h2><span>Keine Daten</span></div><div className="preview-rows"><div><span>Leistung</span><strong>—</strong></div><div><span>Wunschtermin</span><strong>—</strong></div><div><span>Mitarbeiter</span><strong>—</strong></div></div><p className="subtle-note">Die erkannten Angaben erscheinen hier erst nach der Realtime-Integration.</p></aside>
    </div>
    {mode === "test" && <div className="grid diagnostic-grid">
      <section className="card"><div className="section-heading"><h2>Transkript</h2><span>0 Nachrichten</span></div><div className="placeholder-panel"><p>Noch kein Transkript</p><small>Gesprächsinhalte werden in diesem Schritt nicht erzeugt oder gespeichert.</small></div></section>
      <section className="card"><div className="section-heading"><h2>Werkzeugaufrufe</h2><span>0 Aufrufe</span></div><div className="placeholder-panel"><p>Noch keine Werkzeugaufrufe</p><small>Kalender- und Buchungswerkzeuge sind nicht angebunden.</small></div></section>
      <section className="card diagnosis"><div className="section-heading"><h2>Technische Diagnose</h2><span>Testmodus</span></div><dl><div><dt>Zustand</dt><dd>{session.state}</dd></div><div><dt>Transport</dt><dd>Nicht konfiguriert</dd></div><div><dt>Reaktionszeit</dt><dd>— ms</dd></div><div><dt>Audio</dt><dd>Inaktiv</dd></div></dl></section>
    </div>}
  </div>}</DataPage>;
}

