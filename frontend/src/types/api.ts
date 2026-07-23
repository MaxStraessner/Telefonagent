export interface TenantSettings {
  assistant_name: string;
  default_language: string;
  welcome_message: string;
  presentation_mode_enabled: boolean;
  diagnostics_enabled: boolean;
}

export interface Location {
  id: string;
  name: string;
  street: string;
  postal_code: string;
  city: string;
  country_code: string;
  timezone: string;
  is_primary: boolean;
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  industry: string;
  timezone: string;
  status: "draft" | "active" | "inactive";
  settings: TenantSettings;
  primary_location: Location | null;
}

export interface Service { id: string; name: string; description: string; duration_minutes: number; is_active: boolean; }
export interface StaffMember { id: string; display_name: string; role_name: string; is_active: boolean; }
export interface Appointment {
  id: string; customer_name: string; starts_at: string; ends_at: string;
  status: "pending" | "confirmed" | "cancelled" | "completed";
  source: "web_test" | "voice_agent" | "manual" | "external_calendar";
  service: Service | null; staff_member: StaffMember | null;
}
export interface PlatformStatus {
  environment: string; backend_version: string; realtime_voice_configured: boolean;
  telephony_configured: boolean; calendar_configured: boolean; database_connected: boolean;
  realtime_model: string; realtime_voice: string;
}
export interface RealtimeVadConfig {
  type: "server_vad" | "semantic_vad"; threshold: number | null; prefix_padding_ms: number | null; silence_duration_ms: number | null;
  eagerness: "low" | "medium" | "high" | null;
  create_response: boolean; interrupt_response: boolean;
}
export interface RealtimeAgentConfig {
  tenant_id: string; tenant_name: string; assistant_name: string; language: string;
  welcome_message: string; instructions: string; model: string; voice: string;
  speed: number; configuration_version: number; capability_keys: string[]; tool_names: string[];
  maximum_session_minutes: number; max_output_tokens: number; transcription_enabled: boolean; raw_event_logging: boolean;
  vad: RealtimeVadConfig;
}
export interface RealtimeClientSecret {
  client_secret: string; expires_at: number; session_id: string | null; model: string; voice: string;
  speed: number; configuration_version: number; call_session_id: string; tenant_id: string;
}
export interface RuntimeToolDefinition {
  type: "function";
  name: string;
  description: string;
  parameters: {
    type: "object";
    properties: Record<string, unknown>;
    required: string[];
    additionalProperties: false;
  };
}
export interface RuntimeRecoveryPolicy {
  continuation_ack_timeout_ms: number;
  recovery_response_timeout_ms: number;
  maximum_attempts_per_turn: number;
}
export interface RuntimeManifest {
  schema_version: string; digest: string; tenant_id: string; timezone: string;
  assistant_name: string; language: string; welcome_message: string;
  instructions: string; prompt_digest: string; model: string; voice: string;
  speed: number; configuration_version: number; source_digests: Record<string, string>;
  capability_keys: string[]; tools: RuntimeToolDefinition[]; tool_names: string[];
  tools_digest: string; maximum_session_minutes: number; max_output_tokens: number;
  transcription_enabled: boolean; raw_event_logging: boolean; vad: RealtimeVadConfig;
  recovery: RuntimeRecoveryPolicy;
  setting_targets: Record<string, "prompt" | "session" | "tools" | "ui_only">;
}
export interface RealtimeSessionBootstrap {
  secret: RealtimeClientSecret;
  manifest: RuntimeManifest;
}
export interface AppliedRealtimeConfiguration {
  model?: string; voice?: string; speed?: number; language?: string;
  prompt_digest?: string; tool_names?: string[]; tools_digest?: string;
  vad?: Record<string, unknown>;
}
export interface RuntimeConfigurationDiff {
  session_id: string; status: "pending" | "applied" | "mismatch";
  manifest_digest: string; expected: AppliedRealtimeConfiguration;
  applied: AppliedRealtimeConfiguration | null;
  differences: Record<string, { expected: unknown; actual: unknown }>;
  unobserved: string[];
}
export interface Health { status: string; database: string; }
export interface PlatformData {
  tenant: Tenant; services: Service[]; staff: StaffMember[]; appointments: Appointment[];
  platformStatus: PlatformStatus; health: Health;
}

