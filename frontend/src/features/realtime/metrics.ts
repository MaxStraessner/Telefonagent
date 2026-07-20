import type { LatencyMetrics } from "./types";

export const emptyLatencyMetrics: LatencyMetrics = {
  connectionMs: null,
  lastResponseMs: null,
  averageResponseMs: null,
  minimumResponseMs: null,
  maximumResponseMs: null,
  responseCount: 0,
  sessionDurationSeconds: 0,
};

export class RealtimeMetricsTracker {
  private speechStoppedAt: number | null = null;
  private samples: number[] = [];
  private startedAt: number | null = null;
  private connectionMs: number | null = null;

  start(now = performance.now()) {
    this.startedAt = now;
  }

  connected(now = performance.now()) {
    if (this.startedAt !== null) this.connectionMs = Math.max(0, Math.round(now - this.startedAt));
  }

  userSpeechStopped(now = performance.now()) {
    this.speechStoppedAt = now;
  }

  assistantAudioPlaying(now = performance.now()) {
    if (this.speechStoppedAt === null) return;
    this.samples.push(Math.max(0, Math.round(now - this.speechStoppedAt)));
    this.speechStoppedAt = null;
  }

  snapshot(now = performance.now()): LatencyMetrics {
    const total = this.samples.reduce((sum, sample) => sum + sample, 0);
    return {
      connectionMs: this.connectionMs,
      lastResponseMs: this.samples.at(-1) ?? null,
      averageResponseMs: this.samples.length ? Math.round(total / this.samples.length) : null,
      minimumResponseMs: this.samples.length ? Math.min(...this.samples) : null,
      maximumResponseMs: this.samples.length ? Math.max(...this.samples) : null,
      responseCount: this.samples.length,
      sessionDurationSeconds: this.startedAt === null ? 0 : Math.max(0, Math.floor((now - this.startedAt) / 1000)),
    };
  }
}
