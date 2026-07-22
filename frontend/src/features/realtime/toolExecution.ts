export type ConversationRuntimeState = "idle" | "generation_running" | "tool_running" | "tool_result_ready" | "continuation_starting" | "continuation_running" | "playback_running";

export interface ToolContinuationState {
  callId: string;
  toolName: string;
  startedAt: number;
  resultSentAt: number | null;
  continuationStartedAt: number | null;
  responseId: string | null;
  completed: boolean;
}

export class RealtimeToolExecutor {
  private readonly pending = new Map<string, Promise<unknown>>();
  private readonly continuation = new Map<string, ToolContinuationState>();
  private runtimeState: ConversationRuntimeState = "idle";

  constructor(
    readonly sessionId: string,
    private readonly onEvent: (type: string, detail?: string) => void,
    private readonly onRuntimeState: (state: ConversationRuntimeState) => void,
  ) {}

  setRuntimeState(state: ConversationRuntimeState) {
    this.runtimeState = state;
    this.onRuntimeState(state);
  }

  execute<T>(callId: string, toolName: string, action: () => Promise<T>): Promise<T> {
    const duplicate = this.pending.get(callId);
    if (duplicate) return duplicate as Promise<T>;
    const state: ToolContinuationState = {
      callId, toolName, startedAt: performance.now(), resultSentAt: null,
      continuationStartedAt: null, responseId: null, completed: false,
    };
    this.continuation.set(callId, state);
    this.setRuntimeState("tool_running");
    this.onEvent("tool_started", JSON.stringify({ sessionId: this.sessionId, toolCallId: callId, toolName }));
    const execution = action().then((result) => {
      state.resultSentAt = performance.now();
      state.continuationStartedAt = state.resultSentAt;
      this.setRuntimeState("tool_result_ready");
      this.onEvent("tool_completed", JSON.stringify({ sessionId: this.sessionId, toolCallId: callId, toolName }));
      this.setRuntimeState("continuation_starting");
      this.onEvent("tool_result_sent", JSON.stringify({
        sessionId: this.sessionId, toolCallId: callId, toolName,
        durationMs: Math.round(state.resultSentAt - state.startedAt), continuationMode: "sdk_automatic",
      }));
      return result;
    }).catch((error) => {
      state.completed = true;
      this.setRuntimeState("idle");
      this.onEvent("tool_failed", JSON.stringify({ sessionId: this.sessionId, toolCallId: callId, toolName }));
      throw error;
    });
    this.pending.set(callId, execution);
    return execution;
  }

  attachContinuationResponse(responseId: string) {
    const state = [...this.continuation.values()].reverse().find((item) => item.continuationStartedAt !== null && item.responseId === null);
    if (!state) return;
    state.responseId = responseId;
    this.setRuntimeState("continuation_running");
    this.onEvent("tool_continuation_response_created", JSON.stringify({ toolCallId: state.callId, responseId }));
  }

  completeResponse(responseId: string | null) {
    const state = [...this.continuation.values()].find((item) => item.responseId === responseId);
    if (state) state.completed = true;
  }
}
