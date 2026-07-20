import { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import type { RealtimeAgentConfig, RealtimeClientSecret } from "../../types/api";
import type { ConversationState } from "../conversation/state";

export interface RealtimeClientCallbacks {
  onState: (state: ConversationState) => void;
  onHistory: (history: readonly unknown[]) => void;
  onEvent: (type: string, detail?: string) => void;
  onError: (error: unknown) => void;
  onConnected: () => void;
  onUserSpeechStopped: () => void;
  onAssistantAudioPlaying: () => void;
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
  private microphoneTracks: MediaStreamTrack[] = [];

  constructor(callbacks: RealtimeClientCallbacks) {
    this.callbacks = callbacks;
  }

  async connect(config: RealtimeAgentConfig, secret: RealtimeClientSecret, stream: MediaStream, audioElement: HTMLAudioElement) {
    this.closed = false;
    this.stream = stream;
    this.microphoneTracks = stream.getAudioTracks?.() ?? stream.getTracks();
    this.microphoneTracks.forEach((track) => track.addEventListener?.("ended", this.handleMicrophoneEnded));
    this.audioElement = audioElement;
    this.rawEvents = config.raw_event_logging;
    audioElement.autoplay = true;
    audioElement.addEventListener("playing", this.handleAudioPlaying);

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
    this.bindEvents(session);

    await session.connect({ apiKey: secret.client_secret, model: config.model });
    if (this.closed) return;
    this.callbacks.onCallId(transport.callId ?? secret.session_id);
    this.callbacks.onState("connected");
    this.callbacks.onConnected();
    this.callbacks.onEvent("session.connected");
    transport.requestResponse({
      instructions: `Begrüße die anrufende Person jetzt. Verwende diese Begrüßung als Grundlage: ${config.welcome_message}`,
    });
  }

  private bindEvents(session: RealtimeSession) {
    session.on("history_updated", (history) => this.callbacks.onHistory(history));
    session.on("transport_event", (event) => {
      const type = typeof event?.type === "string" ? event.type : "transport.event";
      const detail = this.rawEvents ? this.safeEventDetail(event) : undefined;
      this.callbacks.onEvent(type, detail);
      if (type === "input_audio_buffer.speech_started") this.callbacks.onState("user_speaking");
      if (type === "input_audio_buffer.speech_stopped") {
        this.callbacks.onUserSpeechStopped();
        this.callbacks.onState("assistant_thinking");
      }
    });
    session.on("agent_start", () => this.callbacks.onState("assistant_thinking"));
    session.on("audio_start", () => this.callbacks.onState("assistant_speaking"));
    session.on("audio_stopped", () => this.callbacks.onState("connected"));
    session.on("audio_interrupted", () => {
      this.callbacks.onEvent("audio.interrupted");
      this.callbacks.onState("user_speaking");
    });
    session.on("error", (event) => this.callbacks.onError(event.error));
  }

  private safeEventDetail(event: unknown): string | undefined {
    try {
      return JSON.stringify(event).slice(0, 1000);
    } catch {
      return undefined;
    }
  }

  private handleAudioPlaying = () => {
    this.callbacks.onAssistantAudioPlaying();
    this.callbacks.onState("assistant_speaking");
  };

  private handleMicrophoneEnded = () => {
    if (!this.closed) this.callbacks.onError(new Error("microphone track ended"));
  };

  mute(muted: boolean) {
    this.session?.mute(muted);
    this.callbacks.onState(muted ? "muted" : "connected");
    this.callbacks.onEvent(muted ? "microphone.muted" : "microphone.unmuted");
  }

  interrupt() {
    this.session?.interrupt();
    this.callbacks.onEvent("assistant.interrupted");
  }

  close() {
    if (!this.closed) this.callbacks.onEvent("session.disconnected");
    this.closed = true;
    this.session?.close();
    if (!this.session || this.transport?.status !== "disconnected") this.transport?.close();
    this.microphoneTracks.forEach((track) => track.removeEventListener?.("ended", this.handleMicrophoneEnded));
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.audioElement) {
      this.audioElement.removeEventListener("playing", this.handleAudioPlaying);
      this.audioElement.pause();
      this.audioElement.srcObject = null;
    }
    this.session = null;
    this.transport = null;
    this.stream = null;
    this.microphoneTracks = [];
    this.audioElement = null;
  }
}
