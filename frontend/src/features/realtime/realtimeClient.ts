import { OpenAIRealtimeWebRTC, RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import type { TransportEvent } from "@openai/agents/realtime";
import type { RealtimeClientSecret, RuntimeManifest } from "../../types/api";
import type { ConversationState } from "../conversation/state";
import { createCalendarTools } from "./calendarTools";
import { sanitizedRealtimeEventDetail } from "./events";
import { diagnoseResponseCompletion } from "./completionDiagnosis";
import { RealtimeClientError, realtimeErrors } from "./errors";
import { derivePlaybackStatus, incompleteResponseWasInterrupted, type PlaybackStatus } from "./playback";
import { RealtimeToolExecutor } from "./toolExecution";
import { manifestTurnDetection } from "./turnDetection";

export const CONNECTION_TIMEOUT_MS = 15_000;
export const CONFIGURATION_ACK_TIMEOUT_MS = 8_000;
const OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

function signalingUrl(model: string): string {
  const url = new URL(OPENAI_REALTIME_CALLS_URL);
  url.searchParams.set("model", model);
  return url.toString();
}

export interface RealtimeClientCallbacks {
  onState: (state: ConversationState) => void;
  onHistory: (history: readonly unknown[]) => void;
  onEvent: (type: string, detail?: string) => void;
  onError: (error: unknown) => void;
  onConnected: () => void | Promise<void>;
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
  const response = (event as { response?: unknown }).response;
  return response && typeof response === "object" ? response as Record<string, unknown> : undefined;
}

function responseIdFromEvent(event: TransportEvent): string | null {
  const value = event as {
    response_id?: unknown;
    responseId?: unknown;
    response?: { id?: unknown };
  };
  const candidates = [value.response?.id, value.response_id, value.responseId];
  return candidates.find((candidate): candidate is string => typeof candidate === "string" && candidate.length > 0) ?? null;
}

function providerErrorDetails(value: unknown): Readonly<Record<string, unknown>> {
  const error = value && typeof value === "object" && "error" in value
    ? (value as { error?: unknown }).error
    : value;
  if (!error || typeof error !== "object") return { providerError: "unknown" };
  if (
    (error as { type?: unknown }).type === "error"
    && "error" in error
  ) {
    return providerErrorDetails(error);
  }
  const providerError = error as { type?: unknown; code?: unknown; param?: unknown; message?: unknown };
  return {
    phase: "conversation",
    providerErrorType: typeof providerError.type === "string" ? providerError.type : null,
    providerErrorCode: typeof providerError.code === "string" ? providerError.code : null,
    providerErrorParam: typeof providerError.param === "string" ? providerError.param : null,
    providerRequestId: null,
    httpStatus: null,
    retryable: false,
    technicalMessage: sanitizeTechnicalMessage(providerError.message),
  };
}

function sanitizeTechnicalMessage(value: unknown): string {
  return String(value ?? "")
    .replace(/\r|\n/g, " ")
    .replace(/\b(?:Bearer\s+)?(?:sk|ek)[-_][a-z0-9_-]{8,}\b/gi, "[REDACTED_CREDENTIAL]")
    .trim()
    .slice(0, 500);
}

function signalingErrorDetails(value: unknown): Readonly<Record<string, unknown>> {
  if (value instanceof RealtimeClientError) {
    return {
      phase: "signaling",
      providerRequestId: null,
      httpStatus: null,
      retryable: value.code === "realtime_connection_timeout",
      technicalMessage: sanitizeTechnicalMessage(value.message),
      ...value.details,
    };
  }
  const message = value instanceof Error ? value.message : String(value ?? "");
  const statusMatch = message.match(/\bstatus\s+(\d{3})\b/i);
  const httpStatus = statusMatch ? Number(statusMatch[1]) : null;
  let providerErrorType: string | null = null;
  let providerErrorCode: string | null = null;
  let providerErrorParam: string | null = null;
  let providerMessage = message;
  const jsonStart = message.indexOf("{");
  if (jsonStart >= 0) {
    try {
      const parsed = JSON.parse(message.slice(jsonStart)) as {
        error?: { type?: unknown; code?: unknown; param?: unknown; message?: unknown };
      };
      providerErrorType = typeof parsed.error?.type === "string" ? parsed.error.type : null;
      providerErrorCode = typeof parsed.error?.code === "string" ? parsed.error.code : null;
      providerErrorParam = typeof parsed.error?.param === "string" ? parsed.error.param : null;
      if (typeof parsed.error?.message === "string") providerMessage = parsed.error.message;
    } catch {
      // The SDK error still contains the HTTP status and a redacted message.
    }
  }
  return {
    phase: "signaling",
    providerErrorType,
    providerErrorCode,
    providerErrorParam,
    providerRequestId: null,
    httpStatus,
    retryable: httpStatus === null || httpStatus === 408 || httpStatus === 429 || httpStatus >= 500,
    technicalMessage: sanitizeTechnicalMessage(providerMessage),
  };
}

interface PendingConfigurationAck {
  connectionGenerationId: string;
  resolve: (event: TransportEvent) => void;
  reject: (error: unknown) => void;
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
  private readonly reportedProviderErrors = new Set<string>();
  private connectionGenerationId: string | null = null;
  private pendingConfigurationAck: PendingConfigurationAck | null = null;
  private configurationApplied = false;
  private interruptionsEnabled = false;
  private callAttemptId: string | null = null;

  constructor(callbacks: RealtimeClientCallbacks) {
    this.callbacks = callbacks;
  }

  async connect(
    manifest: RuntimeManifest,
    secret: RealtimeClientSecret,
    stream: MediaStream,
    audioElement: HTMLAudioElement,
    callAttemptId: string = secret.call_attempt_id,
  ) {
    if (secret.expires_at * 1000 <= Date.now() + 5_000) throw realtimeErrors.clientSecretExpired();
    if (this.session || this.transport || this.stream) this.close();
    const runtimeManifest = structuredClone(manifest);
    const connectionGenerationId = crypto.randomUUID();
    const revision = `${runtimeManifest.configuration_version}:${runtimeManifest.digest}`;
    this.connectionGenerationId = connectionGenerationId;
    this.callAttemptId = callAttemptId;
    this.configurationApplied = false;
    this.closed = false;
    this.connected = false;
    this.assistantSpeaking = false;
    this.assistantGenerating = false;
    this.activeResponse = emptyActiveResponse();
    this.playbackStatus = null;
    this.reportedProviderErrors.clear();
    this.stream = stream;
    this.microphoneTracks = stream.getAudioTracks?.() ?? stream.getTracks();
    this.setMicrophoneEnabled(false);
    this.microphoneTracks.forEach((track) => track.addEventListener?.("ended", this.handleMicrophoneEnded));
    this.audioElement = audioElement;
    this.rawEvents = runtimeManifest.raw_event_logging;
    this.interruptionsEnabled = runtimeManifest.vad.interrupt_response;
    audioElement.autoplay = true;
    audioElement.preload = "auto";
    audioElement.addEventListener("playing", this.handleAudioPlaying);
    audioElement.addEventListener("loadedmetadata", this.handleAudioReady);
    audioElement.addEventListener("error", this.handleAudioError);

    const transport = new OpenAIRealtimeWebRTC({ mediaStream: stream, audioElement });
    this.toolExecutor = new RealtimeToolExecutor(secret.call_session_id, this.callbacks.onEvent);
    const calendarTools = createCalendarTools(runtimeManifest.tools, this.toolExecutor);
    const agent = new RealtimeAgent({
      name: runtimeManifest.assistant_name,
      instructions: runtimeManifest.instructions,
      voice: runtimeManifest.voice,
      tools: calendarTools,
    });
    const turnDetection = manifestTurnDetection(runtimeManifest);
    const session = new RealtimeSession(agent, {
      transport,
      model: runtimeManifest.model,
      historyStoreAudio: false,
      tracingDisabled: true,
      config: {
        outputModalities: ["audio"],
        providerData: {
          max_output_tokens: runtimeManifest.max_output_tokens,
          parallel_tool_calls: false,
        },
        toolChoice: calendarTools.length ? "auto" : "none",
        audio: {
          input: {
            noiseReduction: { type: "near_field" },
            transcription: manifest.transcription_enabled
              ? { model: "gpt-4o-mini-transcribe", language: runtimeManifest.language }
              : null,
            turnDetection,
          },
          output: { voice: runtimeManifest.voice, speed: runtimeManifest.speed },
        },
      },
    });
    this.transport = transport;
    this.session = session;
    this.bindEvents(session, transport, connectionGenerationId);
    const configurationUpdated = new Promise<TransportEvent>((resolve, reject) => {
      this.pendingConfigurationAck = {
        connectionGenerationId,
        resolve,
        reject,
      };
    });
    configurationUpdated.catch(() => undefined);

    let timeoutId: number | undefined;
    const providerEndpoint = signalingUrl(runtimeManifest.model);
    this.callbacks.onEvent("signaling_started", JSON.stringify({
      callAttemptId,
      connectionGenerationId,
      model: runtimeManifest.model,
      providerEndpoint,
    }));
    try {
      await Promise.race([
        session.connect({
          apiKey: secret.client_secret,
          model: runtimeManifest.model,
          url: providerEndpoint,
        }),
        new Promise<never>((_, reject) => {
          timeoutId = window.setTimeout(() => reject(realtimeErrors.connectionTimeout()), CONNECTION_TIMEOUT_MS);
        }),
      ]);
      this.callbacks.onEvent("signaling_succeeded", JSON.stringify({
        callAttemptId,
        connectionGenerationId,
        providerSessionId: secret.session_id,
      }));
    } catch (error) {
      const details = signalingErrorDetails(error);
      if (import.meta.env.DEV) console.error("realtime_signaling_failed", {
        call_attempt_id: callAttemptId,
        ...details,
      });
      this.callbacks.onEvent("signaling_failed", JSON.stringify({
        callAttemptId,
        connectionGenerationId,
        errorCode: details.providerErrorCode ?? (
          error instanceof RealtimeClientError ? error.code : "realtime_signaling_failed"
        ),
        httpStatus: details.httpStatus ?? null,
        retryable: details.retryable ?? false,
      }));
      if (error instanceof RealtimeClientError) throw error;
      if (/expired|client.?secret|ephemeral/i.test(String(
        error instanceof Error ? error.message : error,
      ))) {
        throw realtimeErrors.clientSecretExpired();
      }
      throw realtimeErrors.signalingFailed(details);
    } finally {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    }
    if (!this.isCurrentConnection(connectionGenerationId)) return;
    let configurationTimeoutId: number | undefined;
    this.callbacks.onEvent("session_configuration_started", JSON.stringify({
      callAttemptId,
      connectionGenerationId,
      manifestDigest: runtimeManifest.digest,
    }));
    try {
      await Promise.race([
        configurationUpdated,
        new Promise<never>((_, reject) => {
          configurationTimeoutId = window.setTimeout(
            () => reject(realtimeErrors.configurationAckTimeout()),
            CONFIGURATION_ACK_TIMEOUT_MS,
          );
        }),
      ]);
      this.callbacks.onEvent("session_configuration_succeeded", JSON.stringify({
        callAttemptId,
        connectionGenerationId,
        manifestDigest: runtimeManifest.digest,
      }));
    } catch (error) {
      this.callbacks.onEvent("session_configuration_failed", JSON.stringify({
        callAttemptId,
        connectionGenerationId,
        errorCode: error instanceof RealtimeClientError
          ? error.code
          : "realtime_session_configuration_failed",
      }));
      throw error;
    } finally {
      if (this.pendingConfigurationAck?.connectionGenerationId === connectionGenerationId) {
        this.pendingConfigurationAck = null;
      }
      if (configurationTimeoutId !== undefined) window.clearTimeout(configurationTimeoutId);
    }
    if (!this.isCurrentConnection(connectionGenerationId)) return;
    this.callbacks.onEvent("realtime_configuration_acknowledged", JSON.stringify({
      connectionGenerationId,
      callAttemptId,
      revision,
      manifestDigest: runtimeManifest.digest,
      result: "session.updated",
    }));
    this.configurationApplied = true;
    this.callbacks.onCallId(secret.call_session_id ?? transport.callId ?? secret.session_id);
    await this.callbacks.onConnected();
    if (!this.isCurrentConnection(connectionGenerationId)) return;
    this.connected = true;
    this.callbacks.onState("connected");
    this.emitInternalEvent("session_connected", "application_internal");
    this.callbacks.onEvent("call_connected", JSON.stringify({
      callAttemptId,
      connectionGenerationId,
      sessionId: secret.call_session_id,
      providerSessionId: secret.session_id,
    }));
    this.callbacks.onEvent("active_agent_configuration", JSON.stringify({
      callAttemptId,
      connectionGenerationId,
      revision,
      sessionId: secret.call_session_id,
      manifestDigest: runtimeManifest.digest,
      configurationVersion: runtimeManifest.configuration_version,
      model: runtimeManifest.model,
      voice: runtimeManifest.voice,
      speed: runtimeManifest.speed,
      language: runtimeManifest.language,
      maxOutputTokens: runtimeManifest.max_output_tokens,
      toolNames: runtimeManifest.tool_names,
      instructionsLength: runtimeManifest.instructions.length,
      standardGermanActive: runtimeManifest.instructions.includes("Standarddeutsch"),
      responseAuthority: "agents_sdk_response_create_sequencer",
      normalTurnResponses: "server_vad",
      automaticToolContinuation: "agents_sdk",
    }));
    this.assistantGenerating = true;
    this.updateMicrophone();
    transport.requestResponse({
      instructions: `Begrüße die anrufende Person jetzt. Verwende diese Begrüßung als Grundlage: ${runtimeManifest.welcome_message}`,
    });
  }

  private isCurrentConnection(connectionGenerationId: string) {
    return !this.closed && this.connectionGenerationId === connectionGenerationId;
  }

  private isReadyConnection(connectionGenerationId: string) {
    return this.isCurrentConnection(connectionGenerationId) && this.configurationApplied;
  }

  private bindEvents(session: RealtimeSession, transport: OpenAIRealtimeWebRTC, connectionGenerationId: string) {
    const historyUpdated = (history: Parameters<RealtimeClientCallbacks["onHistory"]>[0]) => {
      if (this.isReadyConnection(connectionGenerationId)) this.callbacks.onHistory(history);
    };
    const transportEvent = (event: TransportEvent) => this.handleTransportEvent(event, connectionGenerationId);
    const agentStart = () => {
      if (!this.isReadyConnection(connectionGenerationId)) return;
      this.assistantGenerating = true;
      this.updateMicrophone();
      this.callbacks.onState("assistant_thinking");
      this.emitInternalEvent("sdk_agent_generation_started", "openai_sdk");
    };
    const toolStart = (_context: unknown, _agent: unknown, tool: unknown, details: unknown) => {
      if (!this.isReadyConnection(connectionGenerationId)) return;
      const name = (tool as { name?: unknown } | null)?.name;
      const callId = (details as { toolCall?: { callId?: unknown } } | null)?.toolCall?.callId;
      this.updateMicrophone();
      this.callbacks.onEvent("sdk_agent_tool_start", JSON.stringify({
        toolName: typeof name === "string" ? name : null,
        toolCallId: typeof callId === "string" ? callId : null,
        toolStatus: "running",
      }));
    };
    const toolEnd = (_context: unknown, _agent: unknown, tool: unknown, _result: unknown, details: unknown) => {
      if (!this.isReadyConnection(connectionGenerationId)) return;
      const name = (tool as { name?: unknown } | null)?.name;
      const callId = (details as { toolCall?: { callId?: unknown } } | null)?.toolCall?.callId;
      if (typeof callId === "string") {
        this.toolExecutor?.markResultSubmitted(callId);
      }
      this.updateMicrophone();
      this.callbacks.onEvent("sdk_agent_tool_end", JSON.stringify({
        toolName: typeof name === "string" ? name : null,
        toolCallId: typeof callId === "string" ? callId : null,
        toolStatus: "result_submitted",
        continuationMode: "sdk_sequenced",
      }));
    };
    const sessionError = (event: { error: unknown }) => {
      if (this.isCurrentConnection(connectionGenerationId)) this.reportProviderError(providerErrorDetails(event.error));
    };
    const connectionChange = (status: "connecting" | "connected" | "disconnected") => {
      if (!this.isCurrentConnection(connectionGenerationId)) return;
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

  private handleTransportEvent(event: TransportEvent, connectionGenerationId: string) {
    if (!this.isCurrentConnection(connectionGenerationId)) return;
    const rawEventType = typeof event?.type === "string" ? event.type : "transport.event";
    if (rawEventType === "session.updated") {
      const session = (event as { session?: unknown }).session;
      const pending = this.pendingConfigurationAck;
      if (pending && pending.connectionGenerationId === connectionGenerationId && session && typeof session === "object") {
        this.pendingConfigurationAck = null;
        pending.resolve(event);
      }
      return;
    }
    if (!this.configurationApplied) return;
    const beforeSpeaking = this.assistantSpeaking;
    const beforeMicrophone = this.microphoneEnabled();
    const incomingResponseId = responseIdFromEvent(event);
    const response = responseFromEvent(event);
    if (rawEventType === "response.created") {
      if (
        this.activeResponse.responseId
        && !this.activeResponse.responseCompleted
        && this.activeResponse.responseId !== incomingResponseId
      ) {
        this.callbacks.onEvent("concurrent_response_observed", JSON.stringify({
          activeResponseId: this.activeResponse.responseId,
          responseId: incomingResponseId,
        }));
      }
      this.activeResponse = emptyActiveResponse(incomingResponseId);
      this.assistantGenerating = true;
      this.updateMicrophone();
      if (incomingResponseId && this.toolExecutor?.hasAwaitingContinuation()) {
        this.toolExecutor.attachContinuationResponse(incomingResponseId);
      }
    } else if (incomingResponseId && !this.activeResponse.responseId) {
      this.activeResponse.responseId = incomingResponseId;
    }

    let internalEventName = "realtime_event_observed";
    let terminalError: ReturnType<typeof realtimeErrors.providerRequestFailed> | null = null;
    const item = (event as {
      item?: { id?: string; type?: string; call_id?: string; name?: string; status?: string };
    }).item;
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
        internalEventName = "assistant_playback_started";
        break;
      case "response.output_audio.done":
        this.activeResponse.generationDone = true;
        internalEventName = "assistant_audio_generation_completed";
        break;
      case "response.done":
        this.assistantGenerating = false;
        this.updateMicrophone();
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
          internalEventName = diagnosis.interruption
            ? "assistant_response_interrupted"
            : diagnosis.status === "incomplete"
              ? "assistant_response_incomplete"
              : "assistant_response_generation_completed";
        }
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
        {
          const details = providerErrorDetails(event);
          terminalError = realtimeErrors.providerRequestFailed(details);
          this.callbacks.onEvent("realtime_provider_error", JSON.stringify({
            responseId: incomingResponseId,
            phase: details.phase,
            providerErrorType: details.providerErrorType,
            providerErrorCode: details.providerErrorCode,
            providerErrorParam: details.providerErrorParam,
            providerRequestId: details.providerRequestId,
            httpStatus: details.httpStatus,
            retryable: details.retryable,
            activeResponseId: this.activeResponse.responseId,
          }));
        }
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
      callAttemptId: this.callAttemptId,
      eventId: (event as { event_id?: string }).event_id ?? null,
      sessionId: this.toolExecutor?.sessionId ?? null,
      responseId: this.activeResponse.responseId,
      activeResponseId: this.activeResponse.responseId,
      itemId: item?.id ?? null,
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
      toolStatus: this.toolExecutor?.status(this.activeResponse.toolCallId) ?? null,
      eventSource: "openai_data_channel",
      dataChannelState: this.transport?.connectionState?.dataChannel?.readyState ?? "closed",
      peerConnectionState: this.transport?.connectionState?.peerConnection?.connectionState ?? "closed",
      audioState: this.audioElement
        ? (this.audioElement.paused ? "paused" : "playing")
        : "unavailable",
      errorCode: terminalError?.details?.providerErrorCode ?? null,
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
    if (terminalError && !this.closed) this.reportProviderError(terminalError.details);
  }

  private reportProviderError(details?: Readonly<Record<string, unknown>>) {
    const key = JSON.stringify({
      type: details?.providerErrorType ?? null,
      code: details?.providerErrorCode ?? null,
      param: details?.providerErrorParam ?? null,
    });
    if (this.reportedProviderErrors.has(key)) return;
    this.reportedProviderErrors.add(key);
    if (import.meta.env.DEV) console.error("realtime_provider_request_failed", {
      call_attempt_id: this.callAttemptId,
      phase: details?.phase ?? "conversation",
      error_code: details?.providerErrorCode ?? "realtime_provider_request_failed",
      http_status: details?.httpStatus ?? null,
      provider_request_id: details?.providerRequestId ?? null,
      retryable: details?.retryable ?? false,
      technical_message: details?.technicalMessage ?? null,
    });
    this.callbacks.onError(realtimeErrors.providerRequestFailed(details));
  }

  private emitInternalEvent(internalEventName: string, eventSource: "openai_sdk" | "application_internal" | "ui") {
    this.callbacks.onEvent(internalEventName, JSON.stringify({
      rawEventType: null,
      internalEventName,
      callAttemptId: this.callAttemptId,
      eventId: null,
      sessionId: this.toolExecutor?.sessionId ?? null,
      responseId: this.activeResponse.responseId,
      activeResponseId: this.activeResponse.responseId,
      itemId: null,
      responseStatus: this.activeResponse.responseStatus,
      toolCallId: this.activeResponse.toolCallId,
      toolName: this.activeResponse.toolName,
      toolStatus: this.toolExecutor?.status(this.activeResponse.toolCallId) ?? null,
      eventSource,
      dataChannelState: this.transport?.connectionState?.dataChannel?.readyState ?? "closed",
      peerConnectionState: this.transport?.connectionState?.peerConnection?.connectionState ?? "closed",
      audioState: this.audioElement
        ? (this.audioElement.paused ? "paused" : "playing")
        : "unavailable",
      errorCode: null,
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
    this.updateMicrophone();
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
    this.updateMicrophone();
    this.callbacks.onState(speaking ? "assistant_speaking" : (this.manuallyMuted ? "muted" : "connected"));
  }

  private updateMicrophone() {
    const outputBlocksMicrophone = (
      this.assistantSpeaking || this.assistantGenerating
    ) && !this.interruptionsEnabled;
    this.setMicrophoneEnabled(
      !this.closed
      && this.connected
      && !this.manuallyMuted
      && !outputBlocksMicrophone,
    );
  }

  close() {
    if (!this.closed) this.emitInternalEvent("session_disconnected", "application_internal");
    this.closed = true;
    this.connectionGenerationId = null;
    this.configurationApplied = false;
    const pendingConfigurationAck = this.pendingConfigurationAck;
    this.pendingConfigurationAck = null;
    pendingConfigurationAck?.reject(realtimeErrors.connectionLost());
    this.connected = false;
    this.assistantSpeaking = false;
    this.assistantGenerating = false;
    this.reportedProviderErrors.clear();
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
    this.stream?.getTracks().forEach((track) => {
      // RealtimeSession.close() delegates to the WebRTC transport, which stops
      // sender tracks synchronously. Only stop tracks that the transport did
      // not own yet (for example when signaling failed before addTrack()).
      if (track.readyState !== "ended") track.stop();
    });
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
    this.callAttemptId = null;
  }
}
