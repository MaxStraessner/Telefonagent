export type ConversationState = "idle" | "connecting" | "connected" | "user_speaking" | "assistant_thinking" | "assistant_speaking" | "tool_running" | "error" | "ended" | "not_configured";

export interface ConversationSession {
  state: ConversationState;
  muted: boolean;
  transcript: readonly { speaker: "user" | "assistant"; text: string }[];
  recognizedAppointment: { service?: string; staff?: string; startsAt?: string } | null;
  toolExecutions: readonly { name: string; status: string; durationMs?: number }[];
}

export const initialConversation: ConversationSession = { state: "not_configured", muted: false, transcript: [], recognizedAppointment: null, toolExecutions: [] };

