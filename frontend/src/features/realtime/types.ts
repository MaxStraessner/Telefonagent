import type { ConversationState } from "../conversation/state";

export type TranscriptSpeaker = "user" | "assistant";
export type TranscriptStatus = "partial" | "completed" | "interrupted";
export type { PlaybackStatus } from "./playback";

export interface TranscriptEntry {
  id: string;
  speaker: TranscriptSpeaker;
  text: string;
  status: TranscriptStatus;
  startedAt: number;
}

export interface RealtimeEventSummary {
  id: string;
  type: string;
  timestamp: number;
  detail?: string;
}

export interface LatencyMetrics {
  connectionMs: number | null;
  lastResponseMs: number | null;
  averageResponseMs: number | null;
  minimumResponseMs: number | null;
  maximumResponseMs: number | null;
  responseCount: number;
  completedRounds: number;
  sessionDurationSeconds: number;
}

export interface RealtimeViewState {
  state: ConversationState;
  muted: boolean;
  transcript: TranscriptEntry[];
  events: RealtimeEventSummary[];
  metrics: LatencyMetrics;
  error: string | null;
  errorCode: string | null;
  notice: string | null;
  callId: string | null;
  remainingSeconds: number | null;
  vadSummary: string | null;
  playbackStatus: import("./playback").PlaybackStatus | null;
}
