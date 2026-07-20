import { describe, expect, it } from "vitest";
import { RealtimeMetricsTracker } from "../src/features/realtime/metrics";
import { tickSessionTimer } from "../src/features/realtime/sessionTimer";
import { mapRealtimeHistory } from "../src/features/realtime/transcript";

describe("Realtime transcript mapping", () => {
  it("führt partielle Einträge anhand der Item-ID ohne Duplikate fort", () => {
    const partial = mapRealtimeHistory([{ itemId: "u1", type: "message", role: "user", status: "in_progress", content: [{ type: "input_audio", transcript: "Guten" }] }], [], 100);
    const completed = mapRealtimeHistory([{ itemId: "u1", type: "message", role: "user", status: "completed", content: [{ type: "input_audio", transcript: "Guten Tag" }] }], partial, 200);
    expect(completed).toEqual([{ id: "u1", speaker: "user", text: "Guten Tag", status: "completed", startedAt: 100 }]);
  });

  it("markiert abgebrochene Assistentenantworten und ignoriert leere Nicht-Nachrichten", () => {
    const result = mapRealtimeHistory([
      { itemId: "a1", type: "message", role: "assistant", status: "incomplete", content: [{ type: "output_audio", transcript: "Einen Moment" }] },
      { itemId: "tool", type: "function_call", role: "assistant" },
      { itemId: "empty", type: "message", role: "user", status: "completed", content: [] },
    ], [], 10);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ id: "a1", status: "interrupted", speaker: "assistant" });
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
    expect(tracker.snapshot(2100)).toEqual({
      connectionMs: 250,
      lastResponseMs: 600,
      averageResponseMs: 500,
      minimumResponseMs: 400,
      maximumResponseMs: 600,
      responseCount: 2,
      sessionDurationSeconds: 2,
    });
  });
});
