import type { AgentAvailabilityRequest, AgentCatalog, AgentConfiguration, AgentKnowledge, AppliedRealtimeConfiguration, Appointment, AppointmentTypeWrite, BookingConfiguration, CalendarAgenda, CalendarAppointmentType, CalendarAvailabilityResult, CalendarBookingResult, CalendarConnectionsOverview, CalendarProviderName, ExternalCalendar, Health, PlatformStatus, PromptPreview, RealtimeAgentConfig, RealtimeClientSecret, RealtimeSessionBootstrap, RuntimeConfigurationDiff, RuntimeSummary, Service, StaffMember, Tenant } from "../types/api";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number, public readonly code?: string, public readonly fieldErrors: Record<string, string> = {}) { super(message); }
}

async function request<T>(path: string, options: { signal?: AbortSignal; method?: "GET" | "POST" | "PUT" | "DELETE"; body?: unknown } = {}): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      signal: options.signal, method: options.method ?? "GET",
      headers: { Accept: "application/json", ...(options.body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string }; detail?: Array<{ loc?: Array<string | number>; msg?: string }> } | null;
      const fieldErrors = Object.fromEntries((body?.detail ?? []).map((item) => [String(item.loc?.slice(1).join(".") ?? "request"), item.msg ?? "Ungültiger Wert"]));
      throw new ApiError(body?.error?.message ?? (body?.detail ? "Bitte prüfen Sie die markierten Eingaben." : "Die Plattformdaten konnten nicht geladen werden."), response.status, body?.error?.code, fieldErrors);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof DOMException && error.name === "AbortError")) throw error;
    throw new ApiError("Das Backend ist derzeit nicht erreichbar. Bitte prüfen Sie den lokalen Start.");
  }
}

