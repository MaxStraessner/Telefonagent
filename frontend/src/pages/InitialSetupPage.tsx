import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../api/AuthProvider";
import { ApiError } from "../api/client";

function defaultTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Berlin";
}

export function InitialSetupPage() {
  const { session, loading, setupAvailable, initialSetup } = useAuth();
  const navigate = useNavigate();
  const [companyName, setCompanyName] = useState("");
  const [industry, setIndustry] = useState("");
  const [timezone, setTimezone] = useState(defaultTimezone);
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [setupCode, setSetupCode] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session) return <Navigate to="/" replace />;
  if (loading) return <main className="auth-loading" aria-live="polite">Einrichtung wird geprüft …</main>;
  if (!setupAvailable) return <Navigate to="/login" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== passwordConfirmation) {
      setError("Die Passwörter stimmen nicht überein.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await initialSetup({
        setup_code: setupCode,
        company_name: companyName,
        industry,
        timezone,
        display_name: displayName,
        username,
        email: email.trim() || null,
        password,
      });
      navigate("/", { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Die Einrichtung ist derzeit nicht möglich.");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="login-page">
    <section className="login-card setup-card">
      <div className="brand login-brand"><span className="brand-mark">T</span><div><strong>Telefonagent</strong><small>Einmalige Ersteinrichtung</small></div></div>
      <h1>Unternehmen einrichten</h1>
      <p>Erstelle dein Unternehmen und den ersten Owner-Zugang. Der Einrichtungscode wird nicht gespeichert.</p>
      <form onSubmit={submit}>
        <label>Einrichtungscode<input type="password" autoComplete="off" value={setupCode} onChange={(event) => setSetupCode(event.target.value)} required autoFocus /></label>
        <label>Unternehmensname<input value={companyName} onChange={(event) => setCompanyName(event.target.value)} required /></label>
        <label>Branche<input value={industry} onChange={(event) => setIndustry(event.target.value)} required placeholder="z. B. Dienstleistung" /></label>
        <label>Zeitzone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} required placeholder="Europe/Berlin" /></label>
        <label>Dein Name<input autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
        <label>Benutzername<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>E-Mail-Adresse <small>(optional)</small><input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label>Passwort<input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={15} required /></label>
        <label>Passwort wiederholen<input type="password" autoComplete="new-password" value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} minLength={15} required /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Einrichtung läuft …" : "Unternehmen einrichten"}</button>
      </form>
    </section>
  </main>;
}
