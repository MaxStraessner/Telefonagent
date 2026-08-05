import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { ApiError } from "../api/client";

export function PasswordChangePage() {
  const { session, changePassword, logout } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!session) return <Navigate to="/login" replace />;
  if (!session.user.must_change_password) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirmation) { setError("Die Passwörter stimmen nicht überein."); return; }
    setSubmitting(true); setError(null);
    try { await changePassword(currentPassword, password); navigate("/", { replace: true }); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Das Passwort konnte nicht geändert werden."); }
    finally { setSubmitting(false); }
  }

  return <main className="login-page"><section className="login-card">
    <div className="brand login-brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Sicherheitsanforderung</small></div></div>
    <h1>Passwort ändern</h1><p>Ihr vorläufiges Passwort muss vor der weiteren Nutzung ersetzt werden.</p>
    <form onSubmit={submit}>
      <label>Aktuelles Passwort<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required autoFocus /></label>
      <label>Neues Passwort<input type="password" autoComplete="new-password" minLength={15} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
      <label>Passwort wiederholen<input type="password" autoComplete="new-password" minLength={15} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
      {error && <div className="login-error" role="alert">{error}</div>}
      <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Passwort wird geändert …" : "Sicheres Passwort setzen"}</button>
      <button className="button ghost" type="button" onClick={() => void logout()}>Abmelden</button>
    </form>
  </section></main>;
}
