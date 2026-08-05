import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AuthSession, InitialSetupRequest } from "../types/api";
import { api, ApiError } from "./client";

interface AuthState {
  session: AuthSession | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  initialSetup: (value: InitialSetupRequest) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  selectCompanyContext: (companyId: string) => Promise<void>;
  clearCompanyContext: () => Promise<void>;
  refresh: () => Promise<void>;
  setupAvailable: boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [setupAvailable, setSetupAvailable] = useState(false);

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
      .then((value) => { if (mounted) { setSession(value); setSetupAvailable(false); } })
      .catch(async () => {
        if (!mounted) return;
        setSession(null);
        try {
          const setup = await api.initialSetupStatus();
          if (mounted) setSetupAvailable(setup.available);
        } catch {
          if (mounted) setSetupAvailable(false);
        }
      })
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
    setSetupAvailable(false);
  }, []);

  const initialSetup = useCallback(async (value: InitialSetupRequest) => {
    setSession(await api.initialSetup(value));
    setSetupAvailable(false);
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* Session is cleared locally even if the backend is unreachable. */ }
    finally { setSession(null); }
  }, []);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    setSession(await api.changePassword(currentPassword, newPassword));
  }, []);

  const selectCompanyContext = useCallback(async (companyId: string) => {
    setSession(await api.selectCompanyContext(companyId));
  }, []);

  const clearCompanyContext = useCallback(async () => {
    setSession(await api.clearCompanyContext());
  }, []);

  const value = useMemo(
    () => ({ session, loading, login, initialSetup, logout, changePassword, selectCompanyContext, clearCompanyContext, refresh, setupAvailable }),
    [session, loading, login, initialSetup, logout, changePassword, selectCompanyContext, clearCompanyContext, refresh, setupAvailable],
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
