import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { RealtimeAttemptFinish } from "../../types/api";
import type { ConversationState } from "../conversation/state";
import { RealtimeMetricsTracker, emptyLatencyMetrics } from "./metrics";
import { BrowserRealtimeClient } from "./realtimeClient";
import { RealtimeClientError, realtimeErrors } from "./errors";
import { tickSessionTimer } from "./sessionTimer";
import { mapRealtimeHistory } from "./transcript";
import type { RealtimeViewState } from "./types";

const MAX_EVENTS = 40;
const ACTIVE_ATTEMPT_STORAGE_KEY = "telefonagent.realtime.active_call_attempt";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LIFECYCLE_EVENTS = new Set([
  "call_start_requested",
  "local_session_state_checked",
  "backend_session_request_started",
  "session_bootstrap_started",
  "session_bootstrap_succeeded",
  "signaling_started",
  "signaling_succeeded",
  "signaling_failed",
  "session_configuration_started",
  "session_configuration_succeeded",
  "session_configuration_failed",
  "call_connected",
  "call_end_requested",
  "session_cleanup_started",
  "session_cleanup_completed",
  "session_cleanup_failed",
  "session_state_reset",
]);

type RealtimePhase =
  | "local_validation"
  | "microphone"
  | "session_bootstrap"
  | "signaling"
  | "session_configuration"
  | "lifecycle_sync"
  | "conversation"
  | "cleanup"
  | "browser_reload";

interface ReadableError {
  code: string;
  message: string;
  diagnostic: RealtimeAttemptFinish;
}

function safeTechnicalMessage(value: unknown): string {
  return String(value ?? "")
    .replace(/\r|\n/g, " ")
    .replace(/\b(?:Bearer\s+)?(?:sk|ek)[-_][a-z0-9_-]{8,}\b/gi, "[REDACTED_CREDENTIAL]")
    .trim()
    .slice(0, 500);
}

function userMessageForClientError(code: string): string {
  const messages: Record<string, string> = {
    audio_element_unavailable: "Die Audioausgabe konnte nicht vorbereitet werden. Bitte lade die Seite neu.",
    audio_playback_blocked: "Die Audioausgabe wurde vom Browser blockiert. Bitte erlaube Ton für diese Seite und starte das Gespräch erneut.",
    browser_insecure_context: "Die Sprachfunktion benötigt HTTPS oder localhost als sicheren Browserkontext.",
    browser_unsupported: "Dieser Browser unterstützt die benötigten WebRTC- und Medienfunktionen nicht.",
    microphone_access_ended: "Der Mikrofonzugriff wurde während des Gesprächs beendet.",
    realtime_bootstrap_mismatch: "Die Realtime-Startdaten sind widersprüchlich. Bitte starte das Testgespräch erneut.",
    realtime_client_secret_expired: "Das kurzlebige Verbindungs-Token ist abgelaufen. Bitte starte das Testgespräch erneut.",
    realtime_configuration_ack_timeout: "OpenAI hat die aktive Sitzungskonfiguration nicht rechtzeitig bestätigt. Bitte starte das Testgespräch erneut.",
    realtime_connection_lost: "Die WebRTC-Sprachverbindung wurde unterbrochen. Bitte prüfe dein Netzwerk und starte erneut.",
    realtime_connection_timeout: "Der Aufbau der Sprachverbindung hat zu lange gedauert. Bitte versuche es erneut.",
    realtime_signaling_failed: "Die WebRTC-Sprachverbindung konnte nicht aufgebaut werden. Bitte prüfe Netzwerk und Browserfreigaben.",
    realtime_provider_request_failed: "OpenAI hat die laufende Realtime-Anfrage abgelehnt. Bitte starte das Testgespräch erneut.",
    realtime_response_create_rejected: "OpenAI hat einen konkurrierenden Antwortstart abgelehnt. Die Sitzung wurde sicher beendet; bitte starte erneut.",
  };
  return messages[code] ?? "Die Sprachverbindung wurde unerwartet beendet. Bitte starte das Testgespräch erneut.";
}

