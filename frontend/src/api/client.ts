import type { AccountInvitation, AgentAvailabilityRequest, AgentCatalog, AgentConfiguration, AgentKnowledge, Appointment, AppointmentTypeWrite, AuditEntry, AuthSession, BookingConfiguration, CalendarAgenda, CalendarAppointmentType, CalendarAvailabilityResult, CalendarBookingResult, CalendarConnectionsOverview, CalendarProviderName, CompanyCreate, CompanyDetail, CompanyStatus, CompanySummary, CompanyTelephony, CompanyUser, CompanyUserCreate, CompanyUserInvite, ExternalCalendar, Health, InitialSetupRequest, InitialSetupStatus, InvitationPreview, ManagedUser, ManagedUserUpdate, ManagedUserWrite, PlatformAdmin, PlatformAdminCreate, PlatformDashboard, PlatformStatus, PromptPreview, RealtimeAgentConfig, RealtimeAttemptFinish, RealtimeClientSecret, RealtimeSessionBootstrap, RuntimeSummary, Service, StaffMember, Tenant, TwilioNumber } from "../types/api";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly code?: string,
    public readonly fieldErrors: Record<string, string> = {},
    public readonly details: Record<string, unknown> = {},
  ) { super(message); }
}

function cookieValue(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const match = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

function csrfToken(): string | null {
  return cookieValue("__Host-telefonagent_csrf") ?? cookieValue("telefonagent_csrf");
}

function withoutServerFields<T extends object>(value: T, fields: ReadonlyArray<keyof T>): Partial<T> {
  const excluded = new Set<PropertyKey>(fields);
  return Object.fromEntries(Object.entries(value).filter(([key]) => !excluded.has(key))) as Partial<T>;
}

async function request<T>(path: string, options: { signal?: AbortSignal; method?: "GET" | "POST" | "PUT" | "DELETE"; body?: unknown; suppressUnauthorizedEvent?: boolean; loginRequest?: boolean; keepalive?: boolean } = {}): Promise<T> {
  try {
    const method = options.method ?? "GET";
    const csrf = method !== "GET" && !options.loginRequest ? csrfToken() : null;
    const response = await fetch(`${API_BASE_URL}${path}`, {
      signal: options.signal, method, credentials: "include", keepalive: options.keepalive,
      headers: { Accept: "application/json", ...(options.body === undefined ? {} : { "Content-Type": "application/json" }), ...(csrf ? { "X-CSRF-Token": csrf } : {}), ...(options.loginRequest ? { "X-Requested-With": "Telefonagent" } : {}) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      if (response.status === 401 && !options.suppressUnauthorizedEvent)
        window.dispatchEvent(new Event("telefonagent:unauthorized"));
      const body = await response.json().catch(() => null) as { error?: Record<string, unknown> & { code?: string; message?: string }; detail?: Array<{ loc?: Array<string | number>; msg?: string }> } | null;
      const fieldErrors = Object.fromEntries((body?.detail ?? []).map((item) => [String(item.loc?.slice(1).join(".") ?? "request"), item.msg ?? "Ungültiger Wert"]));
      const details = Object.fromEntries(Object.entries(body?.error ?? {}).filter(([key]) => key !== "code" && key !== "message"));
      throw new ApiError(body?.error?.message ?? (body?.detail ? "Bitte prüfen Sie die markierten Eingaben." : "Die Plattformdaten konnten nicht geladen werden."), response.status, body?.error?.code, fieldErrors, details);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof DOMException && error.name === "AbortError")) throw error;
    throw new ApiError("Das Backend ist derzeit nicht erreichbar. Bitte prüfen Sie den lokalen Start.");
  }
}

export const api = {
  authSession: (signal?: AbortSignal) => request<AuthSession>("/auth/session", { signal, suppressUnauthorizedEvent: true }),
  initialSetupStatus: (signal?: AbortSignal) => request<InitialSetupStatus>("/auth/setup-status", { signal, loginRequest: true, suppressUnauthorizedEvent: true }),
  initialSetup: (value: InitialSetupRequest) => request<AuthSession>("/auth/initial-setup", { method: "POST", body: value, loginRequest: true, suppressUnauthorizedEvent: true }),
  login: (username: string, password: string) => request<AuthSession>("/auth/login", { method: "POST", body: { username, password }, loginRequest: true, suppressUnauthorizedEvent: true }),
  logout: () => request<void>("/auth/logout", { method: "POST", suppressUnauthorizedEvent: true }),
  changePassword: (currentPassword: string, newPassword: string) => request<AuthSession>("/auth/change-password", { method: "POST", body: { current_password: currentPassword, new_password: newPassword } }),
  forgotPassword: (identifier: string) => request<void>("/auth/forgot-password", { method: "POST", body: { identifier }, loginRequest: true, suppressUnauthorizedEvent: true }),
  resetPassword: (token: string, newPassword: string) => request<void>("/auth/reset-password", { method: "POST", body: { token, new_password: newPassword }, loginRequest: true, suppressUnauthorizedEvent: true }),
  invitation: (token: string, signal?: AbortSignal) => request<InvitationPreview>(`/auth/invitations/${encodeURIComponent(token)}`, { signal, loginRequest: true, suppressUnauthorizedEvent: true }),
  acceptInvitation: (token: string, password: string) => request<void>(`/auth/invitations/${encodeURIComponent(token)}`, { method: "POST", body: { password }, loginRequest: true, suppressUnauthorizedEvent: true }),
  selectCompanyContext: (companyId: string) => request<AuthSession>("/auth/context", { method: "POST", body: { company_id: companyId } }),
  clearCompanyContext: () => request<AuthSession>("/auth/context", { method: "DELETE" }),
  platformDashboard: () => request<PlatformDashboard>("/platform/dashboard"),
  companies: (search = "", status = "") => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (status) params.set("status", status);
    const query = params.toString();
    return request<CompanySummary[]>(`/platform/companies${query ? `?${query}` : ""}`);
  },
  company: (id: string) => request<CompanyDetail>(`/platform/companies/${id}`),
  twilioNumbers: () => request<TwilioNumber[]>("/platform/telephony/twilio/numbers"),
  companyTelephony: (id: string) => request<CompanyTelephony>(`/platform/companies/${id}/telephony`),
  assignCompanyTwilioNumber: (id: string, phoneNumber: string, transfer = false) => request<CompanyTelephony>(`/platform/companies/${id}/telephony/twilio`, { method: "PUT", body: { phone_number: phoneNumber, transfer } }),
  removeCompanyTwilioNumber: (id: string) => request<CompanyTelephony>(`/platform/companies/${id}/telephony/twilio`, { method: "DELETE" }),
  syncCompanyTwilioNumber: (id: string) => request<CompanyTelephony>(`/platform/companies/${id}/telephony/twilio/sync`, { method: "POST" }),
  createCompany: (value: CompanyCreate) => request<CompanyDetail>("/platform/companies", { method: "POST", body: value }),
  updateCompanyStatus: (id: string, status: CompanyStatus) => request<CompanyDetail>(`/platform/companies/${id}/status`, { method: "POST", body: { status } }),
  selectPlatformCompany: (id: string) => request<AuthSession>("/auth/context", { method: "POST", body: { company_id: id } }),
  platformCompanyUsers: (id: string) => request<CompanyUser[]>(`/platform/companies/${id}/users`),
  createPlatformCompanyUser: (id: string, value: CompanyUserCreate) => request<CompanyUser>(`/platform/companies/${id}/users`, { method: "POST", body: value }),
  updatePlatformCompanyUser: (companyId: string, userId: string, value: Pick<CompanyUser, "display_name" | "email" | "role" | "is_active">) => request<CompanyUser>(`/platform/companies/${companyId}/users/${userId}`, { method: "PUT", body: value }),
  transferPlatformPrimaryAdmin: (companyId: string, userId: string) => request<CompanyUser>(`/platform/companies/${companyId}/primary-admin`, { method: "POST", body: { user_id: userId } }),
  platformCompanyInvitations: (id: string) => request<AccountInvitation[]>(`/platform/companies/${id}/invitations`),
  invitePlatformCompanyUser: (id: string, value: CompanyUserInvite) => request<AccountInvitation>(`/platform/companies/${id}/invitations`, { method: "POST", body: value }),
  revokePlatformCompanyInvitation: (companyId: string, invitationId: string) => request<AccountInvitation>(`/platform/companies/${companyId}/invitations/${invitationId}`, { method: "DELETE" }),
  ownCompany: () => request<CompanyDetail>("/company"),
  updateOwnCompany: (value: Pick<CompanyDetail, "contact_name" | "contact_email" | "contact_phone" | "timezone">) => request<CompanyDetail>("/company", { method: "PUT", body: value }),
  ownCompanyUsers: () => request<CompanyUser[]>("/company/users"),
  inviteOwnCompanyUser: (value: CompanyUserInvite) => request<AccountInvitation>("/company/invitations", { method: "POST", body: value }),
  updateOwnCompanyUser: (userId: string, value: Pick<CompanyUser, "display_name" | "email" | "role" | "is_active">) => request<CompanyUser>(`/company/users/${userId}`, { method: "PUT", body: value }),
  transferOwnPrimaryAdmin: (userId: string) => request<CompanyUser>("/company/primary-admin", { method: "POST", body: { user_id: userId } }),
  ownCompanyInvitations: () => request<AccountInvitation[]>("/company/invitations"),
  revokeOwnCompanyInvitation: (invitationId: string) => request<AccountInvitation>(`/company/invitations/${invitationId}`, { method: "DELETE" }),
  platformAdmins: () => request<PlatformAdmin[]>("/platform/admins"),
  createPlatformAdmin: (value: PlatformAdminCreate) => request<PlatformAdmin>("/platform/admins", { method: "POST", body: value }),
  invitePlatformAdmin: (value: { username: string; display_name: string; email: string; current_password: string }) => request<AccountInvitation>("/platform/admins/invitations", { method: "POST", body: value }),
  updatePlatformAdmin: (id: string, value: { display_name: string; email: string | null; is_active: boolean; current_password: string }) => request<PlatformAdmin>(`/platform/admins/${id}`, { method: "PUT", body: value }),
  platformAudit: (companyId = "") => request<AuditEntry[]>(`/platform/audit${companyId ? `?company_id=${encodeURIComponent(companyId)}` : ""}`),
  ownCompanyAudit: () => request<AuditEntry[]>("/company/audit"),
  managedUsers: () => request<ManagedUser[]>("/auth/users"),
  createManagedUser: (value: ManagedUserWrite) => request<ManagedUser>("/auth/users", { method: "POST", body: value }),
  updateManagedUser: (id: string, value: ManagedUserUpdate) => request<ManagedUser>(`/auth/users/${id}`, { method: "PUT", body: value }),
  resetManagedUserPassword: (id: string, password: string) => request<void>(`/auth/users/${id}/reset-password`, { method: "POST", body: { password } }),
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
  realtimeSessionBootstrap: (callAttemptId: string, signal?: AbortSignal) => request<RealtimeSessionBootstrap>("/realtime/session-bootstrap", { signal, method: "POST", body: { call_attempt_id: callAttemptId } }),
  realtimeAttemptConnected: (callAttemptId: string) => request<void>(`/realtime/call-attempts/${encodeURIComponent(callAttemptId)}/connected`, { method: "POST" }),
  realtimeAttemptFinish: (callAttemptId: string, value: RealtimeAttemptFinish, keepalive = false) => request<void>(`/realtime/call-attempts/${encodeURIComponent(callAttemptId)}/finish`, { method: "POST", body: value, keepalive, suppressUnauthorizedEvent: keepalive }),
  agentConfiguration: (signal?: AbortSignal) => request<AgentConfiguration>("/agent/config", { signal }),
  saveAgentConfiguration: (value: AgentConfiguration) => request<AgentConfiguration>("/agent/config", { method: "PUT", body: { ...withoutServerFields(value, ["tenant_id", "version", "updated_at", "can_edit", "role"]), expected_version: value.version } }),
  agentKnowledge: (signal?: AbortSignal) => request<AgentKnowledge>("/agent/knowledge", { signal }),
  saveAgentKnowledge: (value: AgentKnowledge) => request<AgentKnowledge>("/agent/knowledge", { method: "PUT", body: { ...withoutServerFields(value, ["tenant_id", "version", "can_edit"]), expected_version: value.version } }),
  agentCatalog: (signal?: AbortSignal) => request<AgentCatalog>("/agent/catalog", { signal }),
  agentTestSession: (signal?: AbortSignal) => request<RuntimeSummary>("/agent/test-session", { signal, method: "POST" }),
  agentPromptPreview: (signal?: AbortSignal) => request<PromptPreview>("/agent/prompt-preview", { signal }),
  voicePreview: async (value: Pick<AgentConfiguration, "pronunciation_style" | "regional_accent" | "pronunciation_instructions"> & { voice: string; speed: number; text: string }) => {
    const csrf = csrfToken();
    const response = await fetch(`${API_BASE_URL}/agent/voice-preview`, { method: "POST", credentials: "include", headers: { Accept: "audio/mpeg", "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) }, body: JSON.stringify(value) });
    if (!response.ok) {
      if (response.status === 401) window.dispatchEvent(new Event("telefonagent:unauthorized"));
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
  resolveBookingDatetime: (value: { session_id: string; tool_call_id: string; expression: string }) => request<{
    status: "concrete" | "search_window" | "clarification_required" | "past" | "out_of_horizon" | "invalid";
    timezone: string;
    start: string | null;
    end: string | null;
    speech: string | null;
    reason: string | null;
    explicit_year: boolean;
    resolution_version: number;
  }>("/calendar/tools/resolve-booking-datetime", { method: "POST", body: value }),
  checkAppointmentAvailability: (value: { session_id: string; tool_call_id: string; appointment_type_id: string }) => request<{ available: boolean; appointment_start: string; appointment_end: string; blocked_start: string; blocked_end: string; slot_id: string | null; reason: string | null; alternatives: Array<{ slot_id: string; start: string; end: string; spoken_date: string; spoken_time: string }>; source: "snapshot" | "targeted_refresh"; preliminary: boolean }>("/calendar/tools/check-appointment-availability/session", { method: "POST", body: value }),
  findAlternativeSlots: (value: { session_id: string; tool_call_id: string; preferred_time_of_day?: "morning" | "afternoon" | "evening"; maximum_results: number }) => request<{ success: boolean; timezone: string; slots: Array<{ slot_id: string; start: string; end: string; spoken_date: string; spoken_time: string }> }>("/calendar/tools/find-alternative-slots", { method: "POST", body: value }),
  selectBookingSlot: (value: { session_id: string; tool_call_id: string; slot_id: string }) => request<Record<string, unknown>>("/calendar/tools/select-booking-slot", { method: "POST", body: value }),
  prepareAppointmentConfirmation: (value: { session_id: string; tool_call_id: string; customer_name: string; customer_phone: string | null; customer_email: string | null }) => request<{ success: boolean; confirmation_version: number; confirmation_digest: string; summary: Record<string, string | null>; state: "awaiting_confirmation" }>("/calendar/tools/prepare-appointment-confirmation", { method: "POST", body: value }),
  finalizeAppointmentBooking: (value: { session_id: string; tool_call_id: string; confirmation_version: number; confirmation_utterance: string }) => request<CalendarBookingResult>("/calendar/tools/finalize-appointment-booking", { method: "POST", body: value }),
  findAvailableAppointments: (value: AgentAvailabilityRequest) => request<CalendarAvailabilityResult>("/calendar/tools/find-available-appointments", { method: "POST", body: value }),
  createCalendarBooking: (value: { slot_id: string; appointment_type_id: string; customer_name: string; customer_phone: string; customer_email: string; customer_notes: string; idempotency_key: string }) => request<CalendarBookingResult>("/calendar/tools/create-calendar-booking", { method: "POST", body: value }),
};

