import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";
import { api, ApiError } from "../api/client";

export function ForgotPasswordPage() {
  const { session } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (session) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.forgotPassword(identifier);
      setSent(true);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Die Anfrage ist derzeit nicht möglich.");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="login-page"><section className="login-card">
    <div className="brand login-brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Kontowiederherstellung</small></div></div>
    <h1>Passwort vergessen</h1>
    {sent ? <div className="auth-success" role="status">Falls ein aktives Konto existiert, wurde ein zeitlich begrenzter Link versendet.</div> : <>
      <p>Geben Sie Ihren Benutzernamen oder Ihre E-Mail-Adresse ein.</p>
      <form onSubmit={submit}>
        <label>Benutzername oder E-Mail<input autoComplete="username" value={identifier} onChange={(event) => setIdentifier(event.target.value)} required autoFocus /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Anfrage läuft …" : "Link anfordern"}</button>
      </form>
    </>}
    <p className="setup-link"><Link to="/login">Zurück zur Anmeldung</Link></p>
  </section></main>;
}
