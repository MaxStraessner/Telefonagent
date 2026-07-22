import { tool } from "@openai/agents/realtime";
import { z } from "zod";
import { api } from "../../api/client";
import { RealtimeToolExecutor } from "./toolExecution";

type ToolDetails = { toolCall?: { callId?: string } };

function callId(details: ToolDetails | undefined) {
  return details?.toolCall?.callId ?? crypto.randomUUID();
}

const toolError = (_context: unknown, error: unknown) => JSON.stringify({
  success: false,
  error_code: "tool_request_failed",
  message: error instanceof Error ? error.message : "Die Aktion konnte nicht abgeschlossen werden.",
});

export function createCalendarTools(toolNames: string[], executor: RealtimeToolExecutor) {
  const enabled = new Set(toolNames);
  const listBookableServices = tool({
    name: "list_bookable_services",
    description: "Lädt ausschließlich aktive Leistungen und Terminarten. Erfinde keine Leistung.",
    parameters: z.object({}),
    execute: async (_input, _context, details) => {
      const id = callId(details as ToolDetails);
      return executor.execute(id, "list_bookable_services", () => api.listBookableServices(executor.sessionId, id));
    },
    errorFunction: toolError,
  });
  const resolveService = tool({
    name: "resolve_service",
    description: "Löst den gesprochenen Leistungsnamen gegen den serverseitigen Katalog auf.",
    parameters: z.object({ service_name: z.string().min(1) }),
    execute: async (input, _context, details) => {
      const id = callId(details as ToolDetails);
      return executor.execute(id, "resolve_service", () => api.resolveService({ ...input, session_id: executor.sessionId, tool_call_id: id }));
    },
    errorFunction: toolError,
  });
  const checkAvailability = tool({
    name: "check_appointment_availability",
    description: "Prüft eine konkrete Startzeit vorläufig gegen den Sitzungssnapshot. Verfügbarkeit nie selbst schätzen.",
    parameters: z.object({
      service_id: z.string().uuid(), appointment_type_id: z.string().uuid(),
      requested_start: z.string().datetime({ offset: true }), timezone: z.string().min(1),
    }),
    execute: async (input, _context, details) => {
      const id = callId(details as ToolDetails);
      return executor.execute(id, "check_appointment_availability", () => api.checkAppointmentAvailability({ ...input, session_id: executor.sessionId, tool_call_id: id }));
    },
    errorFunction: toolError,
  });
  const findAlternatives = tool({
    name: "find_alternative_slots",
    description: "Findet serverseitig freie Alternativen aus dem aktuellen Sitzungssnapshot.",
    parameters: z.object({
      service_id: z.string().uuid(), appointment_type_id: z.string().uuid(),
      search_start: z.string().datetime({ offset: true }), search_days: z.number().int().min(1).max(30).default(7),
      preferred_day: z.string().optional(), preferred_time_of_day: z.enum(["morning", "afternoon", "evening"]).optional(),
      maximum_results: z.number().int().min(1).max(10).default(3),
    }),
    execute: async (input, _context, details) => {
      const id = callId(details as ToolDetails);
      return executor.execute(id, "find_alternative_slots", () => api.findAlternativeSlots({ ...input, session_id: executor.sessionId, tool_call_id: id }));
    },
    errorFunction: toolError,
  });
  const finalizeBooking = tool({
    name: "finalize_appointment_booking",
    description: "Führt nach ausdrücklicher Bestätigung die exakte Endprüfung und echte Kalenderbuchung aus.",
    parameters: z.object({
      service_id: z.string().uuid(), appointment_type_id: z.string().uuid(), customer_name: z.string().min(1),
      customer_phone: z.string().nullable(), customer_email: z.string().nullable(),
      start_at: z.string().datetime({ offset: true }), timezone: z.string().min(1),
      confirmation_version: z.number().int().min(1), confirmed: z.literal(true),
    }),
    execute: async (input, _context, details) => {
      const id = callId(details as ToolDetails);
      return executor.execute(id, "finalize_appointment_booking", async () => {
        const result = await api.finalizeAppointmentBooking({ ...input, session_id: executor.sessionId, tool_call_id: id });
        return result.success && result.status === "confirmed" && result.external_event_id
          ? result
          : { ...result, success: false, error_code: result.error_code ?? "external_confirmation_missing" };
      });
    },
    errorFunction: toolError,
  });
  return [
    enabled.has("list_bookable_services") ? listBookableServices : null,
    enabled.has("resolve_service") ? resolveService : null,
    enabled.has("check_appointment_availability") ? checkAvailability : null,
    enabled.has("find_alternative_slots") ? findAlternatives : null,
    enabled.has("finalize_appointment_booking") ? finalizeBooking : null,
  ].filter((value) => value !== null);
}
