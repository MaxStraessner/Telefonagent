import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { GmailNotificationSettings, MailSection } from "../types/api";
import { accountErrorMessage, PageHeader } from "./shared";

const sections: [MailSection, string][] = [
  ["caller", "Anrufername und Telefonnummer"],
  ["time", "Datum, Uhrzeit und Gesprächsdauer"],
  ["summary", "KI-Zusammenfassung"],
  ["concern", "Anliegen und Dringlichkeit"],
  ["tasks", "Rückrufbedarf und offene Aufgaben"],
  ["transcript", "Vollständiges Gesprächstranskript"],
  ["cost", "Telefoniekosten, sofern verfügbar"],
];

export function NotificationsPage() {
  const [params, setParams] = useSearchParams();
  const [profile, setProfile] = useState<GmailNotificationSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const result = params.get("gmail");
    if (!result) return;
    if (result === "connected") setMessage("Google-Konto verbunden.");
    else setError("Die Google-Verbindung konnte nicht hergestellt werden. Starten Sie die Verbindung erneut.");
    const next = new URLSearchParams(params);
    next.delete("gmail");
    setParams(next, { replace: true });
  }, [params, setParams]);

  useEffect(() => {
    const controller = new AbortController();
    api.gmailNotifications(controller.signal)
      .then(setProfile)
      .catch((cause) => {
        if (!controller.signal.aborted) setError(accountErrorMessage(cause, "Die Einstellungen konnten nicht geladen werden."));
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!profile || busy) return;
    const connect = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "connect";
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const saved = await api.saveGmailNotifications({
        enabled: profile.enabled,
        recipient: profile.recipient?.trim() || null,
        sections: profile.sections,
      });
      setProfile(saved);
      if (connect) {
        const start = await api.connectGmail(!saved.connected || saved.enabled);
        window.location.assign(start.authorization_url);
      } else {
        setMessage("Benachrichtigungseinstellungen gespeichert.");
      }
    } catch (cause) {
      setError(accountErrorMessage(cause, "Die Einstellungen konnten nicht gespeichert werden."));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await api.disconnectGmail();
      setProfile(await api.gmailNotifications());
      setMessage("Google-Konto getrennt.");
    } catch (cause) {
      setError(accountErrorMessage(cause, "Das Google-Konto konnte nicht getrennt werden."));
    } finally {
      setBusy(false);
    }
  }

  return <div className="page">
    <PageHeader title="Benachrichtigungen" description="Lassen Sie sich nach Telefonaten einen Gesprächsbericht über Gmail senden." />
    {error && <p className="form-error" role="alert">{error}</p>}
    {message && <p className="form-success" role="status">{message}</p>}
    <section className="card detail-card">
      <h2>Gmail</h2>
      {loading && <p role="status">Einstellungen werden geladen …</p>}
      {!profile && !loading && <button className="button secondary" onClick={() => { setLoading(true); setError(null); setAttempt(attempt + 1); }}>Erneut laden</button>}
      {profile && <form className="settings-form" onSubmit={(event) => void submit(event)}>
        <p>{profile.connected ? "Google-Konto verbunden" : "Mit Ihrem persönlichen Google-Konto verbinden. Telefonagent erhält die Berechtigung zum Senden von E-Mails."}</p>
        {!profile.available && <p>Google OAuth ist auf dem Server noch nicht eingerichtet.</p>}
        {profile.active_elsewhere && <p>Ein anderes Benutzerkonto versendet die Berichte für dieses Unternehmen. Wenn Sie den Versand hier aktivieren, übernimmt Ihr Konto.</p>}
        <fieldset className="form-section" disabled={busy}>
          <legend>Versand</legend>
          <label className="form-field">Empfängeradresse
            <input type="email" maxLength={254} value={profile.recipient ?? ""} required={profile.enabled}
              onChange={(event) => setProfile({ ...profile, recipient: event.target.value })} />
          </label>
          {profile.connected && <label className="agent-toggle">
            <input type="checkbox" checked={profile.enabled}
              onChange={(event) => setProfile({ ...profile, enabled: event.target.checked })} />
            <span>Automatischer Versand nach Telefonaten</span>
          </label>}
        </fieldset>
        <fieldset className="form-section" disabled={busy}>
          <legend>Inhalte der E-Mail</legend>
          {sections.map(([key, label]) => <label className="agent-toggle" key={key}>
            <input type="checkbox" checked={profile.sections.includes(key)} onChange={(event) => {
              const selected = event.target.checked
                ? sections.map(([section]) => section).filter((section) => section === key || profile.sections.includes(section))
                : profile.sections.filter((section) => section !== key);
              setProfile({ ...profile, sections: selected });
            }} />
            <span>{label}</span>
          </label>)}
          <small>E-Mails können personenbezogene Gesprächsdaten enthalten. Wählen Sie eine Empfängeradresse mit entsprechendem Zugriffsschutz.</small>
        </fieldset>
        {!profile.connected && <p>Nach der Google-Freigabe wird der Versand an die eingetragene Adresse aktiviert.</p>}
        <div className="call-detail-actions">
          <button className="button primary" type="submit" value="save" disabled={busy || profile.sections.length === 0}>Einstellungen speichern</button>
          <button className="button secondary" type="submit" value="connect"
            disabled={busy || !profile.available || !profile.recipient?.trim() || profile.sections.length === 0}>
            {profile.connected ? "Google erneut verbinden" : "Mit Google verbinden"}
          </button>
          {profile.connected && <button className="button secondary" type="button" disabled={busy} onClick={() => void disconnect()}>Google-Konto trennen</button>}
        </div>
      </form>}
    </section>
  </div>;
}
