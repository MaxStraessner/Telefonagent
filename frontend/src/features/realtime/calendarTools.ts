import { tool } from "@openai/agents/realtime";
import { z } from "zod";
import { api } from "../../api/client";

export function createCalendarTools(toolNames: string[], onEvent: (type: string, detail?: string) => void) {
  const enabled = new Set(toolNames);
  const listBookableServices = tool({
    name: "list_bookable_services",
    description: "Lädt nur aktive Leistungen und deren aktive Terminarten. Keine Leistung erfinden.",
    parameters: z.object({}),
    execute: async () => {
      onEvent("tool_started", "list_bookable_services");
      const result = await api.listBookableServices();
      onEvent("tool_completed", `list_bookable_services:${result.services.length}`);
      return result;
    },
    errorFunction: (_context, error) => JSON.stringify({ success: false, error_code: "tool_request_failed", message: error instanceof Error ? error.message : "Leistungen konnten nicht geladen werden." }),
  });
  const checkAvailability = tool({
    name: "check_appointment_availability",
    description: "Prüft eine konkrete Startzeit serverseitig. Verfügbarkeit niemals selbst schätzen.",
    parameters: z.object({
      service_id: z.string().uuid(),
      appointment_type_id: z.string().uuid(),
      requested_start: z.string().datetime({ offset: true }),
      timezone: z.string().min(1),
    }),
    execute: async (input) => {
      onEvent("tool_started", "check_appointment_availability");
      const result = await api.checkAppointmentAvailability(input);
      onEvent("tool_completed", `check_appointment_availability:${result.available}`);
      return result;
    },
    errorFunction: (_context, error) => JSON.stringify({ success: false, error_code: "tool_request_failed", message: error instanceof Error ? error.message : "Verfügbarkeit konnte nicht geprüft werden." }),
  });
  const createAppointment = tool({
    name: "create_appointment",
    description: "Erstellt den Termin erst nach ausdrücklicher Bestätigung und bestätigt Erfolg nur mit externer Ereignis-ID.",
    parameters: z.object({
      service_id: z.string().uuid(),
      appointment_type_id: z.string().uuid(),
      customer_name: z.string().min(1),
      customer_phone: z.string().nullable(),
      customer_email: z.string().nullable(),
      start_at: z.string().datetime({ offset: true }),
      timezone: z.string().min(1),
      idempotency_key: z.string().min(8).max(200),
      confirmed: z.literal(true),
    }),
    execute: async (input) => {
      onEvent("tool_started", "create_appointment");
      const result = await api.createAppointment(input);
      const verified = result.success && result.status === "confirmed" && Boolean(result.external_event_id);
      onEvent(verified ? "tool_completed" : "tool_failed", `create_appointment:${result.status ?? result.error_code ?? "unknown"}`);
      return verified ? result : { ...result, success: false, error_code: result.error_code ?? "calendar_confirmation_missing", message: result.message ?? "Die externe Kalenderbestätigung fehlt." };
    },
    errorFunction: (_context, error) => JSON.stringify({ success: false, error_code: "tool_request_failed", message: error instanceof Error ? error.message : "Der Termin konnte nicht gebucht werden." }),
  });
  return [
    enabled.has("list_bookable_services") ? listBookableServices : null,
    enabled.has("check_appointment_availability") ? checkAvailability : null,
    enabled.has("create_appointment") ? createAppointment : null,
  ].filter((value) => value !== null);
}
