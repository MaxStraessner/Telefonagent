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
}
export interface Health { status: string; database: string; }
export interface PlatformData {
  tenant: Tenant; services: Service[]; staff: StaffMember[]; appointments: Appointment[];
  platformStatus: PlatformStatus; health: Health;
}