export const api = {
  tenant: (signal?: AbortSignal) => request<Tenant>("/tenant", { signal }),
  services: (signal?: AbortSignal) => request<Service[]>("/services", { signal }),
  createService: (value: Omit<Service, "id">) => request<Service>("/services", { method: "POST", body: value }),
  updateService: (id: string, value: Omit<Service, "id">) => request<Service>(`/services/${id}`, { method: "PUT", body: value }),
  staff: (signal?: AbortSignal) => request<StaffMember[]>("/staff", { signal }),
  appointments: (signal?: AbortSignal) => request<Appointment[]>("/appointments", { signal }),
  calendarAgenda: (start: string, end: string, signal?: AbortSignal) => request<CalendarAgenda>(`/calendar/appointments?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, { signal }),
  status: (signal?: AbortSignal) => request<PlatformStatus>("/platform/status", { signal }),
  health: (signal?: AbortSignal) => request<Health>("/health", { signal }),
  realtimeAgentConfig: (signal?: AbortSignal) => request<RealtimeAgentConfig>("/realtime/agent-config", { signal }),
  realtimeClientSecret: (signal?: AbortSignal) => request<RealtimeClientSecret>("/realtime/client-secret", { signal, method: "POST" }),
  realtimeSessionBootstrap: (signal?: AbortSignal) => request<RealtimeSessionBootstrap>("/realtime/session-bootstrap", { signal, method: "POST" }),
  reportAppliedRealtimeConfiguration: (
    sessionId: string,
    manifestDigest: string,
    applied: AppliedRealtimeConfiguration,
  ) => request<RuntimeConfigurationDiff>(`/realtime/sessions/${sessionId}/applied-configuration`, {
    method: "POST",
    body: { manifest_digest: manifestDigest, applied },
  }),
  realtimeRuntimeDiff: (sessionId: string, signal?: AbortSignal) => request<RuntimeConfigurationDiff>(`/realtime/sessions/${sessionId}/runtime-diff`, { signal }),
  agentConfiguration: (signal?: AbortSignal) => request<AgentConfiguration>("/agent/config", { signal }),
  saveAgentConfiguration: (value: AgentConfiguration) => request<AgentConfiguration>("/agent/config", { method: "PUT", body: { ...value, expected_version: value.version } }),
  agentKnowledge: (signal?: AbortSignal) => request<AgentKnowledge>("/agent/knowledge", { signal }),
  saveAgentKnowledge: (value: AgentKnowledge) => request<AgentKnowledge>("/agent/knowledge", { method: "PUT", body: { ...value, expected_version: value.version } }),
  agentCatalog: (signal?: AbortSignal) => request<AgentCatalog>("/agent/catalog", { signal }),
  agentTestSession: (signal?: AbortSignal) => request<RuntimeSummary>("/agent/test-session", { signal, method: "POST" }),
  agentPromptPreview: (signal?: AbortSignal) => request<PromptPreview>("/agent/prompt-preview", { signal }),
  voicePreview: async (value: Pick<AgentConfiguration, "pronunciation_style" | "regional_accent" | "pronunciation_instructions"> & { voice: string; speed: number; text: string }) => {
    const response = await fetch(`${API_BASE_URL}/agent/voice-preview`, { method: "POST", headers: { Accept: "audio/mpeg", "Content-Type": "application/json" }, body: JSON.stringify(value) });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
      throw new ApiError(body?.error?.message ?? "Die Stimmprobe konnte nicht geladen werden.", response.status, body?.error?.code);
    }
    return response.blob();
  },
  calendarConnections: (signal?: AbortSignal) => request<CalendarConnectionsOverview>("/calendar/connections", { signal }),
  startCalendarOAuth: (provider: CalendarProviderName) => request<{ authorization_url: string; expires_at: string }>(`/calendar/oauth/${provider}/start`, { method: "POST" }),
  testCalendarConnection: (connectionId: string) => request<{ success: boolean; calendars_found: number; availability_calendars_read: number }>(`/calendar/connections/${connectionId}/test`, { method: "POST" }),
  disconnectCalendar: (connectionId: string) => request<void>(`/calendar/connections/${connectionId}`, { method: "DELETE" }),
  refreshCalendars: (connectionId: string) => request<ExternalCalendar[]>(`/calendar/connections/${connectionId}/calendars`),
  saveCalendarSelection: (calendars: Array<Pick<ExternalCalendar, "id" | "is_selected_for_availability" | "is_selected_for_booking">>) => request<ExternalCalendar[]>("/calendar/configuration/calendars", { method: "PUT", body: { calendars: calendars.map((item) => ({ calendar_id: item.id, is_selected_for_availability: item.is_selected_for_availability, is_selected_for_booking: item.is_selected_for_booking })) } }),
  bookingConfiguration: (signal?: AbortSignal) => request<BookingConfiguration>("/calendar/configuration", { signal }),
  saveBookingConfiguration: (value: BookingConfiguration) => request<BookingConfiguration>("/calendar/configuration", { method: "PUT", body: { timezone: value.timezone, slot_interval_minutes: value.slot_interval_minutes, minimum_notice_minutes: value.minimum_notice_minutes, maximum_booking_horizon_days: value.maximum_booking_horizon_days, buffer_before_minutes: value.buffer_before_minutes, buffer_after_minutes: value.buffer_after_minutes, maximum_suggestions_per_request: value.maximum_suggestions_per_request, business_hours: value.business_hours } }),
  appointmentTypes: (signal?: AbortSignal) => request<CalendarAppointmentType[]>("/calendar/appointment-types", { signal }),
  createAppointmentType: (value: AppointmentTypeWrite) => request<CalendarAppointmentType>("/calendar/appointment-types", { method: "POST", body: value }),
  updateAppointmentType: (id: string, value: AppointmentTypeWrite) => request<CalendarAppointmentType>(`/calendar/appointment-types/${id}`, { method: "PUT", body: value }),
  deleteAppointmentType: (id: string) => request<void>(`/calendar/appointment-types/${id}`, { method: "DELETE" }),
  bootstrapBookingConversation: (session_id: string) => request<{ success: boolean; state: string; snapshot_status: "ready" | "unavailable"; error_code: string | null }>("/calendar/conversation/bootstrap", { method: "POST", body: { session_id } }),
  listBookableServices: (session_id: string, tool_call_id: string) => request<{ success: boolean; services: Array<{ service_id: string; name: string; description: string; duration_minutes: number; appointment_types: Array<{ appointment_type_id: string; appointment_format: string; location: string; buffer_before_minutes: number; buffer_after_minutes: number }> }> }>("/calendar/tools/list-bookable-services", { method: "POST", body: { session_id, tool_call_id } }),
  resolveService: (value: { session_id: string; tool_call_id: string; service_name: string }) => request<Record<string, unknown>>("/calendar/tools/resolve-service", { method: "POST", body: value }),
  checkAppointmentAvailability: (value: { session_id: string; tool_call_id: string; service_id: string; appointment_type_id: string; requested_start: string; timezone: string }) => request<{ available: boolean; appointment_start: string; appointment_end: string; blocked_start: string; blocked_end: string; slot_id: string | null; reason: string | null; alternatives: Array<{ start: string; end: string }>; source: "snapshot" | "targeted_refresh"; preliminary: boolean }>("/calendar/tools/check-appointment-availability/session", { method: "POST", body: value }),
  findAlternativeSlots: (value: { session_id: string; tool_call_id: string; service_id: string; appointment_type_id: string; search_start: string; search_days: number; preferred_day?: string; preferred_time_of_day?: "morning" | "afternoon" | "evening"; maximum_results: number }) => request<{ success: boolean; timezone: string; slots: Array<{ slot_id: string; start: string; end: string; spoken_date: string; spoken_time: string }> }>("/calendar/tools/find-alternative-slots", { method: "POST", body: value }),
  finalizeAppointmentBooking: (value: { session_id: string; tool_call_id: string; service_id: string; appointment_type_id: string; customer_name: string; customer_phone: string | null; customer_email: string | null; start_at: string; timezone: string; confirmation_version: number; confirmation_utterance: string; confirmed: true }) => request<CalendarBookingResult>("/calendar/tools/finalize-appointment-booking", { method: "POST", body: value }),
  findAvailableAppointments: (value: AgentAvailabilityRequest) => request<CalendarAvailabilityResult>("/calendar/tools/find-available-appointments", { method: "POST", body: value }),
  createCalendarBooking: (value: { slot_id: string; appointment_type_id: string; customer_name: string; customer_phone: string; customer_email: string; customer_notes: string; idempotency_key: string }) => request<CalendarBookingResult>("/calendar/tools/create-calendar-booking", { method: "POST", body: value }),
};

