export interface ToolContinuationState {
  callId: string;
  toolName: string;
  startedAt: number;
  resultSentAt: number | null;
  continuationStartedAt: number | null;
  responseId: string | null;
  completed: boolean;
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
  private readonly continuation = new Map<string, ToolContinuationState>();

  constructor(
    readonly sessionId: string,
    private readonly onEvent: (type: string, detail?: string) => void,
    private readonly onToolStarted: (callId: string) => void,
    private readonly onToolResultReady: (callId: string, result: unknown) => Promise<void>,
  ) {}

  execute<T>(callId: string, toolName: string, action: () => Promise<T>): Promise<T | NormalizedToolFailure> {
    const duplicate = this.pending.get(callId);
    if (duplicate) return duplicate as Promise<T | NormalizedToolFailure>;
    const state: ToolContinuationState = {
      callId, toolName, startedAt: performance.now(), resultSentAt: null,
      continuationStartedAt: null, responseId: null, completed: false,
    };
    this.continuation.set(callId, state);
    this.onToolStarted(callId);
    this.onEvent("tool_started", JSON.stringify({ sessionId: this.sessionId, toolCallId: callId, toolName }));
    const finish = async (result: T | NormalizedToolFailure, success: boolean) => {
      state.resultSentAt = performance.now();
      state.continuationStartedAt = state.resultSentAt;
      await this.onToolResultReady(callId, result);
      this.onEvent("tool_completed", JSON.stringify({
        sessionId: this.sessionId, toolCallId: callId, toolName, success,
        ...(!success && typeof result === "object" && result !== null && "error_code" in result
          ? { errorCode: result.error_code }
          : {}),
      }));
      this.onEvent("tool_result_sent", JSON.stringify({
        sessionId: this.sessionId, toolCallId: callId, toolName,
        durationMs: Math.round(state.resultSentAt - state.startedAt), continuationMode: "sdk_automatic", success,
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

  attachContinuationResponse(responseId: string) {
    const state = [...this.continuation.values()].reverse().find((item) => item.continuationStartedAt !== null && item.responseId === null);
    if (!state) return;
    state.responseId = responseId;
      this.onEvent("tool_continuation_response_created", JSON.stringify({ toolCallId: state.callId, responseId }));
  }

  completeResponse(responseId: string | null) {
    const state = [...this.continuation.values()].find((item) => item.responseId === responseId);
    if (state) state.completed = true;
  }

  hasAwaitingContinuation() {
    return [...this.continuation.values()].some((item) =>
      item.continuationStartedAt !== null && item.responseId === null && !item.completed
    );
  }
}
