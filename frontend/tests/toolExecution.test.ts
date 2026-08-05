import { describe, expect, it, vi } from "vitest";
import { RealtimeToolExecutor } from "../src/features/realtime/toolExecution";

const continuationPaths = [
  ["list_bookable_services", true], ["resolve_service", true],
  ["resolve_booking_datetime", true],
  ["check_appointment_availability", true], ["find_alternative_slots", true],
  ["select_booking_slot", true], ["prepare_appointment_confirmation", true],
  ["finalize_appointment_booking", true], ["no_slots_available", true],
  ["list_bookable_services", false],
  ["resolve_service", false], ["check_appointment_availability", false],
  ["resolve_booking_datetime", false], ["find_alternative_slots", false],
  ["select_booking_slot", false], ["prepare_appointment_confirmation", false],
  ["finalize_appointment_booking", false],
  ["provider_timeout", false], ["slot_conflict", true],
] as const;

describe("RealtimeToolExecutor continuation", () => {
  it.each(continuationPaths)("verarbeitet %s mit Erfolg=%s genau einmal", async (toolName, success) => {
    const events = vi.fn();
    const action = success ? vi.fn().mockResolvedValue({ success: true }) : vi.fn().mockRejectedValue(new Error("controlled"));
    const executor = new RealtimeToolExecutor("session-1", events);
    const result = executor.execute("call-1", toolName, action);
    if (success) await expect(result).resolves.toEqual({ success: true });
    else await expect(result).resolves.toEqual({
      success: false,
      error_code: "tool_request_failed",
      message: "controlled",
    });
    expect(action).toHaveBeenCalledOnce();
    expect(events.mock.calls.filter(([name]) => name === "tool_started")).toHaveLength(1);
    expect(events.mock.calls.filter(([name]) => name === "tool_completed")).toHaveLength(1);
    expect(events.mock.calls.some(([name]) => name === "tool_result_submitted")).toBe(false);
    expect(executor.status("call-1")).toBe(success ? "completed" : "failed");
  });

  it("dedupliziert dieselbe Tool-Call-ID und ordnet genau eine SDK-Fortsetzungsantwort zu", async () => {
    const events = vi.fn();
    const action = vi.fn().mockResolvedValue({ success: true });
    const executor = new RealtimeToolExecutor("session-1", events);
    const first = executor.execute("same-call", "resolve_service", action);
    const second = executor.execute("same-call", "resolve_service", action);
    await Promise.all([first, second]);
    executor.markResultSubmitted("same-call");
    executor.attachContinuationResponse("response-1");
    executor.attachContinuationResponse("response-2");
    expect(action).toHaveBeenCalledOnce();
    expect(events.mock.calls.filter(([name]) => name === "tool_duplicate_ignored")).toHaveLength(1);
    expect(events.mock.calls.filter(([name]) => name === "tool_result_submitted")).toHaveLength(1);
    expect(events.mock.calls.filter(([name]) => name === "tool_continuation_response_created")).toHaveLength(1);
  });

  it("bleibt während eines langsamen Werkzeugs aktiv und wechselt danach zur Fortsetzung", async () => {
    let finish: ((value: { success: true }) => void) | undefined;
    const action = vi.fn(() => new Promise<{ success: true }>((resolve) => { finish = resolve; }));
    const executor = new RealtimeToolExecutor("session-1", vi.fn());
    const result = executor.execute("slow-call", "check_appointment_availability", action);
    expect(executor.status("slow-call")).toBe("running");
    finish?.({ success: true });
    await expect(result).resolves.toEqual({ success: true });
    expect(executor.status("slow-call")).toBe("completed");
    executor.markResultSubmitted("slow-call");
    expect(executor.status("slow-call")).toBe("result_submitted");
    executor.attachContinuationResponse("response-slow");
    expect(executor.status("slow-call")).toBe("continuation_started");
    executor.completeResponse("response-slow");
    expect(executor.status("slow-call")).toBe("response_completed");
  });
});
