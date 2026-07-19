import type { Appointment, Health, PlatformStatus, Service, StaffMember, Tenant } from "../types/api";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) { super(message); }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { signal, headers: { Accept: "application/json" } });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
      throw new ApiError(body?.error?.message ?? "Die Plattformdaten konnten nicht geladen werden.", response.status);
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof DOMException && error.name === "AbortError")) throw error;
    throw new ApiError("Das Backend ist derzeit nicht erreichbar. Bitte prüfen Sie den lokalen Start.");
  }
}

export const api = {
  tenant: (signal?: AbortSignal) => request<Tenant>("/tenant", signal),
  services: (signal?: AbortSignal) => request<Service[]>("/services", signal),
  staff: (signal?: AbortSignal) => request<StaffMember[]>("/staff", signal),
  appointments: (signal?: AbortSignal) => request<Appointment[]>("/appointments", signal),
  status: (signal?: AbortSignal) => request<PlatformStatus>("/platform/status", signal),
  health: (signal?: AbortSignal) => request<Health>("/health", signal),
};

