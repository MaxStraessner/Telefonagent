import { describe, expect, it, vi } from "vitest";
import { RealtimeToolExecutor } from "../src/features/realtime/toolExecution";

const continuationPaths = [
  ["list_bookable_services", true], ["resolve_service", true],
  ["check_appointment_availability", true], ["find_alternative_slots", true],
  ["finalize_appointment_booking", true], ["list_bookable_services", false],
  ["resolve_service", false], ["check_appointment_availability", false],
  ["find_alternative_slots", false], ["finalize_appointment_booking", false],
  ["provider_timeout", false], ["slot_conflict", true],
] as const;

describe("RealtimeToolExecutor continuation", () => {
  it.each(continuationPaths)("verarbeitet %s mit Erfolg=%s genau einmal", async (toolName, success) => {
    const events = vi.fn();
    const states = vi.fn();
    const action = success ? vi.fn().mockResolvedValue({ success: true }) : vi.fn().mockRejectedValue(new Error("controlled"));
    const executor = new RealtimeToolExecutor("session-1", events, states);
    const result = executor.execute("call-1", toolName, action);
    if (success) await expect(result).resolves.toEqual({ success: true });
    else await expect(result).resolves.toEqual({
      success: false,
      error_code: "tool_request_failed",
      message: "controlled",
    });
    expect(action).toHaveBeenCalledOnce();
    expect(events.mock.calls.filter(([name]) => name === "tool_result_sent")).toHaveLength(1);
    expect(states).not.toHaveBeenLastCalledWith("idle");
  });

  it("dedupliziert dieselbe Tool-Call-ID und ordnet genau eine SDK-Fortsetzungsantwort zu", async () => {
    const events = vi.fn();
    const action = vi.fn().mockResolvedValue({ success: true });
    const executor = new RealtimeToolExecutor("session-1", events, vi.fn());
    const first = executor.execute("same-call", "resolve_service", action);
    const second = executor.execute("same-call", "resolve_service", action);
    await Promise.all([first, second]);
    executor.attachContinuationResponse("response-1");
    executor.attachContinuationResponse("response-2");
    expect(action).toHaveBeenCalledOnce();
    expect(events.mock.calls.filter(([name]) => name === "tool_continuation_response_created")).toHaveLength(1);
  });

  it("bleibt während eines langsamen Werkzeugs aktiv und wechselt danach zur Fortsetzung", async () => {
    let finish: ((value: { success: true }) => void) | undefined;
    const action = vi.fn(() => new Promise<{ success: true }>((resolve) => { finish = resolve; }));
    const states = vi.fn();
    const executor = new RealtimeToolExecutor("session-1", vi.fn(), states);
    const result = executor.execute("slow-call", "check_appointment_availability", action);
    expect(states).toHaveBeenLastCalledWith("tool_running");
    finish?.({ success: true });
    await expect(result).resolves.toEqual({ success: true });
    expect(states.mock.calls.map(([state]) => state)).toEqual([
      "tool_running", "tool_result_ready", "continuation_starting",
    ]);
  });
});
