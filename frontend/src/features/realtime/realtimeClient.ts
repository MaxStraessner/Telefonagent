import { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import type { TransportEvent } from "@openai/agents/realtime";
import type { RealtimeAgentConfig, RealtimeClientSecret } from "../../types/api";
import type { ConversationState } from "../conversation/state";
import { normalizedRealtimeEventType, sanitizedRealtimeEventDetail } from "./events";
import { realtimeErrors } from "./errors";

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
  onCallId: (callId: string | null) => void;
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
    const agent = new RealtimeAgent({
      name: config.assistant_name,
      instructions: config.instructions,
      voice: config.voice,
      tools: [],
    });
    const session = new RealtimeSession(agent, {
      transport,
      model: config.model,
      historyStoreAudio: false,
      tracingDisabled: true,
      config: {
        outputModalities: ["audio"],
        toolChoice: "none",
        tools: [],
        audio: {
          input: {
            noiseReduction: { type: "near_field" },
            transcription: config.transcription_enabled ? { model: "gpt-4o-mini-transcribe", language: config.language } : null,
            turnDetection: {
              type: config.vad.type,
              threshold: config.vad.threshold,
              prefixPaddingMs: config.vad.prefix_padding_ms,
              silenceDurationMs: config.vad.silence_duration_ms,
              createResponse: config.vad.create_response,
              interruptResponse: config.vad.interrupt_response,
            },
          },
          output: { voice: config.voice },
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
    this.callbacks.onCallId(transport.callId ?? secret.session_id);
    this.callbacks.onState("connected");
    this.callbacks.onConnected();
    this.callbacks.onEvent("session_connected");
    transport.requestResponse({
      instructions: `Begrüße die anrufende Person jetzt. Verwende diese Begrüßung als Grundlage: ${config.welcome_message}`,
    });
  }

  private bindEvents(session: RealtimeSession, transport: OpenAIRealtimeWebRTC) {
    const historyUpdated = (history: Parameters<RealtimeClientCallbacks["onHistory"]>[0]) => this.callbacks.onHistory(history);
    const transportEvent = (event: TransportEvent) => {
      const rawType = typeof event?.type === "string" ? event.type : "transport.event";
      const type = normalizedRealtimeEventType(rawType);
      const detail = this.rawEvents ? sanitizedRealtimeEventDetail(event) : undefined;
      this.callbacks.onEvent(type, detail);
      if (rawType === "input_audio_buffer.speech_started") this.callbacks.onState("user_speaking");
      if (rawType === "input_audio_buffer.speech_stopped") {
        this.callbacks.onUserSpeechStopped();
        this.callbacks.onState("assistant_thinking");
      }
      if (rawType === "response.done") {
        const responseStatus = (event as { response?: { status?: string } }).response?.status;
        this.callbacks.onResponseCompleted(responseStatus === undefined || responseStatus === "completed");
      }
    };
    const agentStart = () => this.callbacks.onState("assistant_thinking");
    const audioStart = () => this.callbacks.onState("assistant_speaking");
    const audioStopped = () => this.callbacks.onState("connected");
    const audioInterrupted = () => {
      this.callbacks.onEvent("audio_interrupted");
      this.callbacks.onState("user_speaking");
    };
    const sessionError = (event: { error: unknown }) => this.callbacks.onError(event.error);
    const connectionChange = (status: "connecting" | "connected" | "disconnected") => {
      this.callbacks.onEvent(`transport_${status}`);
      if (status === "disconnected" && this.connected && !this.closed) this.callbacks.onError(realtimeErrors.connectionLost());
    };

    session.on("history_updated", historyUpdated);
    session.on("transport_event", transportEvent);
    session.on("agent_start", agentStart);
    session.on("audio_start", audioStart);
    session.on("audio_stopped", audioStopped);
    session.on("audio_interrupted", audioInterrupted);
    session.on("error", sessionError);
    transport.on("connection_change", connectionChange);
    this.listenerDisposers.push(
      () => session.off("history_updated", historyUpdated),
      () => session.off("transport_event", transportEvent),
      () => session.off("agent_start", agentStart),
      () => session.off("audio_start", audioStart),
      () => session.off("audio_stopped", audioStopped),
      () => session.off("audio_interrupted", audioInterrupted),
      () => session.off("error", sessionError),
      () => transport.off("connection_change", connectionChange),
    );
  }

  private handleAudioReady = () => {
    if (this.closed || !this.audioElement) return;
    this.audioElement.play().catch(() => {
      if (!this.closed) this.callbacks.onError(realtimeErrors.audioPlaybackBlocked());
    });
  };

  private handleAudioError = () => {
    if (!this.closed) this.callbacks.onError(realtimeErrors.audioPlaybackBlocked());
  };

  private handleAudioPlaying = () => {
    this.callbacks.onEvent("first_audio_playing");
    this.callbacks.onAssistantAudioPlaying();
    this.callbacks.onState("assistant_speaking");
  };

  private handleMicrophoneEnded = () => {
    if (!this.closed) this.callbacks.onError(realtimeErrors.microphoneEnded());
  };

  mute(muted: boolean) {
    this.session?.mute(muted);
    this.callbacks.onState(muted ? "muted" : "connected");
    this.callbacks.onEvent(muted ? "microphone_muted" : "microphone_unmuted");
  }

  interrupt() {
    this.session?.interrupt();
    this.callbacks.onEvent("assistant_interrupted");
  }

  close() {
    if (!this.closed) this.callbacks.onEvent("session_disconnected");
    this.closed = true;
    this.connected = false;
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
