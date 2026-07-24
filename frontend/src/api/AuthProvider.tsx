import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AuthSession } from "../types/api";
import { api, ApiError } from "./client";

interface AuthState {
  session: AuthSession | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setSession(await api.authSession());
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setSession(null);
      else throw error;
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    api.authSession()
      .then((value) => { if (mounted) setSession(value); })
      .catch(() => { if (mounted) setSession(null); })
      .finally(() => { if (mounted) setLoading(false); });
    const unauthorized = () => setSession(null);
    window.addEventListener("telefonagent:unauthorized", unauthorized);
    return () => {
      mounted = false;
      window.removeEventListener("telefonagent:unauthorized", unauthorized);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setSession(await api.login(username, password));
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* Session is cleared locally even if the backend is unreachable. */ }
    finally { setSession(null); }
  }, []);

  const value = useMemo(
    () => ({ session, loading, login, logout, refresh }),
    [session, loading, login, logout, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth muss innerhalb des AuthProvider verwendet werden.");
  return context;
}

export function useOptionalAuth(): AuthState | null {
  return useContext(AuthContext);
}
