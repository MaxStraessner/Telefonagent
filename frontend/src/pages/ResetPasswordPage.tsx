import { useState, type FormEvent } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";

export function ResetPasswordPage() {
  const { session } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (session) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirmation) { setError("Die Passwörter stimmen nicht überein."); return; }
    setSubmitting(true); setError(null);
    try { await api.resetPassword(token, password); setComplete(true); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Das Passwort konnte nicht geändert werden."); }
    finally { setSubmitting(false); }
  }

  return <main className="login-page"><section className="login-card">
    <div className="brand login-brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Neues Passwort</small></div></div>
    <h1>Passwort zurücksetzen</h1>
    {!token ? <div className="login-error" role="alert">Der Link enthält kein gültiges Token.</div> : complete ? <div className="auth-success" role="status">Das Passwort wurde geändert. Sie können sich jetzt anmelden.</div> : <form onSubmit={submit}>
      <label>Neues Passwort<input type="password" autoComplete="new-password" minLength={15} value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus /></label>
      <label>Passwort wiederholen<input type="password" autoComplete="new-password" minLength={15} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
      {error && <div className="login-error" role="alert">{error}</div>}
      <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Passwort wird geändert …" : "Passwort ändern"}</button>
    </form>}
    <p className="setup-link"><Link to="/login">Zur Anmeldung</Link></p>
  </section></main>;
}