export interface AgentListItem { id?: string | null; is_active: boolean; sort_order: number; }
export interface AgentTopic extends AgentListItem { label: string; instructions: string; topic_type: "allowed" | "forbidden"; }
export interface AgentRule extends AgentListItem { rule_text: string; }
export interface AgentConfiguration {
  tenant_id: string; version: number; updated_at: string; can_edit: boolean; role: "owner" | "admin" | "member";
  company_name: string; assistant_name: string; assistant_role: string; transparency_notice: string;
  address_formality: "formal" | "informal"; language: "de";
  standard_greeting: string; outside_hours_greeting: string; test_greeting: string; farewell: string;
  voice: string; speech_speed: number; pronunciation_instructions: string; pronunciation_style: "neutral" | "regional" | "custom"; regional_accent: "" | "north_german" | "westphalian" | "rhineland" | "south_german";
  tone: "professional_binding" | "friendly_service" | "calm_empathic" | "relaxed_personal" | "concise_factual" | "custom"; custom_style_instructions: string;
  response_length: "very_short" | "short" | "balanced" | "detailed"; question_style: "one_at_a_time" | "natural";
  turn_detection_type: "server_vad" | "semantic_vad"; turn_eagerness: "low" | "medium" | "high";
  vad_threshold: number; prefix_padding_ms: number; silence_duration_ms: number; interruptions_enabled: boolean; idle_prompt_enabled: boolean; idle_timeout_ms: number;
  primary_task: string; off_topic_behavior: string; off_topic_mode: "strict" | "brief_redirect" | "limited_smalltalk"; uncertainty_behavior: string; uncertainty_modes: Array<"acknowledge" | "ask_clarifying" | "offer_contact">; fallback_message: string;
  simple_mode: boolean; topics: AgentTopic[]; custom_rules: AgentRule[];
}
export interface KnowledgeProfile { company_description: string; products: string; locations: string; important_notes: string; contact_phone: string; contact_email: string; website: string; }
export interface AgentFaq extends AgentListItem { question: string; answer: string; }
export interface AgentKnowledgeService extends AgentListItem { name: string; description: string; price_information: string; }
export interface BusinessHours { weekday: number; opens_at: string; closes_at: string; is_closed: boolean; }
export interface AgentKnowledge {
  tenant_id: string; version: number; can_edit: boolean; profile: KnowledgeProfile;
  faqs: AgentFaq[]; services: AgentKnowledgeService[]; business_hours: BusinessHours[];
}
export interface VoiceOption { value: string; label: string; recommended: boolean; }
export interface Capability { key: string; label: string; description: string; available: boolean; active: boolean; unavailable_reason: string | null; }
export interface AgentCatalog { voices: VoiceOption[]; capabilities: Capability[]; }
export interface RuntimeSummary {
  tenant_id: string; configuration_version: number; company_name: string; assistant_name: string; language: string; style: AgentConfiguration["tone"]; business_hours_status: "open" | "closed"; model: string; voice: string; speed: number;
  turn_detection: Record<string, unknown>; capability_keys: string[]; tool_names: string[]; greeting: string; prompt_sections: string[];
}
export interface PromptPreview { configuration_version: number; prompt: string; sections: string[]; }

export type CalendarProviderName = "google" | "microsoft";
export interface CalendarProviderConfiguration {
  provider: CalendarProviderName; label: string; configured: boolean; missing_configuration: string[];
}
export interface ExternalCalendar {
  id: string; connection_id: string; external_calendar_id: string; calendar_name: string;
  calendar_timezone: string; owner_name: string; access_role: string; is_primary: boolean;
  can_write: boolean; is_selected_for_availability: boolean; is_selected_for_booking: boolean;
  last_seen_at: string;
}
export interface CalendarConnection {
  id: string; provider: CalendarProviderName; account_email: string; display_name: string;
  connection_status: "connected" | "reauthorization_required" | "error" | "disconnected";
  last_successful_request_at: string | null; last_error_code: string | null; created_at: string;
  calendars: ExternalCalendar[];
}
export interface CalendarConnectionsOverview { providers: CalendarProviderConfiguration[]; connections: CalendarConnection[]; }
export interface CalendarBusinessHour { weekday: number; start_time: string; end_time: string; is_active: boolean; }
export interface BookingConfiguration {
  id: string; tenant_id: string; timezone: string; slot_interval_minutes: number;
  minimum_notice_minutes: number; maximum_booking_horizon_days: number;
  buffer_before_minutes: number; buffer_after_minutes: number;
  maximum_suggestions_per_request: number; business_hours: CalendarBusinessHour[]; updated_at: string;
}
export interface CalendarAppointmentType {
  id: string; tenant_id: string; service_id: string; name: string; service_name: string; description: string; duration_minutes: number;
  buffer_before_minutes: number | null; buffer_after_minutes: number | null;
  location_type: "phone" | "onsite" | "video" | "custom"; location_text: string;
  is_active: boolean; created_at: string; updated_at: string;
}
export type AppointmentTypeWrite = Pick<CalendarAppointmentType, "service_id" | "buffer_before_minutes" | "buffer_after_minutes" | "location_type" | "location_text" | "is_active">;
export interface AvailableCalendarSlot {
  slot_id: string; start: string; end: string; spoken_date: string; spoken_time: string;
}
export interface AgentAvailabilityRequest {
  appointment_type_id: string; preferred_date: string | null;
  preferred_time_of_day: "morning" | "afternoon" | "evening" | null; search_days: number;
}
export interface CalendarAvailabilityResult { success: boolean; timezone: string; slots: AvailableCalendarSlot[]; }
export interface CalendarBookingResult {
  success: boolean; booking_id?: string; status?: "pending" | "confirmed" | "failed" | "cancelled";
  start?: string; end?: string; timezone?: string; error_code?: string; message?: string;
  alternative_slots: AvailableCalendarSlot[];
  external_event_id?: string; calendar_name?: string; service_name?: string;
}
export interface CalendarEntry {
  id: string; kind: "platform" | "external"; service_name: string; customer_name: string;
  start_at: string; end_at: string; duration_minutes: number; appointment_format: string; location: string;
  status: string; sync_status: string; source: string; calendar_provider: string; calendar_id: string;
  calendar_name: string; external_event_id: string | null; buffer_before_minutes: number;
  buffer_after_minutes: number; created_at: string | null;
}
export interface CalendarAgenda { calendar_connected: boolean; entries: CalendarEntry[]; }

