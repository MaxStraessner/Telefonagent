export type TurnState =
  | "idle"
  | "response_active"
  | "tool_running"
  | "tool_output_submitted"
  | "continuation_pending"
  | "continuation_active"
  | "playback_active"
  | "completed"
  | "interrupted"
  | "failed";

export interface TurnRecord {
  turnId: string;
  originatingResponseId: string | null;
  toolCallId: string | null;
  resultDigest: string | null;
  continuationResponseId: string | null;
  audioStarted: boolean;
  audioStopped: boolean;
  responseDone: boolean;
  recoveryAttempts: number;
  terminalReason: string | null;
}

export interface TurnRecoveryPolicy {
  continuation_ack_timeout_ms: number;
  recovery_response_timeout_ms: number;
  maximum_attempts_per_turn: number;
}

export interface ResponseCompletion {
  responseId: string | null;
  status: string | null;
  reason: string;
  recoverable: boolean;
  interrupted: boolean;
  functionCallRequested: boolean;
}

interface TurnCoordinatorCallbacks {
  requestResponse: () => void;
  onState: (state: TurnState, record: Readonly<TurnRecord>) => void;
  onEvent: (type: string, detail: Record<string, unknown>) => void;
  onFailure: (reason: string) => void;
}

let turnSequence = 0;

function newTurn(): TurnRecord {
  turnSequence += 1;
  return {
    turnId: `turn-${Date.now()}-${turnSequence}`,
    originatingResponseId: null,
    toolCallId: null,
    resultDigest: null,
    continuationResponseId: null,
    audioStarted: false,
    audioStopped: false,
    responseDone: false,
    recoveryAttempts: 0,
    terminalReason: null,
  };
}

function currentResponseId(record: TurnRecord) {
  return record.continuationResponseId ?? record.originatingResponseId;
}

export class TurnCoordinator {
  private stateValue: TurnState = "idle";
  private recordValue = newTurn();
  private continuationAckTimer: ReturnType<typeof setTimeout> | null = null;
  private recoveryResponseTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(
    private readonly policy: TurnRecoveryPolicy,
    private readonly callbacks: TurnCoordinatorCallbacks,
  ) {}

  get state() {
    return this.stateValue;
  }

  get record(): Readonly<TurnRecord> {
    return this.recordValue;
  }

  responseRequested() {
    if (this.disposed) return;
    if (["idle", "completed", "interrupted", "failed"].includes(this.stateValue)) {
      this.recordValue = newTurn();
    }
    this.transition("response_active");
  }

  responseCreated(responseId: string | null) {
    if (this.disposed || !responseId) return;
    if (this.stateValue === "failed") {
      this.emit("turn_late_response_created_ignored", { responseId });
      return;
    }
    if (this.stateValue === "continuation_pending") {
      this.clearRecoveryTimers();
      this.recordValue.continuationResponseId = responseId;
      this.recordValue.audioStarted = false;
      this.recordValue.audioStopped = false;
      this.recordValue.responseDone = false;
      this.transition("continuation_active");
      this.emit("turn_continuation_created", { responseId });
      return;
    }
    if (
      ["tool_running", "tool_output_submitted"].includes(this.stateValue)
      && this.recordValue.toolCallId
    ) {
      this.recordValue.continuationResponseId = responseId;
      this.recordValue.audioStarted = false;
      this.recordValue.audioStopped = false;
      this.recordValue.responseDone = false;
      this.emit("turn_early_continuation_created", { responseId });
      return;
    }
    if (
      responseId === this.recordValue.originatingResponseId
      || responseId === this.recordValue.continuationResponseId
    ) {
      this.emit("turn_duplicate_response_created_ignored", { responseId });
      return;
    }
    if (this.stateValue === "response_active" && this.recordValue.originatingResponseId) {
      this.fail("concurrent_response_created");
      return;
    }
    if (!["idle", "completed", "interrupted", "failed", "response_active"].includes(this.stateValue)) {
      this.fail("concurrent_response_created");
      return;
    }
    if (this.stateValue !== "response_active") {
      this.recordValue = newTurn();
    }
    this.recordValue.originatingResponseId = responseId;
    this.transition("response_active");
  }

  toolStarted(callId: string) {
    if (this.disposed) return;
    if (
      this.recordValue.toolCallId === callId
      && ["continuation_pending", "continuation_active", "playback_active"].includes(this.stateValue)
    ) {
      this.emit("turn_duplicate_tool_start_ignored", { callId });
      return;
    }
    if (this.recordValue.toolCallId && this.recordValue.toolCallId !== callId) {
      this.fail("parallel_tool_call_detected");
      return;
    }
    this.recordValue.toolCallId = callId;
    this.transition("tool_running");
  }

  toolResultReady(callId: string, resultDigest: string) {
    if (this.disposed || this.recordValue.toolCallId !== callId) return;
    this.recordValue.resultDigest = resultDigest;
  }

