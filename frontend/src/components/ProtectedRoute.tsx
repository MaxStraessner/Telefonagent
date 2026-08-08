import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../api/AuthProvider";

export function ProtectedRoute({ children, allowPasswordChange = false }: { children: ReactNode; allowPasswordChange?: boolean }) {
  const { session, loading } = useAuth();
  const location = useLocation();
  if (loading) return <main className="auth-loading" aria-live="polite">Sitzung wird geprüft …</main>;
  if (!session)
    return <Navigate to="/login" replace state={{ returnTo: `${location.pathname}${location.search}` }} />;
  if (session.user.must_change_password && !allowPasswordChange)
    return <Navigate to="/passwort-aendern" replace />;
  return children;
}
