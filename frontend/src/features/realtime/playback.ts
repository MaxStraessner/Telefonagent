export type PlaybackStatus = "generating" | "playing" | "completed" | "interrupted" | "failed";

export interface PlaybackEvidence {
  responseStatus?: string | null;
  responseCompleted?: boolean;
  actualBufferStopped?: boolean;
  bufferCleared?: boolean;
  itemTruncated?: boolean;
  explicitlyCancelled?: boolean;
  failed?: boolean;
  bufferStarted?: boolean;
}

export function derivePlaybackStatus(evidence: PlaybackEvidence): PlaybackStatus {
  if (evidence.failed) return "failed";
  if (
    evidence.explicitlyCancelled || evidence.bufferCleared || evidence.itemTruncated ||
    evidence.responseStatus === "cancelled"
  ) return "interrupted";
  if (
    evidence.responseCompleted && evidence.responseStatus === "completed" && evidence.actualBufferStopped
  ) return "completed";
  if (evidence.bufferStarted || evidence.actualBufferStopped) return "playing";
  return evidence.responseCompleted ? "playing" : "generating";
}

export function incompleteResponseWasInterrupted(response: unknown): boolean {
  if (!response || typeof response !== "object") return false;
  const value = response as { status?: string; status_details?: unknown; incomplete_details?: unknown };
  if (value.status === "cancelled") return true;
  if (value.status !== "incomplete") return false;
  const detail = JSON.stringify(value.status_details ?? value.incomplete_details ?? "");
  return /cancel|interrupt|turn_detected|truncat/i.test(detail);
}
