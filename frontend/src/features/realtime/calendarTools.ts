import { tool } from "@openai/agents/realtime";
import { z } from "zod";
import { api } from "../../api/client";

export function createCalendarTools(toolNames: string[], onEvent: (type: string, detail?: string) => void) {
  const enabled = new Set(toolNames);
  const listAppointmentTypesTool = tool({
    name: "list_appointment_types",
    description: "Lädt ausschließlich die aktiven Terminarten dieses Unternehmensaccounts.",
    parameters: z.object({}),
    execute: async () => {
      onEvent("tool_started", "list_appointment_types");
      const result = await api.listAgentAppointmentTypes();
      onEvent("tool_completed", "list_appointment_types");
      return result;
    },
    errorFunction: (_context, error) => JSON.stringify({ success: false, error_code: "tool_request_failed", message: error instanceof Error ? error.message : "Terminarten konnten nicht geladen werden." }),
  });
  const findAvailableAppointmentsTool = tool({
    name: "find_available_appointments",
    description: "Sucht serverseitig freie Termine. Niemals selbst Verfügbarkeiten berechnen.",
    parameters: z.object({
      appointment_type_id: z.string().uuid(),
      preferred_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable(),
      preferred_time_of_day: z.enum(["morning", "afternoon", "evening"]).nullable(),
      search_days: z.number().int().min(1).max(30),
    }),
    execute: async (input) => {
      onEvent("tool_started", "find_available_appointments");
      const result = await api.findAvailableAppointments(input);
      onEvent("tool_completed", `find_available_appointments:${result.slots.length}`);
      return result;
    },
    errorFunction: (_context, error) => JSON.stringify({ success: false, error_code: "tool_request_failed", message: error instanceof Error ? error.message : "Freie Termine konnten nicht ermittelt werden." }),
  });
  const createCalendarBookingTool = tool({
    name: "create_calendar_booking",
    description: "Bucht einen zuvor angebotenen Termin erst nach ausdrücklicher Kundenbestätigung.",
    parameters: z.object({
      slot_id: z.string().min(20),
      appointment_type_id: z.string().uuid(),
      customer_name: z.string().min(1),
      customer_phone: z.string().min(3),
      customer_email: z.string().nullable(),
      customer_notes: z.string().nullable(),
      idempotency_key: z.string().min(8).max(200),
    }),
    execute: async (input) => {
      onEvent("tool_started", "create_calendar_booking");
      const result = await api.createCalendarBooking({ ...input, customer_email: input.customer_email ?? "", customer_notes: input.customer_notes ?? "" });
      onEvent(result.success ? "tool_completed" : "tool_failed", `create_calendar_booking:${result.status ?? result.error_code ?? "unknown"}`);
      return result;
    },
    errorFunction: (_context, error) => JSON.stringify({ success: false, error_code: "tool_request_failed", message: error instanceof Error ? error.message : "Der Termin konnte nicht gebucht werden." }),
  });
  return [
    enabled.has("list_appointment_types") ? listAppointmentTypesTool : null,
    enabled.has("find_available_appointments") ? findAvailableAppointmentsTool : null,
    enabled.has("create_calendar_booking") ? createCalendarBookingTool : null,
  ].filter((value) => value !== null);
}
