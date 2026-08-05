import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { ApiError } from "../api/client";

function safeReturnTo(value: unknown): string {
  return typeof value === "string" && value.startsWith("/") && !value.startsWith("//")
    ? value
    : "/";
}

export function LoginPage() {
  const { session, loading, login, setupAvailable } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session) return <Navigate to="/" replace />;
  if (loading) return <main className="auth-loading" aria-live="polite">Sitzung wird geprüft …</main>;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      const state = location.state as { returnTo?: unknown } | null;
      navigate(safeReturnTo(state?.returnTo), { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Die Anmeldung ist derzeit nicht möglich.");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="login-page">
    <section className="login-card">
      <div className="brand login-brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Sichere Anmeldung</small></div></div>
      <h1>Willkommen zurück</h1>
      <p>Melden Sie sich mit Ihrem persönlichen Unternehmenskonto an.</p>
      <form onSubmit={submit}>
        <label>Benutzername<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus /></label>
        <label>Passwort<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Anmeldung läuft …" : "Anmelden"}</button>
      </form>
      {setupAvailable && <p className="setup-link">Noch kein Zugang eingerichtet? <Link to="/einrichtung">Ersteinrichtung starten</Link></p>}
    </section>
  </main>;
}
