import { tool } from "@openai/agents/realtime";
import { api } from "../../api/client";
import type { RuntimeToolDefinition } from "../../types/api";
import { RealtimeToolExecutor } from "./toolExecution";

type ToolDetails = { toolCall?: { callId?: string } };
type RealtimeRunContext = { context?: { history?: unknown[] } };
type JsonInput = Record<string, unknown>;

function callId(details: ToolDetails | undefined) {
  return details?.toolCall?.callId ?? crypto.randomUUID();
}

function objectInput(input: unknown): JsonInput {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("Der Werkzeugaufruf enthält keine gültigen Argumente.");
  }
  return input as JsonInput;
}

function requiredString(input: JsonInput, key: string): string {
  const value = input[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Das Werkzeugargument ${key} fehlt.`);
  }
  return value;
}

function optionalString(input: JsonInput, key: string): string | undefined {
  const value = input[key];
  return typeof value === "string" && value ? value : undefined;
}

function requiredNumber(input: JsonInput, key: string): number {
  const value = input[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Das Werkzeugargument ${key} fehlt.`);
  }
  return value;
}

export function latestUserUtterance(runContext: unknown): string {
  const history = (runContext as RealtimeRunContext | undefined)?.context?.history;
  if (!Array.isArray(history)) return "";
  for (const item of [...history].reverse()) {
    if (!item || typeof item !== "object" || (item as { role?: unknown }).role !== "user") continue;
    const content = (item as { content?: unknown }).content;
    if (!Array.isArray(content)) continue;
    const parts = content.flatMap((part) => {
      if (!part || typeof part !== "object") return [];
      const value = part as { transcript?: unknown; text?: unknown };
      const spoken = typeof value.transcript === "string" ? value.transcript : value.text;
      return typeof spoken === "string" && spoken.trim() ? [spoken.trim()] : [];
    });
    if (parts.length) return parts.join(" ");
  }
  return "";
}

const toolError = (_context: unknown, error: unknown) => JSON.stringify({
  success: false,
  error_code: "tool_request_failed",
  message: error instanceof Error ? error.message : "Die Aktion konnte nicht abgeschlossen werden.",
});

async function executeCalendarTool(
  definition: RuntimeToolDefinition,
  inputValue: unknown,
  runContext: unknown,
  executor: RealtimeToolExecutor,
  id: string,
) {
  const input = objectInput(inputValue);
  switch (definition.name) {
    case "list_bookable_services":
      return api.listBookableServices(executor.sessionId, id);
    case "resolve_service":
      return api.resolveService({
        session_id: executor.sessionId,
        tool_call_id: id,
        service_name: requiredString(input, "service_name"),
      });
    case "resolve_booking_datetime":
      return api.resolveBookingDatetime({
        session_id: executor.sessionId,
        tool_call_id: id,
        expression: requiredString(input, "expression"),
      });
    case "check_appointment_availability":
      return api.checkAppointmentAvailability({
        session_id: executor.sessionId,
        tool_call_id: id,
        service_id: requiredString(input, "service_id"),
        appointment_type_id: requiredString(input, "appointment_type_id"),
        requested_start: requiredString(input, "requested_start"),
        timezone: requiredString(input, "timezone"),
      });
    case "find_alternative_slots":
      return api.findAlternativeSlots({
        session_id: executor.sessionId,
        tool_call_id: id,
        service_id: requiredString(input, "service_id"),
        appointment_type_id: requiredString(input, "appointment_type_id"),
        search_start: requiredString(input, "search_start"),
        search_days: requiredNumber(input, "search_days"),
        preferred_day: optionalString(input, "preferred_day"),
        preferred_time_of_day: optionalString(input, "preferred_time_of_day") as
          | "morning"
          | "afternoon"
          | "evening"
          | undefined,
        maximum_results: requiredNumber(input, "maximum_results"),
      });
    case "finalize_appointment_booking": {
      const result = await api.finalizeAppointmentBooking({
        session_id: executor.sessionId,
        tool_call_id: id,
        service_id: requiredString(input, "service_id"),
        appointment_type_id: requiredString(input, "appointment_type_id"),
        customer_name: requiredString(input, "customer_name"),
        customer_phone: optionalString(input, "customer_phone") ?? null,
        customer_email: optionalString(input, "customer_email") ?? null,
        start_at: requiredString(input, "start_at"),
        timezone: requiredString(input, "timezone"),
        confirmation_version: requiredNumber(input, "confirmation_version"),
        confirmation_utterance: latestUserUtterance(runContext),
        confirmed: true,
      });
      return result.success && result.status === "confirmed" && result.external_event_id
        ? result
        : { ...result, success: false, error_code: result.error_code ?? "external_confirmation_missing" };
    }
    default:
      throw new Error(`Das Werkzeug ${definition.name} wird vom Client nicht unterstützt.`);
  }
}

export function createCalendarTools(
  definitions: RuntimeToolDefinition[],
  executor: RealtimeToolExecutor,
) {
  return definitions.map((definition) => tool({
    name: definition.name,
    description: definition.description,
    parameters: definition.parameters,
    strict: true,
    execute: async (input, runContext, details) => {
      const id = callId(details as ToolDetails);
      return executor.execute(
        id,
        definition.name,
        () => executeCalendarTool(definition, input, runContext, executor, id),
      );
    },
    errorFunction: toolError,
  }));
}