  agentToolEnd(callId: string) {
    if (this.disposed || this.recordValue.toolCallId !== callId) return;
    if (["continuation_pending", "continuation_active", "playback_active"].includes(this.stateValue)) {
      this.emit("turn_duplicate_tool_end_ignored", { callId });
      return;
    }
    this.transition("tool_output_submitted");
    this.transition("continuation_pending");
    if (this.recordValue.continuationResponseId) {
      this.transition("continuation_active");
      return;
    }
    this.startContinuationWatch();
  }

  responseDone(completion: ResponseCompletion) {
    if (this.disposed) return;
    const activeId = currentResponseId(this.recordValue);
    if (completion.responseId && activeId && completion.responseId !== activeId) {
      this.emit("turn_late_response_done_ignored", { responseId: completion.responseId });
      return;
    }
    if (completion.interrupted || completion.status === "cancelled") {
      this.terminal("interrupted", completion.reason);
      return;
    }
    if (completion.status === "failed") {
      this.fail(completion.reason || "response_failed");
      return;
    }
    if (completion.status === "incomplete") {
      if (this.stateValue === "continuation_pending") return;
      if (completion.recoverable && this.canRecover()) {
        this.requestRecovery(`incomplete:${completion.reason}`);
      } else {
        this.fail(completion.reason || "response_incomplete");
      }
      return;
    }
    this.recordValue.responseDone = true;
    if (
      completion.functionCallRequested
      && completion.responseId === this.recordValue.originatingResponseId
      && !this.recordValue.continuationResponseId
    ) {
      return;
    }
    if (!this.recordValue.audioStarted || this.recordValue.audioStopped) {
      this.terminal("completed", "response_and_audio_completed");
    }
  }

  audioStarted(responseId: string | null) {
    if (!this.isCurrentResponse(responseId) || this.disposed) return;
    this.recordValue.audioStarted = true;
    this.recordValue.audioStopped = false;
    this.transition("playback_active");
  }

  audioStopped(responseId: string | null) {
    if (!this.isCurrentResponse(responseId) || this.disposed) return;
    this.recordValue.audioStopped = true;
    if (this.recordValue.responseDone) {
      this.terminal("completed", "response_and_audio_completed");
    }
  }

  audioInterrupted(reason: string) {
    if (this.disposed) return;
    this.terminal("interrupted", reason);
  }

  dispose() {
    this.disposed = true;
    this.clearRecoveryTimers();
  }

  private isCurrentResponse(responseId: string | null) {
    const current = currentResponseId(this.recordValue);
    return !responseId || !current || responseId === current;
  }

  private canRecover() {
    return this.recordValue.recoveryAttempts < this.policy.maximum_attempts_per_turn;
  }

  private startContinuationWatch() {
    this.clearRecoveryTimers();
    this.continuationAckTimer = setTimeout(() => {
      if (this.disposed || this.stateValue !== "continuation_pending") return;
      if (!this.canRecover()) {
        this.fail("tool_continuation_missing");
        return;
      }
      this.requestRecovery("tool_continuation_timeout");
    }, this.policy.continuation_ack_timeout_ms);
  }

  private requestRecovery(reason: string) {
    if (!this.canRecover()) {
      this.fail("recovery_budget_exhausted");
      return;
    }
    this.clearRecoveryTimers();
    this.recordValue.recoveryAttempts += 1;
    this.transition("continuation_pending");
    this.emit("turn_recovery_requested", {
      reason,
      recoveryAttempt: this.recordValue.recoveryAttempts,
      recoveryId: `${this.recordValue.turnId}-recovery-${this.recordValue.recoveryAttempts}`,
    });
    try {
      this.callbacks.requestResponse();
    } catch {
      this.fail("recovery_request_failed");
      return;
    }
    this.recoveryResponseTimer = setTimeout(() => {
      if (this.disposed || this.stateValue !== "continuation_pending") return;
      this.fail("recovery_response_missing");
    }, this.policy.recovery_response_timeout_ms);
  }

  private terminal(state: "completed" | "interrupted", reason: string) {
    this.clearRecoveryTimers();
    this.recordValue.terminalReason = reason;
    this.transition(state);
  }

  private fail(reason: string) {
    if (this.stateValue === "failed" || this.disposed) return;
    this.clearRecoveryTimers();
    this.recordValue.terminalReason = reason;
    this.transition("failed");
    this.callbacks.onFailure(reason);
  }

  private transition(state: TurnState) {
    if (this.disposed || this.stateValue === state) return;
    const before = this.stateValue;
    this.stateValue = state;
    this.callbacks.onState(state, this.recordValue);
    this.emit("turn_state_changed", { before, after: state });
  }

  private emit(type: string, detail: Record<string, unknown>) {
    this.callbacks.onEvent(type, {
      turnId: this.recordValue.turnId,
      toolCallId: this.recordValue.toolCallId,
      originatingResponseId: this.recordValue.originatingResponseId,
      continuationResponseId: this.recordValue.continuationResponseId,
      recoveryAttempts: this.recordValue.recoveryAttempts,
      ...detail,
    });
  }

  private clearRecoveryTimers() {
    if (this.continuationAckTimer) clearTimeout(this.continuationAckTimer);
    if (this.recoveryResponseTimer) clearTimeout(this.recoveryResponseTimer);
    this.continuationAckTimer = null;
    this.recoveryResponseTimer = null;
  }
}
