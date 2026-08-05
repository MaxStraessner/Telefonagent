import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Icon } from "../components/Icon";
import { useRealtimeVoice } from "../features/realtime/useRealtimeVoice";
import type { RealtimeViewState } from "../features/realtime/types";
import { usePersistentSetting } from "../hooks/usePersistentSetting";
import type { PlatformData, RuntimeSummary } from "../types/api";
import { DataPage, PageHeader } from "./shared";

const stateLabels: Record<RealtimeViewState["state"], string> = {
  idle: "Bereit für ein Testgespräch",
  requesting_microphone: "Mikrofonzugriff wird angefordert",
  connecting: "Verbindung wird aufgebaut",
  connected: "Verbunden",
  muted: "Mikrofon ist stumm",
  user_speaking: "Du sprichst",
  assistant_thinking: "Antwort wird vorbereitet",
  assistant_speaking: "Assistent spricht",
  error: "Verbindung unterbrochen",
  ended: "Gespräch beendet",
  not_configured: "Nicht eingerichtet",
};

const playbackLabels: Record<NonNullable<RealtimeViewState["playbackStatus"]>, string> = {
  generating: "Audio wird erzeugt",
  playing: "Wiedergabe läuft",
  completed: "Vollständig wiedergegeben",
  interrupted: "Unterbrochen",
  failed: "Wiedergabefehler",
};

function metric(value: number | null, suffix = " ms") {
  return value === null ? "—" : `${value}${suffix}`;
}

