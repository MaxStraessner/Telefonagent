import { describe, expect, it } from "vitest";
import { RealtimeMetricsTracker } from "../src/features/realtime/metrics";
import { normalizedRealtimeEventType, sanitizedRealtimeEventDetail } from "../src/features/realtime/events";
import { tickSessionTimer } from "../src/features/realtime/sessionTimer";
import { mapRealtimeHistory } from "../src/features/realtime/transcript";
import { derivePlaybackStatus, incompleteResponseWasInterrupted } from "../src/features/realtime/playback";
import { diagnoseResponseCompletion } from "../src/features/realtime/completionDiagnosis";

describe("Realtime transcript mapping", () => {
  it("führt partielle Einträge anhand der Item-ID ohne Duplikate fort", () => {
    const partial = mapRealtimeHistory([{ itemId: "u1", type: "message", role: "user", status: "in_progress", content: [{ type: "input_audio", transcript: "Guten" }] }], [], 100);
    const completed = mapRealtimeHistory([{ itemId: "u1", type: "message", role: "user", status: "completed", content: [{ type: "input_audio", transcript: "Guten Tag" }] }], partial, 200);
    expect(completed).toEqual([{ id: "u1", speaker: "user", text: "Guten Tag", status: "completed", startedAt: 100 }]);
  });

  it("leitet aus einem unvollständigen History-Eintrag allein keine Unterbrechung ab", () => {
    const result = mapRealtimeHistory([
      { itemId: "a1", type: "message", role: "assistant", status: "incomplete", content: [{ type: "output_audio", transcript: "Einen Moment" }] },
      { itemId: "tool", type: "function_call", role: "assistant" },
      { itemId: "empty", type: "message", role: "user", status: "completed", content: [] },
    ], [], 10);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ id: "a1", status: "partial", speaker: "assistant" });
  });

  it("bewahrt einen durch echte Ereignisse belegten Unterbrechungsstatus", () => {
    const previous = [{ id: "a1", speaker: "assistant" as const, text: "Einen Moment", status: "interrupted" as const, startedAt: 10 }];
    const result = mapRealtimeHistory([
      { itemId: "a1", type: "message", role: "assistant", status: "incomplete", content: [{ type: "output_audio", transcript: "Einen Moment" }] },
    ], previous, 20);
    expect(result[0].status).toBe("interrupted");
  });
});

describe("Realtime playback status", () => {
  it("unterscheidet Erzeugung, Wiedergabe, Abschluss und echten Abbruch", () => {
    expect(derivePlaybackStatus({})).toBe("generating");
    expect(derivePlaybackStatus({ bufferStarted: true, responseCompleted: true, responseStatus: "completed" })).toBe("playing");
    expect(derivePlaybackStatus({ bufferStarted: true, responseCompleted: true, responseStatus: "completed", actualBufferStopped: true })).toBe("completed");
    expect(derivePlaybackStatus({ responseCompleted: true, responseStatus: "completed", actualBufferStopped: true })).toBe("completed");
    expect(derivePlaybackStatus({ responseStatus: "cancelled" })).toBe("interrupted");
    expect(derivePlaybackStatus({ bufferCleared: true })).toBe("interrupted");
  });

  it("wertet incomplete nur mit passendem Abbruchgrund als Unterbrechung", () => {
    expect(incompleteResponseWasInterrupted({ status: "incomplete" })).toBe(false);
    expect(incompleteResponseWasInterrupted({ status: "incomplete", status_details: { reason: "turn_detected" } })).toBe(true);
    expect(incompleteResponseWasInterrupted({ status: "completed" })).toBe(false);
  });
});

describe("Realtime completion diagnosis", () => {
  it("unterscheidet Ausgabelimit, unvollständigen Tool Call, Inhaltsfilter und echten Abbruch", () => {
    expect(diagnoseResponseCompletion({ status: "incomplete", status_details: { reason: "max_output_tokens" } })).toMatchObject({ reason: "output_token_limit", recoverable: true });
    expect(diagnoseResponseCompletion({ status: "incomplete" }, true, false)).toMatchObject({ reason: "incomplete_function_call", recoverable: true });
    expect(diagnoseResponseCompletion({ status: "incomplete", status_details: { reason: "content_filter" } })).toMatchObject({ reason: "content_filter", recoverable: false });
    expect(diagnoseResponseCompletion({ status: "incomplete", status_details: { reason: "turn_detected" } })).toMatchObject({ reason: "interrupted", interruption: true });
  });
});

describe("Realtime session limit", () => {
  it("warnt genau eine Minute vor Schluss und markiert das kontrollierte Ende", () => {
    expect(tickSessionTimer(62, false)).toEqual({ remainingSeconds: 61, showOneMinuteWarning: false, expired: false });
    expect(tickSessionTimer(61, false)).toEqual({ remainingSeconds: 60, showOneMinuteWarning: true, expired: false });
    expect(tickSessionTimer(60, true)).toEqual({ remainingSeconds: 59, showOneMinuteWarning: false, expired: false });
    expect(tickSessionTimer(1, true)).toEqual({ remainingSeconds: 0, showOneMinuteWarning: false, expired: true });
  });
});

describe("Realtime latency metrics", () => {
  it("berechnet Verbindung sowie Antwort-Minimum, Mittel und Maximum", () => {
    const tracker = new RealtimeMetricsTracker();
    tracker.start(100);
    tracker.connected(350);
    tracker.userSpeechStopped(500);
    tracker.assistantAudioPlaying(900);
    tracker.userSpeechStopped(1000);
    tracker.assistantAudioPlaying(1600);
    tracker.responseCompleted(true);
    expect(tracker.snapshot(2100)).toEqual({
      connectionMs: 250,
      lastResponseMs: 600,
      averageResponseMs: 500,
      minimumResponseMs: 400,
      maximumResponseMs: 600,
      responseCount: 2,
      completedRounds: 1,
      sessionDurationSeconds: 2,
    });
  });
});

describe("Realtime diagnostic events", () => {
  it("normalisiert Pflicht-Ereignisse und entfernt Inhalte sowie Geheimnisfelder", () => {
    expect(normalizedRealtimeEventType("input_audio_buffer.speech_started")).toBe("speech_started");
    expect(normalizedRealtimeEventType("response.done")).toBe("response_completed");
    const detail = sanitizedRealtimeEventDetail({ type: "response.done", transcript: "privat", client_secret: "ek_secret", response: { id: "r1", status: "completed" } });
    expect(detail).toContain("[redacted]");
    expect(detail).toContain("r1");
    expect(detail).not.toContain("privat");
    expect(detail).not.toContain("ek_secret");
  });
});
