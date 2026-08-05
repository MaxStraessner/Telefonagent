import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";
import type { InvitationPreview } from "../types/api";

export function InvitationAcceptPage() {
  const { session } = useAuth();
  const { token = "" } = useParams();
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.invitation(token, controller.signal).then(setPreview).catch((reason) => {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof ApiError ? reason.message : "Die Einladung konnte nicht geladen werden.");
    }).finally(() => setLoading(false));
    return () => controller.abort();
  }, [token]);
  if (session) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirmation) { setError("Die Passwörter stimmen nicht überein."); return; }
    setSubmitting(true); setError(null);
    try { await api.acceptInvitation(token, password); setComplete(true); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Die Einladung konnte nicht angenommen werden."); }
    finally { setSubmitting(false); }
  }

  return <main className="login-page"><section className="login-card">
    <div className="brand login-brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Sichere Einladung</small></div></div>
    <h1>Einladung annehmen</h1>
    {loading ? <p aria-live="polite">Einladung wird geprüft …</p> : complete ? <div className="auth-success" role="status">Ihr Konto ist bereit.</div> : preview ? <>
      <p>{preview.company_name ? `Sie wurden zu ${preview.company_name} eingeladen.` : "Sie wurden zur Plattformverwaltung eingeladen."}</p>
      <dl className="invitation-summary"><div><dt>Name</dt><dd>{preview.display_name}</dd></div><div><dt>E-Mail</dt><dd>{preview.email}</dd></div></dl>
      <form onSubmit={submit}>
        <label>Passwort festlegen<input type="password" autoComplete="new-password" minLength={15} value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus /></label>
        <label>Passwort wiederholen<input type="password" autoComplete="new-password" minLength={15} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Einladung wird angenommen …" : "Einladung annehmen"}</button>
      </form>
    </> : <div className="login-error" role="alert">{error ?? "Die Einladung ist ungültig oder abgelaufen."}</div>}
    <p className="setup-link"><Link to="/login">Zur Anmeldung</Link></p>
  </section></main>;
}