function formatRemaining(seconds: number | null) {
  if (seconds === null) return "—";
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function ConversationPage() {
  return <DataPage>{(data) => <ConversationContent data={data} />}</DataPage>;
}

function ConversationContent({ data }: { data: PlatformData }) {
  const realtimeE2eStub = import.meta.env.VITE_REALTIME_E2E_STUB === "true";
  const [mode, setMode] = usePersistentSetting<"test" | "presentation">("telefonagent-display-mode", "test");
  const [audioElement, setAudioElement] = useState<HTMLAudioElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [runtime, setRuntime] = useState<RuntimeSummary | null>(null);
  const realtime = useRealtimeVoice(
    data.platformStatus.realtime_voice_configured || realtimeE2eStub,
    audioElement,
  );
  const { view } = realtime;
  const active = ["requesting_microphone", "connecting", "connected", "muted", "user_speaking", "assistant_thinking", "assistant_speaking"].includes(view.state);
  const voiceActive = view.state === "user_speaking" || view.state === "assistant_speaking";
  const canStart = (data.platformStatus.realtime_voice_configured || realtimeE2eStub) && !active;
  const agentName = runtime?.assistant_name ?? data.tenant.settings.assistant_name;
  const toolEvents = view.events.filter((event) => event.type.startsWith("tool_"));

  useEffect(() => {
    api.agentTestSession().then((value) => setRuntime(
      typeof value.speed === "number" && typeof value.configuration_version === "number" ? value : null,
    )).catch(() => setRuntime(null));
  }, []);

  useEffect(() => {
    const container = transcriptRef.current;
    if (!container) return;
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    if (nearBottom && typeof container.scrollTo === "function") container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [view.transcript]);

  return <div className={`page conversation-page ${mode}`}>
      <PageHeader
        eyebrow="Browser-Testumgebung"
        title={`Gespräch mit ${agentName}`}
        description={`${data.tenant.name} · OpenAI Realtime über eine direkte WebRTC-Sprachverbindung`}
        action={<div className="segmented" aria-label="Darstellungsmodus"><button className={mode === "test" ? "active" : ""} onClick={() => setMode("test")}>Testmodus</button><button className={mode === "presentation" ? "active" : ""} onClick={() => setMode("presentation")}>Präsentation</button></div>}
      />
      <audio ref={setAudioElement} autoPlay playsInline hidden aria-label="Sprachausgabe des Assistenten" />
      {realtimeE2eStub && <div className="notice warning" role="status"><span>!</span><p>Lokaler E2E-Stub aktiv: Mikrofon und externer Realtime-Provider bleiben deaktiviert.</p></div>}
      {!realtimeE2eStub && !data.platformStatus.realtime_voice_configured && <div className="notice warning" role="status"><span>!</span><p>OpenAI Realtime ist serverseitig noch nicht konfiguriert. Hinterlege den API-Key ausschließlich im Backend.</p></div>}
      {view.notice && <div className="notice" role="status"><span>i</span><p>{view.notice}</p><button aria-label="Hinweis schließen" onClick={realtime.dismissNotice}>×</button></div>}
      {view.error && <div className="notice error" role="alert"><span>!</span><p>{view.error}{mode === "test" && view.errorCode && <small className="technical-error-code">Fehlercode: <code>{view.errorCode}</code></small>}</p></div>}
      <div className="conversation-grid">
        <section className={`card call-stage state-${view.state}`}>
          <span className={`status-badge ${view.state === "error" || view.state === "not_configured" ? "pending" : "ready"}`}><span className="status-dot" />{stateLabels[view.state]}</span>
          <div className={`voice-visual ${voiceActive ? "active" : ""} ${view.muted ? "muted" : ""}`} aria-label={view.muted ? "Mikrofon stumm" : voiceActive ? "Sprachaktivität" : "Mikrofon bereit"}><i /><i /><span><Icon name="mic" size={32} /></span><i /><i /></div>
          <h2>{agentName}</h2>
          <p>{view.state === "assistant_speaking" ? `${agentName} spricht` : view.state === "user_speaking" ? `${agentName} hört zu` : view.state === "assistant_thinking" ? `${agentName} denkt nach` : stateLabels[view.state]}</p>
          {mode === "presentation" && view.transcript.length > 0 && <div className="presentation-transcript" aria-live="polite">{view.transcript.slice(-2).map((entry) => <p key={entry.id}><strong>{entry.speaker === "user" ? "Du" : agentName}:</strong> {entry.text}</p>)}</div>}
          <div className="call-actions">
            <button className="button primary large" disabled={!canStart} onClick={realtime.start}><Icon name="call" /> {view.state === "ended" ? "Neues Testgespräch" : "Testgespräch starten"}</button>
            <button className="button secondary" disabled={!active} onClick={realtime.end}>Gespräch beenden</button>
            <button className="button ghost" aria-pressed={view.muted} disabled={realtimeE2eStub || !active || view.state === "connecting" || view.state === "requesting_microphone"} onClick={realtime.toggleMute}><Icon name="mic" />{view.muted ? "Mikrofon aktivieren" : "Mikrofon stummschalten"}</button>
          </div>
          <small>Audio und Transkript bleiben flüchtig im Browser und werden von dieser Anwendung nicht gespeichert.</small>
        </section>
        <aside className="session-guide card">
          <div className="section-heading"><h2>Testhinweise</h2><span>WebRTC</span></div>
          <ol>
            <li>Gespräch aktiv per Klick starten und Mikrofon freigeben.</li>
            <li>Natürlich sprechen; Pausen steuern die automatische Antwort.</li>
            <li>Während der Assistent spricht, bleibt das Mikrofon gesperrt; danach wird es automatisch wieder freigegeben.</li>
          </ol>
          <div className="privacy-note"><strong>Kontrollierte Terminwerkzeuge</strong><p>Der Agent erhält nur Terminarten, freie Zeitfenster und das Ergebnis bestätigter Buchungen. Inhalte bestehender Kalendereinträge werden nicht an das Sprachmodell übertragen.</p></div>
          {runtime && <div className="privacy-note"><strong>Wirksame Konfiguration · v{runtime.configuration_version}</strong><p>{runtime.company_name} · {runtime.assistant_name} · Deutsch · {runtime.style} · {runtime.voice} · {runtime.speed.toFixed(2)}× · {runtime.business_hours_status === "open" ? "simuliert geöffnet" : "simuliert geschlossen"} · {runtime.capability_keys.length} Fähigkeiten</p></div>}
        </aside>
      </div>
      {mode === "test" && <div className="grid diagnostic-grid realtime-diagnostics">
        <section className="card transcript-card"><div className="section-heading"><h2>Live-Transkript</h2><div className="section-actions"><span>{view.transcript.length} Nachrichten</span><button className="text-button" disabled={view.transcript.length === 0} onClick={realtime.clearTranscript}>Transkript leeren</button></div></div>
          <div className="transcript-list" ref={transcriptRef} aria-live="polite">
            {view.transcript.length === 0 ? <div className="placeholder-panel"><p>Noch kein Transkript</p><small>Gesprochene Beiträge erscheinen hier nur während dieser Sitzung.</small></div> : view.transcript.map((entry) => <article className={`transcript-entry ${entry.speaker}`} key={entry.id}><div><strong>{entry.speaker === "user" ? "Du" : agentName}</strong><time>{new Date(entry.startedAt).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></div><p>{entry.text}</p><small>{entry.status === "partial" ? "wird transkribiert" : entry.status === "interrupted" ? "unterbrochen" : "vollständig"}</small></article>)}
          </div>
        </section>
        <details className="card event-card" open><summary className="section-heading"><h2>Sitzungsereignisse</h2><span>{view.events.length} Ereignisse</span></summary>
          <div className="event-list">{view.events.length === 0 ? <div className="placeholder-panel"><p>Noch keine Ereignisse</p><small>Es werden höchstens die letzten 40 Ereignisse im Arbeitsspeicher angezeigt.</small></div> : [...view.events].reverse().map((event) => <div className="event-row" key={event.id}><time>{new Date(event.timestamp).toLocaleTimeString("de-DE")}</time><code>{event.type}</code>{event.detail && <small>{event.detail}</small>}</div>)}</div>
        </details>
        <section className="card tool-status"><div className="section-heading"><h2>Werkzeugaufrufe</h2><span>{toolEvents.filter((event) => event.type === "tool_started").length} Aufrufe</span></div>{toolEvents.length === 0 ? <div className="placeholder-panel"><p>Noch keine Werkzeugaufrufe</p><small>Terminwerkzeuge werden ausschließlich bei passenden Gesprächsschritten ausgeführt.</small></div> : <div className="event-list">{[...toolEvents].reverse().map((event) => <div className="event-row" key={event.id}><time>{new Date(event.timestamp).toLocaleTimeString("de-DE")}</time><code>{event.type}</code>{event.detail && <small>{event.detail}</small>}</div>)}</div>}</section>
        <section className="card diagnosis"><div className="section-heading"><h2>Technische Diagnose</h2><span>nicht persistent</span></div><dl>
          <div><dt>Zustand</dt><dd>{stateLabels[view.state]}</dd></div>
          <div><dt>Transport</dt><dd>{active ? (realtimeE2eStub ? "Lokaler Stub" : "WebRTC") : "—"}</dd></div>
          <div><dt>Modell</dt><dd>{data.platformStatus.realtime_model}</dd></div>
          <div><dt>Stimme</dt><dd>{data.platformStatus.realtime_voice}</dd></div>
          <div><dt>Konfiguration</dt><dd>{runtime ? `Version ${runtime.configuration_version}` : "—"}</dd></div>
          <div><dt>Sprechtempo</dt><dd>{runtime ? `${runtime.speed.toFixed(2)}×` : "—"}</dd></div>
          <div><dt>Fähigkeiten</dt><dd>{runtime?.capability_keys.length ?? 0}</dd></div>
          <div><dt>VAD</dt><dd>{view.vadSummary ?? "server_vad · beim Start geladen"}</dd></div>
          <div><dt>Wiedergabe</dt><dd>{view.playbackStatus ? playbackLabels[view.playbackStatus] : "—"}</dd></div>
          <div><dt>Verbindungsaufbau</dt><dd>{metric(view.metrics.connectionMs)}</dd></div>
          <div><dt>Letzte Reaktion</dt><dd>{metric(view.metrics.lastResponseMs)}</dd></div>
          <div><dt>Ø / Min / Max</dt><dd>{metric(view.metrics.averageResponseMs)} / {metric(view.metrics.minimumResponseMs)} / {metric(view.metrics.maximumResponseMs)}</dd></div>
          <div><dt>Latenzmessungen</dt><dd>{view.metrics.responseCount}</dd></div>
          <div><dt>Gesprächsrunden</dt><dd>{view.metrics.completedRounds}</dd></div>
          <div><dt>Sitzungsdauer</dt><dd>{view.metrics.sessionDurationSeconds} s</dd></div>
          <div><dt>Restzeit</dt><dd>{formatRemaining(view.remainingSeconds)}</dd></div>
          <div><dt>Call-ID</dt><dd>{view.callId ? `${view.callId.slice(0, 12)}…` : "—"}</dd></div>
          <div><dt>Audio</dt><dd>{realtimeE2eStub ? "deaktiviert (E2E-Stub)" : view.muted ? "Mikrofon stumm" : active ? "flüchtig aktiv" : "inaktiv"}</dd></div>
        </dl></section>
      </div>}
    </div>;
}
