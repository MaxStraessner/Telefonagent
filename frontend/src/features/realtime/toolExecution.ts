export type ToolExecutionStatus =
  | "received"
  | "running"
  | "completed"
  | "failed"
  | "result_submitted"
  | "continuation_started"
  | "response_completed";

export interface ToolExecutionState {
  callId: string;
  toolName: string;
  startedAt: number;
  completedAt: number | null;
  status: ToolExecutionStatus;
  responseId: string | null;
}

export interface NormalizedToolFailure {
  success: false;
  error_code: string;
  message: string;
}

function normalizeToolFailure(error: unknown): NormalizedToolFailure {
  if (error && typeof error === "object") {
    const value = error as { code?: unknown; message?: unknown };
    return {
      success: false,
      error_code: typeof value.code === "string" && value.code ? value.code : "tool_request_failed",
      message: typeof value.message === "string" && value.message
        ? value.message
        : "Die Aktion konnte nicht abgeschlossen werden.",
    };
  }
  return { success: false, error_code: "tool_request_failed", message: "Die Aktion konnte nicht abgeschlossen werden." };
}

export class RealtimeToolExecutor {
  private readonly pending = new Map<string, Promise<unknown>>();
  private readonly executions = new Map<string, ToolExecutionState>();

  constructor(
    readonly sessionId: string,
    private readonly onEvent: (type: string, detail?: string) => void,
  ) {}

  execute<T>(callId: string, toolName: string, action: () => Promise<T>): Promise<T | NormalizedToolFailure> {
    const duplicate = this.pending.get(callId);
    if (duplicate) {
      this.onEvent("tool_duplicate_ignored", JSON.stringify({
        sessionId: this.sessionId,
        toolCallId: callId,
        toolName,
        toolStatus: this.executions.get(callId)?.status ?? "received",
      }));
      return duplicate as Promise<T | NormalizedToolFailure>;
    }
    const state: ToolExecutionState = {
      callId,
      toolName,
      startedAt: performance.now(),
      completedAt: null,
      status: "received",
      responseId: null,
    };
    this.executions.set(callId, state);
    this.onEvent("tool_received", JSON.stringify({
      sessionId: this.sessionId, toolCallId: callId, toolName, toolStatus: state.status,
    }));
    state.status = "running";
    this.onEvent("tool_started", JSON.stringify({
      sessionId: this.sessionId, toolCallId: callId, toolName, toolStatus: state.status,
    }));
    const finish = async (result: T | NormalizedToolFailure, success: boolean) => {
      state.completedAt = performance.now();
      state.status = success ? "completed" : "failed";
      this.onEvent("tool_completed", JSON.stringify({
        sessionId: this.sessionId, toolCallId: callId, toolName, success,
        toolStatus: state.status,
        durationMs: Math.round(state.completedAt - state.startedAt),
        ...(!success && typeof result === "object" && result !== null && "error_code" in result
          ? { errorCode: result.error_code }
          : {}),
      }));
      return result;
    };
    const execution = action().then(
      (result) => finish(result, true),
      (error) => finish(normalizeToolFailure(error), false),
    );
    this.pending.set(callId, execution);
    return execution;
  }

  markResultSubmitted(callId: string) {
    const state = this.executions.get(callId);
    if (!state || ["result_submitted", "continuation_started", "response_completed"].includes(state.status)) return;
    state.status = "result_submitted";
    this.onEvent("tool_result_submitted", JSON.stringify({
      sessionId: this.sessionId,
      toolCallId: callId,
      toolName: state.toolName,
      toolStatus: state.status,
      continuationMode: "sdk_sequenced",
    }));
  }

  attachContinuationResponse(responseId: string) {
    const state = [...this.executions.values()].find(
      (item) => item.status === "result_submitted" && item.responseId === null,
    );
    if (!state) return;
    state.responseId = responseId;
    state.status = "continuation_started";
    this.onEvent("tool_continuation_response_created", JSON.stringify({
      sessionId: this.sessionId,
      toolCallId: state.callId,
      toolName: state.toolName,
      responseId,
      toolStatus: state.status,
    }));
  }

  completeResponse(responseId: string | null) {
    const state = [...this.executions.values()].find((item) => item.responseId === responseId);
    if (state) state.status = "response_completed";
  }

  hasAwaitingContinuation() {
    return [...this.executions.values()].some(
      (item) => item.status === "result_submitted" && item.responseId === null,
    );
  }

  status(callId: string | null): ToolExecutionStatus | null {
    return callId ? this.executions.get(callId)?.status ?? null : null;
  }
}