function readableRealtimeError(
  error: unknown,
  callAttemptId: string | null,
  phase: RealtimePhase,
): ReadableError {
  let code = "realtime_unexpected_error";
  let message = "Die Sprachverbindung wurde unerwartet beendet. Bitte starte das Testgespräch erneut.";
  let httpStatus: number | null = null;
  let providerRequestId: string | null = null;
  let retryable = false;
  let technicalMessage: string;

  if (error instanceof DOMException && error.name === "NotAllowedError") {
    code = "microphone_permission_denied";
    message = "Der Mikrofonzugriff wurde verweigert. Bitte erlaube ihn in den Browser-Einstellungen und versuche es erneut.";
    technicalMessage = `${error.name}: microphone permission denied`;
  } else if (error instanceof DOMException && error.name === "NotFoundError") {
    code = "microphone_not_found";
    message = "Es wurde kein verfügbares Mikrofon gefunden.";
    technicalMessage = `${error.name}: no microphone found`;
  } else if (error instanceof DOMException && error.name === "NotReadableError") {
    code = "microphone_not_readable";
    message = "Das Mikrofon ist derzeit blockiert oder wird von einer anderen Anwendung verwendet.";
    technicalMessage = `${error.name}: microphone is not readable`;
  } else if (error instanceof ApiError) {
    code = error.code ?? "backend_request_failed";
    httpStatus = error.status ?? null;
    retryable = error.status === undefined || error.status >= 500;
    technicalMessage = `Backend request failed: status=${error.status ?? "network"} code=${code}`;
    if (error.code === "realtime_not_configured") {
      message = "OpenAI Realtime ist serverseitig noch nicht konfiguriert.";
    } else if (error.code === "realtime_provider_timeout") {
      message = "OpenAI Realtime antwortet nicht rechtzeitig. Bitte versuche es erneut.";
    } else if (error.code === "realtime_model_unavailable") {
      message = "Das konfigurierte Realtime-Modell ist für dieses OpenAI-Projekt nicht verfügbar.";
    } else if (error.code === "realtime_voice_unavailable") {
      message = "Die konfigurierte Realtime-Stimme ist für dieses OpenAI-Projekt nicht verfügbar.";
    } else if (error.code === "realtime_provider_rate_limited") {
      message = "OpenAI Realtime ist vorübergehend ausgelastet. Bitte versuche es später erneut.";
    } else {
      message = error.message;
    }
  } else if (error instanceof RealtimeClientError) {
    code = error.code;
    message = userMessageForClientError(error.code);
    httpStatus = typeof error.details?.httpStatus === "number" ? error.details.httpStatus : null;
    providerRequestId = typeof error.details?.providerRequestId === "string"
      ? error.details.providerRequestId
      : null;
    retryable = error.details?.retryable === true;
    technicalMessage = safeTechnicalMessage(error.details?.technicalMessage ?? error.message);
  } else {
    const rawMessage = error instanceof Error ? error.message : String(error ?? "");
    technicalMessage = safeTechnicalMessage(rawMessage);
    if (/expired|client.?secret|ephemeral/i.test(rawMessage)) {
      code = "realtime_client_secret_expired";
      message = "Das kurzlebige Verbindungs-Token ist abgelaufen. Bitte starte das Testgespräch erneut.";
    } else if (/webrtc|peer.?connection|ice|data.?channel|connection/i.test(rawMessage)) {
      code = "webrtc_connection_failed";
      message = "Die WebRTC-Sprachverbindung konnte nicht aufgebaut werden. Bitte prüfe Netzwerk und Browserfreigaben.";
      retryable = true;
    }
  }

  return {
    code,
    message,
    diagnostic: {
      status: "failed",
      phase,
      error_code: code,
      http_status: httpStatus,
      provider_request_id: providerRequestId,
      retryable,
      technical_message: safeTechnicalMessage(technicalMessage),
    },
  };
}

function storedAttemptId(): string | null {
  try {
    const value = window.sessionStorage.getItem(ACTIVE_ATTEMPT_STORAGE_KEY);
    return value && UUID_PATTERN.test(value) ? value : null;
  } catch {
    return null;
  }
}

