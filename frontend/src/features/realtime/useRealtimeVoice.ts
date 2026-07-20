import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { ConversationState } from "../conversation/state";
import { RealtimeMetricsTracker, emptyLatencyMetrics } from "./metrics";
import { BrowserRealtimeClient } from "./realtimeClient";
import { RealtimeClientError, realtimeErrors } from "./errors";
import { tickSessionTimer } from "./sessionTimer";
import { mapRealtimeHistory } from "./transcript";
import type { RealtimeViewState } from "./types";

const MAX_EVENTS = 40;

interface ReadableError {
  code: string;
  message: string;
}

function readableRealtimeError(error: unknown): ReadableError {
  if (error instanceof DOMException && error.name === "NotAllowedError") return { code: "microphone_permission_denied", message: "Der Mikrofonzugriff wurde verweigert. Bitte erlaube ihn in den Browser-Einstellungen und versuche es erneut." };
  if (error instanceof DOMException && error.name === "NotFoundError") return { code: "microphone_not_found", message: "Es wurde kein verfügbares Mikrofon gefunden." };
  if (error instanceof DOMException && error.name === "NotReadableError") return { code: "microphone_not_readable", message: "Das Mikrofon ist derzeit blockiert oder wird von einer anderen Anwendung verwendet." };
  if (error instanceof ApiError) {
    if (error.code === "realtime_not_configured") return { code: error.code, message: "OpenAI Realtime ist serverseitig noch nicht konfiguriert." };
    if (error.code === "realtime_provider_timeout") return { code: error.code, message: "OpenAI Realtime antwortet nicht rechtzeitig. Bitte versuche es erneut." };
    if (error.code === "realtime_model_unavailable") return { code: error.code, message: "Das konfigurierte Realtime-Modell ist für dieses OpenAI-Projekt nicht verfügbar." };
    if (error.code === "realtime_voice_unavailable") return { code: error.code, message: "Die konfigurierte Realtime-Stimme ist für dieses OpenAI-Projekt nicht verfügbar." };
    return { code: error.code ?? "backend_request_failed", message: error.message };
  }
  if (error instanceof RealtimeClientError) {
    const messages: Record<string, string> = {
      audio_element_unavailable: "Die Audioausgabe konnte nicht vorbereitet werden. Bitte lade die Seite neu.",
      audio_playback_blocked: "Die Audioausgabe wurde vom Browser blockiert. Bitte erlaube Ton für diese Seite und starte das Gespräch erneut.",
      browser_insecure_context: "Die Sprachfunktion benötigt HTTPS oder localhost als sicheren Browserkontext.",
      browser_unsupported: "Dieser Browser unterstützt die benötigten WebRTC- und Medienfunktionen nicht.",
      microphone_access_ended: "Der Mikrofonzugriff wurde während des Gesprächs beendet.",
      realtime_client_secret_expired: "Das kurzlebige Verbindungs-Token ist abgelaufen. Bitte starte das Testgespräch erneut.",
      realtime_configuration_mismatch: "Die Realtime-Konfiguration hat sich während des Starts geändert. Bitte versuche es erneut.",
      realtime_connection_lost: "Die WebRTC-Sprachverbindung wurde unterbrochen. Bitte prüfe dein Netzwerk und starte erneut.",
      realtime_connection_timeout: "Der Aufbau der Sprachverbindung hat zu lange gedauert. Bitte versuche es erneut.",
    };
    return { code: error.code, message: messages[error.code] ?? "Die Sprachverbindung wurde unerwartet beendet. Bitte starte das Testgespräch erneut." };
  }
  const message = error instanceof Error ? error.message : String(error ?? "");
  if (/expired|client.?secret|ephemeral/i.test(message)) return { code: "realtime_client_secret_expired", message: "Das kurzlebige Verbindungs-Token ist abgelaufen. Bitte starte das Testgespräch erneut." };
  if (/webrtc|peer.?connection|ice|data.?channel|connection/i.test(message)) return { code: "webrtc_connection_failed", message: "Die WebRTC-Sprachverbindung konnte nicht aufgebaut werden. Bitte prüfe Netzwerk und Browserfreigaben." };
  return { code: "realtime_unexpected_error", message: "Die Sprachverbindung wurde unerwartet beendet. Bitte starte das Testgespräch erneut." };
}

