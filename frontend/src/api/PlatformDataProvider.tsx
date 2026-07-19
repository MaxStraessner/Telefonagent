import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "./client";
import type { PlatformData } from "../types/api";

interface DataState { data: PlatformData | null; loading: boolean; error: string | null; retry: () => void; }
const PlatformDataContext = createContext<DataState | null>(null);

export function PlatformDataProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<PlatformData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    Promise.all([
      api.tenant(controller.signal), api.services(controller.signal), api.staff(controller.signal),
      api.appointments(controller.signal), api.status(controller.signal), api.health(controller.signal),
    ]).then(([tenant, services, staff, appointments, platformStatus, health]) => {
      setData({ tenant, services, staff, appointments, platformStatus, health });
    }).catch((reason: unknown) => {
      if (!(reason instanceof DOMException && reason.name === "AbortError"))
        setError(reason instanceof ApiError ? reason.message : "Ein unerwarteter Fehler ist aufgetreten.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  return <PlatformDataContext.Provider value={{ data, loading, error, retry }}>{children}</PlatformDataContext.Provider>;
}

export function usePlatformData(): DataState {
  const context = useContext(PlatformDataContext);
  if (!context) throw new Error("usePlatformData muss innerhalb des PlatformDataProvider verwendet werden.");
  return context;
}