function storeAttemptId(value: string | null): void {
  try {
    if (value) window.sessionStorage.setItem(ACTIVE_ATTEMPT_STORAGE_KEY, value);
    else window.sessionStorage.removeItem(ACTIVE_ATTEMPT_STORAGE_KEY);
  } catch {
    // Storage availability must not control the Realtime lifecycle.
  }
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
  playbackStatus: null,
};

export function useRealtimeVoice(configured: boolean, audioElement: HTMLAudioElement | null) {
  const [view, setView] = useState<RealtimeViewState>(() => ({
    ...initialState,
    state: configured ? "idle" : "not_configured",
  }));
  const clientRef = useRef<BrowserRealtimeClient | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const intervalRef = useRef<number | null>(null);
  const metricsRef = useRef(new RealtimeMetricsTracker());
  const startingRef = useRef(false);
  const mountedRef = useRef(true);
  const warnedRef = useRef(false);
  const remainingRef = useRef<number | null>(null);
  const clearedTranscriptIdsRef = useRef(new Set<string>());
  const generationRef = useRef(0);
  const callAttemptIdRef = useRef<string | null>(null);
  const phaseRef = useRef<RealtimePhase>("local_validation");
  const cleanupPromiseRef = useRef<Promise<void> | null>(null);
  const reloadRecoveryStartedRef = useRef(false);

  const addEvent = useCallback((type: string, detail?: string) => {
    if (import.meta.env.DEV && LIFECYCLE_EVENTS.has(type)) {
      console.info("realtime_lifecycle_event", {
        event_name: type,
        call_attempt_id: callAttemptIdRef.current,
        detail: detail ? safeTechnicalMessage(detail) : null,
      });
    }
    if (!mountedRef.current) return;
    setView((current) => ({
      ...current,
      events: [
        ...current.events,
        {
          id: `${Date.now()}-${Math.random()}`,
          type,
          detail,
          timestamp: Date.now(),
        },
      ].slice(-MAX_EVENTS),
    }));
  }, []);

  const clearTimer = useCallback(() => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
    intervalRef.current = null;
  }, []);

  const sendFinish = useCallback(async (
    callAttemptId: string,
    finish: RealtimeAttemptFinish,
    keepalive: boolean,
  ): Promise<boolean> => {
    const attempts = keepalive ? 1 : 2;
    for (let index = 0; index < attempts; index += 1) {
      try {
        await api.realtimeAttemptFinish(callAttemptId, finish, keepalive);
        return true;
      } catch (error) {
        const retryable = error instanceof ApiError
          && (error.status === undefined || error.status >= 500);
        if (!retryable || index + 1 >= attempts) return false;
      }
    }
    return false;
  }, []);

  const closeResources = useCallback((
    finish: RealtimeAttemptFinish = {
      status: "cancelled",
      phase: "cleanup",
      error_code: "realtime_attempt_cancelled",
      retryable: false,
    },
    keepalive = false,
  ): Promise<void> => {
    if (cleanupPromiseRef.current) return cleanupPromiseRef.current;
    const callAttemptId = callAttemptIdRef.current;
    const hasResources = Boolean(
      callAttemptId
      || abortRef.current
      || clientRef.current
      || intervalRef.current !== null,
    );
    if (!hasResources) return Promise.resolve();

    addEvent("session_cleanup_started", JSON.stringify({
      callAttemptId,
      terminalStatus: finish.status,
      phase: finish.phase,
    }));
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    clearTimer();
    const client = clientRef.current;
    clientRef.current = null;
    let localCleanupFailed = false;
    try {
      client?.close();
    } catch (error) {
      localCleanupFailed = true;
      if (import.meta.env.DEV) console.error("realtime_cleanup_failed", {
        call_attempt_id: callAttemptId,
        technical_message: safeTechnicalMessage(error),
      });
    }
    startingRef.current = false;
    remainingRef.current = null;
    phaseRef.current = "cleanup";

    const completion = (async () => {
      const reported = callAttemptId
        ? await sendFinish(callAttemptId, finish, keepalive)
        : true;
      if (reported && storedAttemptId() === callAttemptId) storeAttemptId(null);
      if (callAttemptIdRef.current === callAttemptId) callAttemptIdRef.current = null;
      if (localCleanupFailed || !reported) {
        addEvent("session_cleanup_failed", JSON.stringify({
          callAttemptId,
          localCleanupFailed,
          lifecycleReported: reported,
        }));
      } else {
        addEvent("session_cleanup_completed", JSON.stringify({ callAttemptId }));
      }
      addEvent("session_state_reset", JSON.stringify({
        callAttemptId,
        terminalStatus: finish.status,
      }));
    })().finally(() => {
      cleanupPromiseRef.current = null;
    });
    cleanupPromiseRef.current = completion;
    return completion;
  }, [addEvent, clearTimer, sendFinish]);

  const fail = useCallback((error: unknown) => {
    const callAttemptId = callAttemptIdRef.current;
    const readable = readableRealtimeError(error, callAttemptId, phaseRef.current);
    if (import.meta.env.DEV) console.error("realtime_attempt_failed", {
      call_attempt_id: callAttemptId,
      phase: readable.diagnostic.phase,
      error_code: readable.code,
      http_status: readable.diagnostic.http_status ?? null,
      provider_request_id: readable.diagnostic.provider_request_id ?? null,
      retryable: readable.diagnostic.retryable ?? false,
      technical_message: readable.diagnostic.technical_message ?? null,
    });
    void closeResources(readable.diagnostic);
    if (!mountedRef.current) return;
    setView((current) => ({
      ...current,
      state: "error",
      muted: false,
      error: readable.message,
      errorCode: readable.code,
      remainingSeconds: null,
    }));
  }, [closeResources]);

  const start = useCallback(async () => {
    if (cleanupPromiseRef.current) await cleanupPromiseRef.current;
    addEvent("local_session_state_checked", JSON.stringify({
      starting: startingRef.current,
      clientPresent: Boolean(clientRef.current),
      configured,
    }));
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

    const previousAttempt = storedAttemptId();
    if (previousAttempt && previousAttempt !== callAttemptIdRef.current) {
      await sendFinish(previousAttempt, {
        status: "abandoned",
        phase: "browser_reload",
        error_code: "browser_recovered_stale_attempt",
        retryable: false,
      }, false);
    }

    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const callAttemptId = crypto.randomUUID();
    callAttemptIdRef.current = callAttemptId;
    storeAttemptId(callAttemptId);
    phaseRef.current = "microphone";
    startingRef.current = true;
    warnedRef.current = false;
    clearedTranscriptIdsRef.current.clear();
    metricsRef.current = new RealtimeMetricsTracker();
    metricsRef.current.start();
    setView({ ...initialState, state: "requesting_microphone" });
    addEvent("call_start_requested", JSON.stringify({
      callAttemptId,
      generation,
    }));

    let stream: MediaStream | null = null;
    const abortController = new AbortController();
    abortRef.current = abortController;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      if (abortController.signal.aborted || generationRef.current !== generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      addEvent("microphone_permission_granted", JSON.stringify({ callAttemptId }));
      setView((current) => ({
        ...current,
        state: "connecting",
        notice: "Sprachverbindung wird aufgebaut …",
      }));
      phaseRef.current = "session_bootstrap";
      addEvent("backend_session_request_started", JSON.stringify({ callAttemptId }));
      addEvent("session_bootstrap_started", JSON.stringify({ callAttemptId }));
      const { manifest, secret } = await api.realtimeSessionBootstrap(
        callAttemptId,
        abortController.signal,
      );
      if (abortController.signal.aborted || generationRef.current !== generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      if (secret.call_attempt_id !== callAttemptId) {
        throw realtimeErrors.bootstrapMismatch();
      }
      addEvent("session_bootstrap_succeeded", JSON.stringify({
        callAttemptId,
        sessionId: secret.call_session_id,
        providerSessionId: secret.session_id,
      }));
      void api.bootstrapBookingConversation(secret.call_session_id).then(
        (result) => addEvent("booking.bootstrap_completed", JSON.stringify({
          callAttemptId,
          ...result,
        })),
        (error) => addEvent("booking.bootstrap_failed", JSON.stringify({
          callAttemptId,
          error: error instanceof ApiError ? error.code : "booking_bootstrap_failed",
        })),
      );
      if (
        secret.tenant_id !== manifest.tenant_id
        || secret.model !== manifest.model
        || secret.voice !== manifest.voice
        || secret.speed !== manifest.speed
        || secret.configuration_version !== manifest.configuration_version
      ) {
        throw realtimeErrors.bootstrapMismatch();
      }

      const client = new BrowserRealtimeClient({
        onState: (state: ConversationState) => setView((current) => {
          if (current.muted && state === "user_speaking") return current;
          return {
            ...current,
            state: current.muted && state === "connected" ? "muted" : state,
            muted: state === "muted" ? true : current.muted,
          };
        }),
        onHistory: (history) => setView((current) => ({
          ...current,
          transcript: mapRealtimeHistory(history, current.transcript)
            .filter((entry) => !clearedTranscriptIdsRef.current.has(entry.id)),
        })),
        onEvent: (type, detail) => {
          if (type === "signaling_started") phaseRef.current = "signaling";
          if (type === "session_configuration_started") phaseRef.current = "session_configuration";
          addEvent(type, detail);
        },
        onError: fail,
        onConnected: async () => {
          if (
            generationRef.current !== generation
            || callAttemptIdRef.current !== callAttemptId
          ) return;
          phaseRef.current = "lifecycle_sync";
          await api.realtimeAttemptConnected(callAttemptId);
          if (
            generationRef.current !== generation
            || callAttemptIdRef.current !== callAttemptId
          ) return;
          phaseRef.current = "conversation";
          startingRef.current = false;
          metricsRef.current.connected();
          setView((current) => ({
            ...current,
            metrics: metricsRef.current.snapshot(),
            notice: "Die Verbindung steht. Du kannst jetzt sprechen.",
          }));
        },
        onUserSpeechStopped: () => metricsRef.current.userSpeechStopped(),
        onAssistantAudioPlaying: () => {
          metricsRef.current.assistantAudioPlaying();
          setView((current) => ({
            ...current,
            metrics: metricsRef.current.snapshot(),
          }));
        },
        onResponseCompleted: (completed) => {
          metricsRef.current.responseCompleted(completed);
          setView((current) => ({
            ...current,
            metrics: metricsRef.current.snapshot(),
          }));
        },
        onPlaybackStatus: (playbackStatus) => setView((current) => {
          if (playbackStatus !== "completed" && playbackStatus !== "interrupted") {
            return { ...current, playbackStatus };
          }
          const transcript = [...current.transcript];
          for (let index = transcript.length - 1; index >= 0; index -= 1) {
            if (transcript[index].speaker === "assistant") {
              transcript[index] = {
                ...transcript[index],
                status: playbackStatus,
              };
              break;
            }
          }
          return { ...current, playbackStatus, transcript };
        }),
        onCallId: (callId) => setView((current) => ({ ...current, callId })),
      });
      clientRef.current = client;
      await client.connect(
        manifest,
        secret,
        stream,
        audioElement,
        callAttemptId,
      );
      if (
        generationRef.current !== generation
        || clientRef.current !== client
      ) {
        client.close();
        return;
      }

      const maximumSeconds = manifest.maximum_session_minutes * 60;
      remainingRef.current = maximumSeconds;
      setView((current) => ({
        ...current,
        remainingSeconds: maximumSeconds,
        vadSummary: manifest.vad.type === "semantic_vad"
          ? `semantic_vad · Reaktionsbereitschaft ${manifest.vad.eagerness} · Unterbrechung ${manifest.vad.interrupt_response ? "an" : "aus"}`
          : `server_vad · Schwelle ${manifest.vad.threshold} · Präfix ${manifest.vad.prefix_padding_ms} ms · Stille ${manifest.vad.silence_duration_ms} ms · Unterbrechung ${manifest.vad.interrupt_response ? "an" : "aus"}`,
      }));
      intervalRef.current = window.setInterval(() => {
        const tick = tickSessionTimer(
          remainingRef.current ?? maximumSeconds,
          warnedRef.current,
        );
        remainingRef.current = tick.remainingSeconds;
        if (tick.showOneMinuteWarning) warnedRef.current = true;
        setView((current) => ({
          ...current,
          remainingSeconds: tick.remainingSeconds,
          notice: tick.showOneMinuteWarning
            ? "Das Testgespräch endet automatisch in einer Minute."
            : current.notice,
          metrics: metricsRef.current.snapshot(),
        }));
        if (tick.expired) {
          void closeResources({
            status: "ended",
            phase: "conversation",
            error_code: "maximum_session_duration_reached",
            retryable: false,
          });
          if (mountedRef.current) {
            setView((current) => ({
              ...current,
              state: "ended",
              muted: false,
              remainingSeconds: null,
              notice: "Die maximale Testdauer wurde erreicht.",
            }));
          }
        }
      }, 1000);
    } catch (error) {
      if (!clientRef.current) stream?.getTracks().forEach((track) => track.stop());
      if (
        generationRef.current === generation
        && !(error instanceof DOMException && error.name === "AbortError")
      ) fail(error);
    }
  }, [
    addEvent,
    audioElement,
    closeResources,
    configured,
    fail,
    sendFinish,
  ]);

  const end = useCallback(() => {
    addEvent("call_end_requested", JSON.stringify({
      callAttemptId: callAttemptIdRef.current,
      phase: phaseRef.current,
    }));
    const wasStarting = startingRef.current;
    void closeResources({
      status: wasStarting ? "cancelled" : "ended",
      phase: wasStarting ? phaseRef.current : "conversation",
      error_code: wasStarting ? "realtime_start_cancelled" : null,
      retryable: false,
    });
    setView((current) => ({
      ...current,
      state: "ended",
      muted: false,
      notice: "Testgespräch beendet.",
      remainingSeconds: null,
    }));
  }, [addEvent, closeResources]);

  const toggleMute = useCallback(() => {
    if (!clientRef.current) return;
    const muted = !view.muted;
    clientRef.current.mute(muted);
    setView((current) => ({
      ...current,
      muted,
      state: muted ? "muted" : "connected",
    }));
  }, [view.muted]);

  const interrupt = useCallback(() => clientRef.current?.interrupt(), []);
  const dismissNotice = useCallback(
    () => setView((current) => ({ ...current, notice: null })),
    [],
  );
  const clearTranscript = useCallback(() => setView((current) => {
    current.transcript.forEach((entry) => {
      clearedTranscriptIdsRef.current.add(entry.id);
    });
    return { ...current, transcript: [] };
  }), []);

  useEffect(() => {
    setView((current) => {
      if (clientRef.current || startingRef.current) return current;
      return {
        ...current,
        state: configured
          ? (current.state === "not_configured" ? "idle" : current.state)
          : "not_configured",
      };
    });
  }, [configured]);

  useEffect(() => {
    if (reloadRecoveryStartedRef.current) return;
    const previousAttempt = storedAttemptId();
    if (!previousAttempt || previousAttempt === callAttemptIdRef.current) return;
    reloadRecoveryStartedRef.current = true;
    void sendFinish(previousAttempt, {
      status: "abandoned",
      phase: "browser_reload",
      error_code: "browser_reloaded_before_cleanup",
      retryable: false,
    }, false).then((reported) => {
      if (reported && storedAttemptId() === previousAttempt) storeAttemptId(null);
    });
  }, [sendFinish]);

  useEffect(() => {
    const handlePageHide = () => {
      if (!callAttemptIdRef.current) return;
      void closeResources({
        status: "abandoned",
        phase: "browser_reload",
        error_code: "browser_page_hidden",
        retryable: false,
      }, true);
    };
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [closeResources]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      void closeResources({
        status: "abandoned",
        phase: "cleanup",
        error_code: "frontend_unmounted",
        retryable: false,
      }, true);
    };
  }, [closeResources]);

  return {
    view,
    start,
    end,
    toggleMute,
    interrupt,
    dismissNotice,
    clearTranscript,
  };
}