const initialState: RealtimeViewState = {
  state: "idle",
  muted: false,
  transcript: [],
  events: [],
  metrics: emptyLatencyMetrics,
  error: null,
  errorCode: null,
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
  const attemptRef = useRef(0);

  const clearTimer = useCallback(() => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
    intervalRef.current = null;
  }, []);

  const closeResources = useCallback(() => {
    attemptRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    clearTimer();
    clientRef.current?.close();
    clientRef.current = null;
    startingRef.current = false;
    remainingRef.current = null;
  }, [clearTimer]);

  const fail = useCallback((error: unknown) => {
    const readable = readableRealtimeError(error);
    closeResources();
    if (!mountedRef.current) return;
    setView((current) => ({ ...current, state: "error", muted: false, error: readable.message, errorCode: readable.code, remainingSeconds: null }));
  }, [closeResources]);

  const addEvent = useCallback((type: string, detail?: string) => {
    if (!mountedRef.current) return;
    setView((current) => ({
      ...current,
      events: [...current.events, { id: `${Date.now()}-${Math.random()}`, type, detail, timestamp: Date.now() }].slice(-MAX_EVENTS),
    }));
  }, []);

  const start = useCallback(async () => {
    if (startingRef.current || clientRef.current || !configured) return;
    if (!audioElement) {
      fail(realtimeErrors.audioElementUnavailable());
      return;
    }
    const localHostname = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
    if (!window.isSecureContext && !localHostname) {
      fail(realtimeErrors.insecureContext());
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === "undefined") {
      fail(realtimeErrors.browserUnsupported());
      return;
    }
    const attempt = attemptRef.current + 1;
    attemptRef.current = attempt;
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
      if (abortController.signal.aborted || attemptRef.current !== attempt) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      addEvent("microphone.permission_granted");
      setView((current) => ({ ...current, state: "connecting", notice: "Sprachverbindung wird aufgebaut …" }));
      const [agentConfig, secret] = await Promise.all([
        api.realtimeAgentConfig(abortController.signal),
        api.realtimeClientSecret(abortController.signal),
      ]);
      if (abortController.signal.aborted || attemptRef.current !== attempt) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      addEvent("client_secret.received");
      if (secret.tenant_id !== agentConfig.tenant_id || secret.model !== agentConfig.model || secret.voice !== agentConfig.voice) {
        throw realtimeErrors.configurationMismatch();
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
        onResponseCompleted: (completed) => {
          metricsRef.current.responseCompleted(completed);
          setView((current) => ({ ...current, metrics: metricsRef.current.snapshot() }));
        },
        onCallId: (callId) => setView((current) => ({ ...current, callId })),
      });
      clientRef.current = client;
      await client.connect(agentConfig, secret, stream, audioElement);
      if (attemptRef.current !== attempt || clientRef.current !== client) {
        client.close();
        return;
      }

      const maximumSeconds = agentConfig.maximum_session_minutes * 60;
      remainingRef.current = maximumSeconds;
      setView((current) => ({
        ...current,
        remainingSeconds: maximumSeconds,
        vadSummary: `${agentConfig.vad.type} · Schwelle ${agentConfig.vad.threshold} · Präfix ${agentConfig.vad.prefix_padding_ms} ms · Stille ${agentConfig.vad.silence_duration_ms} ms · Antwort ${agentConfig.vad.create_response ? "an" : "aus"} · Unterbrechung ${agentConfig.vad.interrupt_response ? "an" : "aus"}`,
      }));
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
      if (attemptRef.current === attempt && !(error instanceof DOMException && error.name === "AbortError")) fail(error);
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
