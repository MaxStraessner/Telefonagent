import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { ConversationState } from "../conversation/state";
import { RealtimeMetricsTracker, emptyLatencyMetrics } from "./metrics";
import { BrowserRealtimeClient } from "./realtimeClient";
import { tickSessionTimer } from "./sessionTimer";
import { mapRealtimeHistory } from "./transcript";
import type { RealtimeViewState } from "./types";

const MAX_EVENTS = 40;

function readableRealtimeError(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") return "Der Mikrofonzugriff wurde verweigert. Bitte erlaube ihn in den Browser-Einstellungen und versuche es erneut.";
  if (error instanceof DOMException && error.name === "NotFoundError") return "Es wurde kein verfügbares Mikrofon gefunden.";
  if (error instanceof DOMException && error.name === "NotReadableError") return "Das Mikrofon ist derzeit blockiert oder wird von einer anderen Anwendung verwendet.";
  if (error instanceof ApiError) {
    if (error.code === "realtime_not_configured") return "OpenAI Realtime ist serverseitig noch nicht konfiguriert.";
    if (error.code === "realtime_provider_timeout") return "OpenAI Realtime antwortet nicht rechtzeitig. Bitte versuche es erneut.";
    return error.message;
  }
  const message = error instanceof Error ? error.message : String(error ?? "");
  if (/microphone track ended/i.test(message)) return "Der Mikrofonzugriff wurde während des Gesprächs beendet.";
  if (/expired|client.?secret|ephemeral/i.test(message)) return "Das kurzlebige Verbindungs-Token ist abgelaufen. Bitte starte das Testgespräch erneut.";
  if (/webrtc|peer.?connection|ice|data.?channel|connection/i.test(message)) return "Die WebRTC-Sprachverbindung konnte nicht aufgebaut werden. Bitte prüfe Netzwerk und Browserfreigaben.";
  return "Die Sprachverbindung wurde unerwartet beendet. Bitte starte das Testgespräch erneut.";
}

const initialState: RealtimeViewState = {
  state: "idle",
  muted: false,
  transcript: [],
  events: [],
  metrics: emptyLatencyMetrics,
  error: null,
  notice: null,
  callId: null,
  remainingSeconds: null,
  vadSummary: null,
};

