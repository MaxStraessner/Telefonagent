import { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import type { TransportEvent } from "@openai/agents/realtime";
import { api } from "../../api/client";
import type { RealtimeClientSecret, RuntimeManifest } from "../../types/api";
import type { ConversationState } from "../conversation/state";
import { manifestTurnDetection, normalizeAppliedConfiguration } from "./appliedConfiguration";
import { createCalendarTools } from "./calendarTools";
import { sanitizedRealtimeEventDetail } from "./events";
import { diagnoseResponseCompletion } from "./completionDiagnosis";
import { realtimeErrors } from "./errors";
import { derivePlaybackStatus, incompleteResponseWasInterrupted, type PlaybackStatus } from "./playback";
import { RealtimeToolExecutor, type ConversationRuntimeState } from "./toolExecution";

export const CONNECTION_TIMEOUT_MS = 15_000;
export const CONFIGURATION_ACK_TIMEOUT_MS = 8_000;

export interface RealtimeClientCallbacks {
  onState: (state: ConversationState) => void;
  onHistory: (history: readonly unknown[]) => void;
  onEvent: (type: string, detail?: string) => void;
  onError: (error: unknown) => void;
  onConnected: () => void;
  onUserSpeechStopped: () => void;
  onAssistantAudioPlaying: () => void;
  onResponseCompleted: (completed: boolean) => void;
  onPlaybackStatus: (status: PlaybackStatus, responseId: string | null) => void;
  onCallId: (callId: string | null) => void;
}

interface ActiveResponse {
  responseId: string | null;
  responseStatus: string | null;
  generationDone: boolean;
  responseCompleted: boolean;
  bufferStarted: boolean;
  playbackStopped: boolean;
  bufferCleared: boolean;
  itemTruncated: boolean;
  explicitlyCancelled: boolean;
  failed: boolean;
  functionCallRequested: boolean;
  functionCallArgumentsComplete: boolean;
  toolCallId: string | null;
  toolName: string | null;
}

function emptyActiveResponse(responseId: string | null = null): ActiveResponse {
  return {
    responseId,
    responseStatus: null,
    generationDone: false,
    responseCompleted: false,
    bufferStarted: false,
    playbackStopped: false,
    bufferCleared: false,
    itemTruncated: false,
    explicitlyCancelled: false,
    failed: false,
    functionCallRequested: false,
    functionCallArgumentsComplete: false,
    toolCallId: null,
    toolName: null,
  };
}

function responseFromEvent(event: TransportEvent): Record<string, unknown> | undefined {
  return (event as { response?: Record<string, unknown> }).response;
}

function responseIdFromEvent(event: TransportEvent): string | null {
  const value = event as { response_id?: string; response?: { id?: string } };
  return value.response?.id ?? value.response_id ?? null;
}

export class BrowserRealtimeClient {
  private session: RealtimeSession | null = null;
  private transport: OpenAIRealtimeWebRTC | null = null;
  private stream: MediaStream | null = null;
  private audioElement: HTMLAudioElement | null = null;
  private callbacks: RealtimeClientCallbacks;
  private rawEvents = false;
  private closed = false;
  private connected = false;
  private microphoneTracks: MediaStreamTrack[] = [];
  private listenerDisposers: Array<() => void> = [];
  private assistantSpeaking = false;
  private assistantGenerating = false;
  private manuallyMuted = false;
  private activeResponse = emptyActiveResponse();
  private playbackStatus: PlaybackStatus | null = null;
  private toolExecutor: RealtimeToolExecutor | null = null;
  private runtimeState: ConversationRuntimeState = "idle";
  private incompleteRecoveryAttempts = 0;
  private incompleteRecoveryPending = false;
  private configurationUpdated: ((event: TransportEvent) => void) | null = null;
  private interruptionsEnabled = false;

  constructor(callbacks: RealtimeClientCallbacks) {
    this.callbacks = callbacks;
  }

  async connect(
    manifest: RuntimeManifest,
    secret: RealtimeClientSecret,
    stream: MediaStream,
    audioElement: HTMLAudioElement,
  ) {
    if (secret.expires_at * 1000 <= Date.now() + 5_000) throw realtimeErrors.clientSecretExpired();
    this.closed = false;
    this.connected = false;
    this.stream = stream;
    this.microphoneTracks = stream.getAudioTracks?.() ?? stream.getTracks();
    this.setMicrophoneEnabled(false);
    this.microphoneTracks.forEach((track) => track.addEventListener?.("ended", this.handleMicrophoneEnded));
    this.audioElement = audioElement;
    this.rawEvents = manifest.raw_event_logging;
    this.interruptionsEnabled = manifest.vad.interrupt_response;
    audioElement.autoplay = true;
    audioElement.preload = "auto";
    audioElement.addEventListener("playing", this.handleAudioPlaying);
    audioElement.addEventListener("loadedmetadata", this.handleAudioReady);
    audioElement.addEventListener("error", this.handleAudioError);

    const transport = new OpenAIRealtimeWebRTC({ mediaStream: stream, audioElement });
    this.toolExecutor = new RealtimeToolExecutor(
      secret.call_session_id,
      this.callbacks.onEvent,
      (state) => this.setRuntimeState(state),
    );
    const calendarTools = createCalendarTools(manifest.tools, this.toolExecutor);
    const agent = new RealtimeAgent({
      name: manifest.assistant_name,
      instructions: manifest.instructions,
      voice: manifest.voice,
      tools: calendarTools,
    });
    const turnDetection = manifestTurnDetection(manifest);
    const session = new RealtimeSession(agent, {
      transport,
      model: manifest.model,
      historyStoreAudio: false,
      tracingDisabled: true,
      config: {
        outputModalities: ["audio"],
        providerData: {
          max_output_tokens: manifest.max_output_tokens,
          parallel_tool_calls: false,
        },
        toolChoice: calendarTools.length ? "auto" : "none",
        audio: {
          input: {
            noiseReduction: { type: "near_field" },
            transcription: manifest.transcription_enabled
              ? { model: "gpt-4o-mini-transcribe", language: manifest.language }
              : null,
            turnDetection,
          },
          output: { voice: manifest.voice, speed: manifest.speed },
        },
      },
    });
    this.transport = transport;
    this.session = session;
    this.bindEvents(session, transport);
    const configurationUpdated = new Promise<TransportEvent>((resolve) => {
      this.configurationUpdated = resolve;
    });

    let timeoutId: number | undefined;
    try {
      await Promise.race([
        session.connect({ apiKey: secret.client_secret, model: manifest.model }),
        new Promise<never>((_, reject) => {
          timeoutId = window.setTimeout(() => reject(realtimeErrors.connectionTimeout()), CONNECTION_TIMEOUT_MS);
        }),
      ]);
    } finally {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    }
    if (this.closed) return;
    let configurationTimeoutId: number | undefined;
    let configurationEvent: TransportEvent;
    try {
      configurationEvent = await Promise.race([
        configurationUpdated,
        new Promise<never>((_, reject) => {
          configurationTimeoutId = window.setTimeout(
            () => reject(realtimeErrors.configurationAckTimeout()),
            CONFIGURATION_ACK_TIMEOUT_MS,
          );
        }),
      ]);
    } finally {
      this.configurationUpdated = null;
      if (configurationTimeoutId !== undefined) window.clearTimeout(configurationTimeoutId);
    }
    if (this.closed) return;
    const applied = await normalizeAppliedConfiguration(configurationEvent);
    const diff = await api.reportAppliedRealtimeConfiguration(
      secret.call_session_id,
      manifest.digest,
      applied,
    );
    if (diff.status === "mismatch") throw realtimeErrors.configurationMismatch();
    this.connected = true;
    this.callbacks.onCallId(secret.call_session_id ?? transport.callId ?? secret.session_id);
    this.callbacks.onState("connected");
    this.callbacks.onConnected();
    this.emitInternalEvent("session_connected", "application_internal");
    this.callbacks.onEvent("active_agent_configuration", JSON.stringify({
      sessionId: secret.call_session_id,
      manifestDigest: manifest.digest,
      configurationVersion: manifest.configuration_version,
      model: manifest.model,
      voice: manifest.voice,
      speed: manifest.speed,
      language: manifest.language,
      maxOutputTokens: manifest.max_output_tokens,
      toolNames: manifest.tool_names,
      instructionsLength: manifest.instructions.length,
      standardGermanActive: manifest.instructions.includes("Standarddeutsch"),
      automaticToolContinuation: "agents_sdk",
    }));
    this.setMicrophoneEnabled(!this.manuallyMuted);
    transport.requestResponse({
      instructions: `Begrüße die anrufende Person jetzt. Verwende diese Begrüßung als Grundlage: ${manifest.welcome_message}`,
    });
  }

  private bindEvents(session: RealtimeSession, transport: OpenAIRealtimeWebRTC) {
    const historyUpdated = (history: Parameters<RealtimeClientCallbacks["onHistory"]>[0]) => this.callbacks.onHistory(history);
    const transportEvent = (event: TransportEvent) => this.handleTransportEvent(event);
    const agentStart = () => {
      this.assistantGenerating = true;
      this.setRuntimeState("generation_running");
      this.callbacks.onState("assistant_thinking");
      this.emitInternalEvent("sdk_agent_generation_started", "openai_sdk");
    };
    const toolStart = (_context: unknown, _agent: unknown, tool: unknown, details: unknown) => {
      this.setRuntimeState("tool_running");
      const name = (tool as { name?: unknown } | null)?.name;
      const callId = (details as { toolCall?: { callId?: unknown } } | null)?.toolCall?.callId;
      this.callbacks.onEvent("sdk_agent_tool_start", JSON.stringify({
        toolName: typeof name === "string" ? name : null,
        toolCallId: typeof callId === "string" ? callId : null,
      }));
    };
    const toolEnd = (_context: unknown, _agent: unknown, tool: unknown, _result: unknown, details: unknown) => {
      this.setRuntimeState("continuation_starting");
      const name = (tool as { name?: unknown } | null)?.name;
      const callId = (details as { toolCall?: { callId?: unknown } } | null)?.toolCall?.callId;
      this.callbacks.onEvent("sdk_agent_tool_end", JSON.stringify({
        toolName: typeof name === "string" ? name : null,
        toolCallId: typeof callId === "string" ? callId : null,
        continuationMode: "sdk_automatic",
      }));
    };
    const sessionError = (event: { error: unknown }) => this.callbacks.onError(event.error);
    const connectionChange = (status: "connecting" | "connected" | "disconnected") => {
      this.emitInternalEvent(`transport_${status}`, "openai_sdk");
      if (status === "disconnected" && this.connected && !this.closed) this.callbacks.onError(realtimeErrors.connectionLost());
    };

    session.on("history_updated", historyUpdated);
    session.on("transport_event", transportEvent);
    session.on("agent_start", agentStart);
    session.on("agent_tool_start", toolStart);
    session.on("agent_tool_end", toolEnd);
    session.on("error", sessionError);
    transport.on("connection_change", connectionChange);
    this.listenerDisposers.push(
      () => session.off("history_updated", historyUpdated),
      () => session.off("transport_event", transportEvent),
      () => session.off("agent_start", agentStart),
      () => session.off("agent_tool_start", toolStart),
      () => session.off("agent_tool_end", toolEnd),
      () => session.off("error", sessionError),
      () => transport.off("connection_change", connectionChange),
    );
  }

  private handleTransportEvent(event: TransportEvent) {
    const rawEventType = typeof event?.type === "string" ? event.type : "transport.event";
    if (rawEventType === "session.updated") this.configurationUpdated?.(event);
    const beforeSpeaking = this.assistantSpeaking;
    const beforeMicrophone = this.microphoneEnabled();
    const incomingResponseId = responseIdFromEvent(event);
    const response = responseFromEvent(event);
    if (rawEventType === "response.created") {
      this.activeResponse = emptyActiveResponse(incomingResponseId);
      this.assistantGenerating = true;
      this.setRuntimeState("generation_running");
      this.incompleteRecoveryPending = false;
      if (incomingResponseId) this.toolExecutor?.attachContinuationResponse(incomingResponseId);
    } else if (incomingResponseId && !this.activeResponse.responseId) {
      this.activeResponse.responseId = incomingResponseId;
    }

    let internalEventName = "realtime_event_observed";
    const item = (event as { item?: { type?: string; call_id?: string; name?: string; status?: string } }).item;
    if ((rawEventType === "response.output_item.added" || rawEventType === "response.output_item.done") && item?.type === "function_call") {
      this.activeResponse.functionCallRequested = true;
      this.activeResponse.toolCallId = item.call_id ?? this.activeResponse.toolCallId;
      this.activeResponse.toolName = item.name ?? this.activeResponse.toolName;
      if (item.status === "completed") this.activeResponse.functionCallArgumentsComplete = true;
      this.callbacks.onEvent(
        item.status === "completed" ? "tool_call_generation_completed" : "tool_call_requested",
        JSON.stringify({
          responseId: incomingResponseId,
          toolCallId: this.activeResponse.toolCallId,
          toolName: this.activeResponse.toolName,
          complete: item.status === "completed",
        }),
      );
    }
    if (rawEventType === "response.function_call_arguments.done") {
      const callEvent = event as { call_id?: string };
      this.activeResponse.functionCallRequested = true;
      this.activeResponse.functionCallArgumentsComplete = true;
      this.activeResponse.toolCallId = callEvent.call_id ?? this.activeResponse.toolCallId;
    }
    switch (rawEventType) {
      case "output_audio_buffer.started":
        this.activeResponse.bufferStarted = true;
        this.activeResponse.playbackStopped = false;
        this.setAssistantSpeaking(true);
        this.setRuntimeState("playback_running");
        internalEventName = "assistant_playback_started";
        break;
      case "response.output_audio.done":
        this.activeResponse.generationDone = true;
        internalEventName = "assistant_audio_generation_completed";
        break;
      case "response.done":
        this.assistantGenerating = false;
        this.activeResponse.responseCompleted = true;
        this.activeResponse.responseStatus = typeof response?.status === "string" ? response.status : null;
        this.activeResponse.explicitlyCancelled = incompleteResponseWasInterrupted(response);
        this.callbacks.onResponseCompleted(response?.status === undefined || response.status === "completed");
        this.toolExecutor?.completeResponse(incomingResponseId);
        {
          const diagnosis = diagnoseResponseCompletion(
            response,
            this.activeResponse.functionCallRequested,
            this.activeResponse.functionCallArgumentsComplete,
          );
          if (diagnosis.status === "completed") this.incompleteRecoveryAttempts = 0;
          internalEventName = diagnosis.interruption
            ? "assistant_response_interrupted"
            : diagnosis.status === "incomplete"
              ? "assistant_response_incomplete"
              : "assistant_response_generation_completed";
          if (
            diagnosis.recoverable
            && this.incompleteRecoveryAttempts < 1
            && !this.toolExecutor?.hasAwaitingContinuation()
          ) {
            this.incompleteRecoveryAttempts += 1;
            this.incompleteRecoveryPending = true;
            this.setRuntimeState("continuation_starting");
            this.callbacks.onEvent("incomplete_response_recovery_requested", JSON.stringify({
              responseId: incomingResponseId,
              reason: diagnosis.reason,
              functionCallRequested: this.activeResponse.functionCallRequested,
              functionCallArgumentsComplete: this.activeResponse.functionCallArgumentsComplete,
            }));
            try {
              this.transport?.requestResponse({
                instructions: "Setze dieselbe Gesprächsrunde jetzt kurz auf Deutsch fort. Falls ein Werkzeugaufruf unvollständig war, erzeuge ihn vollständig. Sonst beende die angefangene Antwort, ohne Gesagtes unnötig zu wiederholen.",
              });
            } catch (error) {
              this.incompleteRecoveryPending = false;
              this.callbacks.onError(error);
            }
          }
        }
        break;
      case "output_audio_buffer.stopped":
        this.activeResponse.playbackStopped = true;
        this.setAssistantSpeaking(false);
        this.setRuntimeState(
          this.assistantGenerating
            ? "generation_running"
            : this.incompleteRecoveryPending || this.toolExecutor?.hasAwaitingContinuation()
              ? "continuation_starting"
              : "idle",
        );
        internalEventName = "assistant_playback_completed";
        break;
      case "output_audio_buffer.cleared":
        this.activeResponse.bufferCleared = true;
        this.setAssistantSpeaking(false);
        this.setRuntimeState("idle");
        internalEventName = "assistant_playback_interrupted";
        break;
      case "conversation.item.truncated":
        this.activeResponse.itemTruncated = true;
        internalEventName = "assistant_item_truncated";
        break;
      case "input_audio_buffer.speech_started":
        if (!this.assistantSpeaking) this.callbacks.onState("user_speaking");
        internalEventName = "user_speech_started";
        break;
      case "input_audio_buffer.speech_stopped":
        this.callbacks.onUserSpeechStopped();
        this.callbacks.onState("assistant_thinking");
        internalEventName = "user_speech_stopped";
        break;
      case "error":
        this.activeResponse.failed = true;
        internalEventName = "realtime_error_received";
        break;
    }

    const status = derivePlaybackStatus({
      responseStatus: this.activeResponse.responseStatus,
      responseCompleted: this.activeResponse.responseCompleted,
      actualBufferStopped: this.activeResponse.playbackStopped,
      bufferStarted: this.activeResponse.bufferStarted,
      bufferCleared: this.activeResponse.bufferCleared,
      itemTruncated: this.activeResponse.itemTruncated,
      explicitlyCancelled: this.activeResponse.explicitlyCancelled,
      failed: this.activeResponse.failed,
    });
    if (status !== this.playbackStatus) {
      this.playbackStatus = status;
      this.callbacks.onPlaybackStatus(status, this.activeResponse.responseId);
    }
    this.callbacks.onEvent(rawEventType, JSON.stringify({
      rawEventType,
      internalEventName,
      eventId: (event as { event_id?: string }).event_id ?? null,
      responseId: this.activeResponse.responseId,
      responseStatus: this.activeResponse.responseStatus,
      responseStatusDetails: response ? sanitizedRealtimeEventDetail({
        status_details: response.status_details,
        incomplete_details: response.incomplete_details,
      }) : undefined,
      responseCompletionReason: rawEventType === "response.done"
        ? diagnoseResponseCompletion(
          response,
          this.activeResponse.functionCallRequested,
          this.activeResponse.functionCallArgumentsComplete,
        ).reason
        : undefined,
      functionCallRequested: this.activeResponse.functionCallRequested,
      functionCallArgumentsComplete: this.activeResponse.functionCallArgumentsComplete,
      toolCallId: this.activeResponse.toolCallId,
      toolName: this.activeResponse.toolName,
      eventSource: "openai_data_channel",
      assistantSpeakingBefore: beforeSpeaking,
      assistantSpeakingAfter: this.assistantSpeaking,
      assistantGenerating: this.assistantGenerating,
      assistantAudioGenerationComplete: this.activeResponse.generationDone,
      playbackStatus: status,
      microphoneEnabledBefore: beforeMicrophone,
      microphoneEnabledAfter: this.microphoneEnabled(),
      timestamp: new Date().toISOString(),
      ...(this.rawEvents ? { event: sanitizedRealtimeEventDetail(event) } : {}),
    }));
  }

  private emitInternalEvent(internalEventName: string, eventSource: "openai_sdk" | "application_internal" | "ui") {
    this.callbacks.onEvent(internalEventName, JSON.stringify({
      rawEventType: null,
      internalEventName,
      eventId: null,
      responseId: this.activeResponse.responseId,
      responseStatus: this.activeResponse.responseStatus,
      eventSource,
      assistantSpeakingBefore: this.assistantSpeaking,
      assistantSpeakingAfter: this.assistantSpeaking,
      microphoneEnabledBefore: this.microphoneEnabled(),
      microphoneEnabledAfter: this.microphoneEnabled(),
      timestamp: new Date().toISOString(),
    }));
  }

  private handleAudioReady = () => {
    if (this.closed || !this.audioElement) return;
    this.audioElement.play().catch(() => {
      if (!this.closed) this.callbacks.onError(realtimeErrors.audioPlaybackBlocked());
    });
  };

  private handleAudioError = () => {
    this.activeResponse.failed = true;
    if (this.playbackStatus !== "failed") {
      this.playbackStatus = "failed";
      this.callbacks.onPlaybackStatus("failed", this.activeResponse.responseId);
    }
    this.emitInternalEvent("html_audio_error", "ui");
    if (!this.closed) this.callbacks.onError(realtimeErrors.audioPlaybackBlocked());
  };

  private handleAudioPlaying = () => {
    this.emitInternalEvent("html_audio_playing", "ui");
    this.callbacks.onAssistantAudioPlaying();
  };

  private handleMicrophoneEnded = () => {
    if (!this.closed) this.callbacks.onError(realtimeErrors.microphoneEnded());
  };

  mute(muted: boolean) {
    this.manuallyMuted = muted;
    this.session?.mute(muted);
    this.setMicrophoneEnabled(!muted && this.runtimeState === "idle");
    this.callbacks.onState(muted ? "muted" : (this.assistantSpeaking ? "assistant_speaking" : "connected"));
    this.emitInternalEvent(muted ? "microphone_muted" : "microphone_unmuted", "ui");
  }

  interrupt() {
    if (!this.interruptionsEnabled) {
      this.emitInternalEvent("assistant_interrupt_disabled", "ui");
      return;
    }
    this.session?.interrupt();
    this.emitInternalEvent("assistant_interrupt_requested", "ui");
  }

  private microphoneEnabled() {
    return this.microphoneTracks.length > 0 && this.microphoneTracks.every((track) => track.enabled);
  }

  private setMicrophoneEnabled(enabled: boolean) {
    this.microphoneTracks.forEach((track) => { track.enabled = enabled; });
  }

  private setAssistantSpeaking(speaking: boolean) {
    if (this.closed || this.assistantSpeaking === speaking) return;
    this.assistantSpeaking = speaking;
    this.setMicrophoneEnabled(!speaking && !this.manuallyMuted && this.runtimeState === "idle");
    this.callbacks.onState(speaking ? "assistant_speaking" : (this.manuallyMuted ? "muted" : "connected"));
  }

  private setRuntimeState(state: ConversationRuntimeState) {
    if (this.closed) return;
    const before = this.runtimeState;
    this.runtimeState = state;
    this.setMicrophoneEnabled(state === "idle" && !this.manuallyMuted && !this.assistantSpeaking);
    if (before !== state) this.callbacks.onEvent("conversation_runtime_state_changed", JSON.stringify({ before, after: state }));
  }

  close() {
    if (!this.closed) this.emitInternalEvent("session_disconnected", "application_internal");
    this.closed = true;
    this.configurationUpdated?.({ type: "session.updated" } as TransportEvent);
    this.configurationUpdated = null;
    this.connected = false;
    this.assistantSpeaking = false;
    this.assistantGenerating = false;
    this.runtimeState = "idle";
    this.setMicrophoneEnabled(false);
    this.listenerDisposers.splice(0).forEach((dispose) => {
      try { dispose(); } catch { /* one faulty SDK listener must not block the remaining cleanup */ }
    });
    try { this.session?.interrupt(); } catch { /* session may not be fully connected */ }
    try { this.session?.close(); } catch { /* continue deterministic cleanup */ }
    try {
      if (!this.session || this.transport?.status !== "disconnected") this.transport?.close();
    } catch { /* continue deterministic cleanup */ }
    this.microphoneTracks.forEach((track) => track.removeEventListener?.("ended", this.handleMicrophoneEnded));
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.audioElement) {
      this.audioElement.removeEventListener("playing", this.handleAudioPlaying);
      this.audioElement.removeEventListener("loadedmetadata", this.handleAudioReady);
      this.audioElement.removeEventListener("error", this.handleAudioError);
      try { this.audioElement.pause(); } catch { /* no-op during teardown */ }
      this.audioElement.srcObject = null;
      this.audioElement.removeAttribute("src");
    }
    this.session = null;
    this.transport = null;
    this.stream = null;
    this.microphoneTracks = [];
    this.audioElement = null;
  }
}
