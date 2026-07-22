import { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import type { TransportEvent } from "@openai/agents/realtime";
import type { RealtimeAgentConfig, RealtimeClientSecret } from "../../types/api";
import type { ConversationState } from "../conversation/state";
import { createCalendarTools } from "./calendarTools";
import { sanitizedRealtimeEventDetail } from "./events";
import { realtimeErrors } from "./errors";
import { derivePlaybackStatus, incompleteResponseWasInterrupted, type PlaybackStatus } from "./playback";

export const CONNECTION_TIMEOUT_MS = 15_000;

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
  };
}

function responseFromEvent(event: TransportEvent): { id?: string; status?: string } | undefined {
  return (event as { response?: { id?: string; status?: string } }).response;
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

  constructor(callbacks: RealtimeClientCallbacks) {
    this.callbacks = callbacks;
  }

  async connect(config: RealtimeAgentConfig, secret: RealtimeClientSecret, stream: MediaStream, audioElement: HTMLAudioElement) {
    if (secret.expires_at * 1000 <= Date.now() + 5_000) throw realtimeErrors.clientSecretExpired();
    this.closed = false;
    this.connected = false;
    this.stream = stream;
    this.microphoneTracks = stream.getAudioTracks?.() ?? stream.getTracks();
    this.microphoneTracks.forEach((track) => track.addEventListener?.("ended", this.handleMicrophoneEnded));
    this.audioElement = audioElement;
    this.rawEvents = config.raw_event_logging;
    audioElement.autoplay = true;
    audioElement.preload = "auto";
    audioElement.addEventListener("playing", this.handleAudioPlaying);
    audioElement.addEventListener("loadedmetadata", this.handleAudioReady);
    audioElement.addEventListener("error", this.handleAudioError);

    const transport = new OpenAIRealtimeWebRTC({ mediaStream: stream, audioElement });
    const calendarTools = createCalendarTools(config.tool_names, this.callbacks.onEvent);
    const agent = new RealtimeAgent({ name: config.assistant_name, instructions: config.instructions, voice: config.voice, tools: calendarTools });
    const turnDetection = config.vad.type === "semantic_vad"
      ? { type: "semantic_vad" as const, eagerness: config.vad.eagerness ?? "medium", createResponse: config.vad.create_response, interruptResponse: false }
      : { type: "server_vad" as const, threshold: config.vad.threshold ?? 0.5, prefixPaddingMs: config.vad.prefix_padding_ms ?? 300, silenceDurationMs: config.vad.silence_duration_ms ?? 600, createResponse: config.vad.create_response, interruptResponse: false };
    const session = new RealtimeSession(agent, {
      transport,
      model: config.model,
      historyStoreAudio: false,
      tracingDisabled: true,
      config: {
        outputModalities: ["audio"],
        toolChoice: calendarTools.length ? "auto" : "none",
        audio: {
          input: {
            noiseReduction: { type: "near_field" },
            transcription: config.transcription_enabled ? { model: "gpt-4o-mini-transcribe", language: config.language } : null,
            turnDetection,
          },
          output: { voice: config.voice, speed: config.speed },
        },
      },
    });
    this.transport = transport;
    this.session = session;
    this.bindEvents(session, transport);

    let timeoutId: number | undefined;
    try {
      await Promise.race([
        session.connect({ apiKey: secret.client_secret, model: config.model }),
        new Promise<never>((_, reject) => {
          timeoutId = window.setTimeout(() => reject(realtimeErrors.connectionTimeout()), CONNECTION_TIMEOUT_MS);
        }),
      ]);
    } finally {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    }
    if (this.closed) return;
    this.connected = true;
    this.callbacks.onCallId(secret.call_session_id ?? transport.callId ?? secret.session_id);
    this.callbacks.onState("connected");
    this.callbacks.onConnected();
    this.emitInternalEvent("session_connected", "application_internal");
    transport.requestResponse({ instructions: `Begrüße die anrufende Person jetzt. Verwende diese Begrüßung als Grundlage: ${config.welcome_message}` });
  }

  private bindEvents(session: RealtimeSession, transport: OpenAIRealtimeWebRTC) {
    const historyUpdated = (history: Parameters<RealtimeClientCallbacks["onHistory"]>[0]) => this.callbacks.onHistory(history);
    const transportEvent = (event: TransportEvent) => this.handleTransportEvent(event);
    const agentStart = () => {
      this.assistantGenerating = true;
      this.callbacks.onState("assistant_thinking");
      this.emitInternalEvent("sdk_agent_generation_started", "openai_sdk");
    };
    const sessionError = (event: { error: unknown }) => this.callbacks.onError(event.error);
    const connectionChange = (status: "connecting" | "connected" | "disconnected") => {
      this.emitInternalEvent(`transport_${status}`, "openai_sdk");
      if (status === "disconnected" && this.connected && !this.closed) this.callbacks.onError(realtimeErrors.connectionLost());
    };

    session.on("history_updated", historyUpdated);
    session.on("transport_event", transportEvent);
    session.on("agent_start", agentStart);
    session.on("error", sessionError);
    transport.on("connection_change", connectionChange);
    this.listenerDisposers.push(
      () => session.off("history_updated", historyUpdated),
      () => session.off("transport_event", transportEvent),
      () => session.off("agent_start", agentStart),
      () => session.off("error", sessionError),
      () => transport.off("connection_change", connectionChange),
    );
  }

  private handleTransportEvent(event: TransportEvent) {
    const rawEventType = typeof event?.type === "string" ? event.type : "transport.event";
    const beforeSpeaking = this.assistantSpeaking;
    const beforeMicrophone = this.microphoneEnabled();
    const incomingResponseId = responseIdFromEvent(event);
    const response = responseFromEvent(event);
    if (rawEventType === "response.created") {
      this.activeResponse = emptyActiveResponse(incomingResponseId);
      this.assistantGenerating = true;
    } else if (incomingResponseId && !this.activeResponse.responseId) {
      this.activeResponse.responseId = incomingResponseId;
    }

    let internalEventName = "realtime_event_observed";
    switch (rawEventType) {
      case "output_audio_buffer.started":
        this.activeResponse.bufferStarted = true;
        this.activeResponse.playbackStopped = false;
        this.setAssistantSpeaking(true);
        internalEventName = "assistant_playback_started";
        break;
      case "response.output_audio.done":
        this.activeResponse.generationDone = true;
        internalEventName = "assistant_audio_generation_completed";
        break;
      case "response.done":
        this.assistantGenerating = false;
        this.activeResponse.responseCompleted = true;
        this.activeResponse.responseStatus = response?.status ?? null;
        this.activeResponse.explicitlyCancelled = incompleteResponseWasInterrupted(response);
        this.callbacks.onResponseCompleted(response?.status === undefined || response.status === "completed");
        internalEventName = this.activeResponse.explicitlyCancelled ? "assistant_response_interrupted" : "assistant_response_generation_completed";
        break;
      case "output_audio_buffer.stopped":
        this.activeResponse.playbackStopped = true;
        this.setAssistantSpeaking(false);
        internalEventName = "assistant_playback_completed";
        break;
      case "output_audio_buffer.cleared":
        this.activeResponse.bufferCleared = true;
        this.setAssistantSpeaking(false);
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
    this.setMicrophoneEnabled(!muted && !this.assistantSpeaking);
    this.callbacks.onState(muted ? "muted" : (this.assistantSpeaking ? "assistant_speaking" : "connected"));
    this.emitInternalEvent(muted ? "microphone_muted" : "microphone_unmuted", "ui");
  }

  interrupt() {
    this.emitInternalEvent("assistant_interrupt_disabled", "ui");
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
    this.setMicrophoneEnabled(!speaking && !this.manuallyMuted);
    this.callbacks.onState(speaking ? "assistant_speaking" : (this.manuallyMuted ? "muted" : "connected"));
  }

  close() {
    if (!this.closed) this.emitInternalEvent("session_disconnected", "application_internal");
    this.closed = true;
    this.connected = false;
    this.assistantSpeaking = false;
    this.assistantGenerating = false;
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