export function useRealtimeVoice(configured: boolean, audioElement: HTMLAudioElement | null) {
  const [view, setView] = useState<RealtimeViewState>(() => ({ ...initialState, state: configured ? "idle" : "not_configured" }));
  const clientRef = useRef<BrowserRealtimeClient | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const intervalRef = useRef<number | null>(null);
  const metricsRef = useRef(new RealtimeMetricsTracker());
  const startingRef = useRef(false);
  const mountedRef = useRef(true);
  const warnedRef = useRef(false);
  const remainingRef = useRef<number | null>(null);
  const clearedTranscriptIdsRef = useRef(new Set<string>());

  const clearTimer = useCallback(() => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
    intervalRef.current = null;
  }, []);

  const closeResources = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    clearTimer();
    clientRef.current?.close();
    clientRef.current = null;
    startingRef.current = false;
  }, [clearTimer]);

  const fail = useCallback((error: unknown) => {
    closeResources();
    if (!mountedRef.current) return;
    setView((current) => ({ ...current, state: "error", muted: false, error: readableRealtimeError(error), remainingSeconds: null }));
  }, [closeResources]);

  const addEvent = useCallback((type: string, detail?: string) => {
    setView((current) => ({
      ...current,
      events: [...current.events, { id: `${Date.now()}-${Math.random()}`, type, detail, timestamp: Date.now() }].slice(-MAX_EVENTS),
    }));
  }, []);

  const start = useCallback(async () => {
    if (startingRef.current || clientRef.current || !configured) return;
    if (!audioElement) {
      fail(new Error("Audio element unavailable"));
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      fail(new Error("WebRTC microphone unavailable"));
      return;
    }
    startingRef.current = true;
    warnedRef.current = false;
    clearedTranscriptIdsRef.current.clear();
    metricsRef.current = new RealtimeMetricsTracker();
    metricsRef.current.start();
    setView({ ...initialState, state: "requesting_microphone" });

    let stream: MediaStream | null = null;
    const abortController = new AbortController();
    abortRef.current = abortController;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
      if (abortController.signal.aborted) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      addEvent("microphone.permission_granted");
      setView((current) => ({ ...current, state: "connecting", notice: "Sichere WebRTC-Verbindung wird aufgebaut …" }));
      const [agentConfig, secret] = await Promise.all([
        api.realtimeAgentConfig(abortController.signal),
        api.realtimeClientSecret(abortController.signal),
      ]);
      addEvent("client_secret.received");
      if (secret.tenant_id !== agentConfig.tenant_id || secret.model !== agentConfig.model || secret.voice !== agentConfig.voice) {
        throw new Error("Realtime configuration mismatch");
      }

      const client = new BrowserRealtimeClient({
        onState: (state: ConversationState) => setView((current) => {
          if (current.muted && state === "user_speaking") return current;
          return { ...current, state: current.muted && state === "connected" ? "muted" : state, muted: state === "muted" ? true : current.muted };
        }),
        onHistory: (history) => setView((current) => ({ ...current, transcript: mapRealtimeHistory(history, current.transcript).filter((entry) => !clearedTranscriptIdsRef.current.has(entry.id)) })),
        onEvent: addEvent,
        onError: fail,
        onConnected: () => {
          startingRef.current = false;
          metricsRef.current.connected();
          setView((current) => ({ ...current, metrics: metricsRef.current.snapshot(), notice: "Die Verbindung steht. Du kannst jetzt sprechen." }));
        },
        onUserSpeechStopped: () => metricsRef.current.userSpeechStopped(),
        onAssistantAudioPlaying: () => {
          metricsRef.current.assistantAudioPlaying();
          setView((current) => ({ ...current, metrics: metricsRef.current.snapshot() }));
        },
        onCallId: (callId) => setView((current) => ({ ...current, callId })),
      });
      clientRef.current = client;
      await client.connect(agentConfig, secret, stream, audioElement);

      const maximumSeconds = agentConfig.maximum_session_minutes * 60;
      remainingRef.current = maximumSeconds;
      setView((current) => ({ ...current, remainingSeconds: maximumSeconds, vadSummary: `${agentConfig.vad.type} · ${agentConfig.vad.silence_duration_ms} ms Pause · Schwelle ${agentConfig.vad.threshold}` }));
      intervalRef.current = window.setInterval(() => {
        const tick = tickSessionTimer(remainingRef.current ?? maximumSeconds, warnedRef.current);
        const remaining = tick.remainingSeconds;
        remainingRef.current = remaining;
        const warning = tick.showOneMinuteWarning;
        if (warning) warnedRef.current = true;
        setView((current) => ({
          ...current,
          remainingSeconds: remaining,
          notice: warning ? "Das Testgespräch endet automatisch in einer Minute." : current.notice,
          metrics: metricsRef.current.snapshot(),
        }));
        if (tick.expired) {
          closeResources();
          remainingRef.current = null;
          if (mountedRef.current) setView((current) => ({ ...current, state: "ended", muted: false, remainingSeconds: null, notice: "Die maximale Testdauer wurde erreicht." }));
        }
      }, 1000);
    } catch (error) {
      if (!clientRef.current) stream?.getTracks().forEach((track) => track.stop());
      if (!(error instanceof DOMException && error.name === "AbortError")) fail(error);
    }
  }, [addEvent, audioElement, closeResources, configured, fail]);

  const end = useCallback(() => {
    closeResources();
    setView((current) => ({ ...current, state: "ended", muted: false, notice: "Testgespräch beendet.", remainingSeconds: null }));
  }, [closeResources]);

  const toggleMute = useCallback(() => {
    if (!clientRef.current) return;
    const muted = !view.muted;
    clientRef.current.mute(muted);
    setView((current) => ({ ...current, muted, state: muted ? "muted" : "connected" }));
  }, [view.muted]);

  const interrupt = useCallback(() => clientRef.current?.interrupt(), []);
  const dismissNotice = useCallback(() => setView((current) => ({ ...current, notice: null })), []);
  const clearTranscript = useCallback(() => setView((current) => {
    current.transcript.forEach((entry) => clearedTranscriptIdsRef.current.add(entry.id));
    return { ...current, transcript: [] };
  }), []);

  useEffect(() => {
    setView((current) => {
      if (clientRef.current || startingRef.current) return current;
      return { ...current, state: configured ? (current.state === "not_configured" ? "idle" : current.state) : "not_configured" };
    });
  }, [configured]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      closeResources();
    };
  }, [closeResources]);

  return { view, start, end, toggleMute, interrupt, dismissNotice, clearTranscript };
}
