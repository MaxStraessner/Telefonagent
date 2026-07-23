import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TurnCoordinator, type TurnState } from "../src/features/realtime/turnCoordinator";

const policy = {
  continuation_ack_timeout_ms: 4_000,
  recovery_response_timeout_ms: 8_000,
  maximum_attempts_per_turn: 1,
};

function fixture() {
  const requestResponse = vi.fn();
  const states: TurnState[] = [];
  const events = vi.fn();
  const failures = vi.fn();
  const coordinator = new TurnCoordinator(policy, {
    requestResponse,
    onState: (state) => states.push(state),
    onEvent: events,
    onFailure: failures,
  });
  return { coordinator, requestResponse, states, events, failures };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("TurnCoordinator", () => {
  it("schließt eine Antwort ohne Audio nach response.done ab", () => {
    const { coordinator, states } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.responseDone({
      responseId: "response-1",
      status: "completed",
      reason: "completed",
      recoverable: false,
      interrupted: false,
      functionCallRequested: false,
    });
    expect(states).toEqual(["response_active", "completed"]);
  });

  it("schließt Playback erst nach response.done und echtem Buffer-Stopp ab", () => {
    const { coordinator } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.audioStarted("response-1");
    coordinator.responseDone({
      responseId: "response-1",
      status: "completed",
      reason: "completed",
      recoverable: false,
      interrupted: false,
      functionCallRequested: false,
    });
    expect(coordinator.state).toBe("playback_active");
    coordinator.audioStopped("response-1");
    expect(coordinator.state).toBe("completed");
  });

  it("wartet nach agent_tool_end und akzeptiert die automatische SDK-Fortsetzung", async () => {
    const { coordinator, requestResponse } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.toolResultReady("call-1", "digest-1");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(3_999);
    coordinator.responseCreated("response-continuation");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(requestResponse).not.toHaveBeenCalled();
    expect(coordinator.state).toBe("continuation_active");
    expect(coordinator.record.continuationResponseId).toBe("response-continuation");
  });

  it("ordnet eine vor agent_tool_end eintreffende Fortsetzung derselben Tool-Runde zu", async () => {
    const { coordinator, requestResponse, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.responseCreated("response-early-continuation");
    expect(coordinator.state).toBe("tool_running");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(coordinator.state).toBe("continuation_active");
    expect(coordinator.record.continuationResponseId).toBe("response-early-continuation");
    expect(requestResponse).not.toHaveBeenCalled();
    expect(failures).not.toHaveBeenCalled();
  });

  it("fordert bei fehlender Tool-Fortsetzung genau einmal ohne Overrides eine Antwort an", async () => {
    const { coordinator, requestResponse } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.toolResultReady("call-1", "digest-1");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(4_000);
    expect(requestResponse).toHaveBeenCalledOnce();
    expect(requestResponse).toHaveBeenCalledWith();
    coordinator.responseCreated("response-recovery");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(requestResponse).toHaveBeenCalledOnce();
    expect(coordinator.record.recoveryAttempts).toBe(1);
  });

  it("endet kontrolliert, wenn auch die einzige Recovery keine Antwort erzeugt", async () => {
    const { coordinator, requestResponse, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(12_000);
    expect(requestResponse).toHaveBeenCalledOnce();
    expect(failures).toHaveBeenCalledOnce();
    expect(failures).toHaveBeenCalledWith("recovery_response_missing", expect.anything());
    expect(coordinator.state).toBe("failed");
  });

  it("bricht bei einer Folgeantwort ohne Response-ID fail closed ab", () => {
    const { coordinator, failures, requestResponse } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    coordinator.responseCreated(null);
    expect(failures).toHaveBeenCalledWith("continuation_response_id_missing", expect.anything());
    expect(requestResponse).not.toHaveBeenCalled();
    expect(coordinator.state).toBe("failed");
  });

  it("ignoriert einen doppelten agent_tool_end während der Übergabe", async () => {
    const { coordinator, requestResponse, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(4_000);
    expect(requestResponse).toHaveBeenCalledOnce();
    expect(failures).not.toHaveBeenCalled();
  });

  it("meldet einen Providerfehler als terminalen Turn-Grund", () => {
    const { coordinator, failures, events } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    coordinator.responseDone({
      responseId: "response-original",
      status: "failed",
      reason: "provider_error_during_turn",
      recoverable: false,
      interrupted: false,
      functionCallRequested: true,
    });
    expect(failures).toHaveBeenCalledWith("provider_error_during_turn", expect.objectContaining({
      originatingResponseId: "response-original",
      toolCallId: "call-1",
    }));
    expect(events.mock.calls.some(([name, detail]) =>
      name === "turn_failed" && detail.reason === "provider_error_during_turn"
    )).toBe(true);
  });

  it("verwendet dasselbe Recovery-Budget für ein späteres incomplete", () => {
    const { coordinator, requestResponse, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-original");
    coordinator.responseDone({
      responseId: "response-original",
      status: "incomplete",
      reason: "max_output_tokens",
      recoverable: true,
      interrupted: false,
      functionCallRequested: false,
    });
    coordinator.responseCreated("response-recovery");
    coordinator.responseDone({
      responseId: "response-recovery",
      status: "incomplete",
      reason: "max_output_tokens",
      recoverable: true,
      interrupted: false,
      functionCallRequested: false,
    });
    expect(requestResponse).toHaveBeenCalledOnce();
    expect(failures).toHaveBeenCalledOnce();
    expect(coordinator.state).toBe("failed");
  });

  it("stellt unterbrochene und abgebrochene Antworten nicht automatisch wieder her", () => {
    const { coordinator, requestResponse } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.responseDone({
      responseId: "response-1",
      status: "cancelled",
      reason: "turn_detected",
      recoverable: true,
      interrupted: true,
      functionCallRequested: false,
    });
    expect(coordinator.state).toBe("interrupted");
    expect(requestResponse).not.toHaveBeenCalled();
  });

  it("ignoriert verspätete Events einer älteren Response", () => {
    const { coordinator } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.responseDone({
      responseId: "response-old",
      status: "failed",
      reason: "late",
      recoverable: false,
      interrupted: false,
      functionCallRequested: false,
    });
    expect(coordinator.state).toBe("response_active");
  });

  it("weist eine zweite gleichzeitig aktive Response kontrolliert zurück", () => {
    const { coordinator, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.responseCreated("response-2");
    expect(coordinator.state).toBe("failed");
    expect(failures).toHaveBeenCalledWith("concurrent_response_created", expect.anything());
  });

  it("weist parallele unterschiedliche Tool-Calls zurück", () => {
    const { coordinator, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.toolStarted("call-1");
    coordinator.toolStarted("call-2");
    expect(coordinator.state).toBe("failed");
    expect(failures).toHaveBeenCalledWith("parallel_tool_call_detected", expect.anything());
  });

  it("ignoriert doppelte Start- und Endereignisse derselben Tool-Call-ID", async () => {
    const { coordinator, requestResponse, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(2_000);
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    await vi.advanceTimersByTimeAsync(2_000);
    expect(requestResponse).toHaveBeenCalledOnce();
    expect(failures).not.toHaveBeenCalled();
  });

  it("lässt doppelte response.created-Events derselben ID zustandsneutral", () => {
    const { coordinator, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.responseCreated("response-1");
    expect(coordinator.state).toBe("response_active");
    expect(failures).not.toHaveBeenCalled();
  });

  it("räumt Recovery-Timer beim Session-Cleanup auf", async () => {
    const { coordinator, requestResponse, failures } = fixture();
    coordinator.responseRequested();
    coordinator.responseCreated("response-1");
    coordinator.toolStarted("call-1");
    coordinator.agentToolEnd("call-1");
    coordinator.dispose();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(requestResponse).not.toHaveBeenCalled();
    expect(failures).not.toHaveBeenCalled();
  });
});
