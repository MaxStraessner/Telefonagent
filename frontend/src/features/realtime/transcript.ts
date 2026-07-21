import type { TranscriptEntry, TranscriptSpeaker, TranscriptStatus } from "./types";

interface HistoryContent {
  type?: string;
  text?: string;
  transcript?: string;
}

interface HistoryItem {
  itemId?: string;
  id?: string;
  type?: string;
  role?: string;
  status?: string;
  content?: HistoryContent[];
}

function transcriptText(item: HistoryItem): string {
  return (item.content ?? [])
    .map((content) => content.transcript ?? content.text ?? "")
    .join("")
    .trim();
}

function transcriptStatus(item: HistoryItem, speaker: TranscriptSpeaker): TranscriptStatus {
  if (speaker === "assistant" && item.status === "incomplete") return "interrupted";
  return item.status === "completed" ? "completed" : "partial";
}

export function mapRealtimeHistory(history: readonly unknown[], previous: readonly TranscriptEntry[] = [], now = Date.now()): TranscriptEntry[] {
  const previousById = new Map(previous.map((entry) => [entry.id, entry]));
  return history.flatMap((rawItem, index) => {
    const item = rawItem as HistoryItem;
    if (item.type !== "message" || (item.role !== "user" && item.role !== "assistant")) return [];
    const text = transcriptText(item);
    if (!text) return [];
    const id = item.itemId ?? item.id ?? `${item.role}-${index}`;
    const old = previousById.get(id);
    const speaker = item.role;
    return [{ id, speaker, text, status: transcriptStatus(item, speaker), startedAt: old?.startedAt ?? now + index }];
  });
}
