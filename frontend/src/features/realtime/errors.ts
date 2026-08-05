export class RealtimeClientError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly details?: Readonly<Record<string, unknown>>,
  ) {
    super(message);
    this.name = "RealtimeClientError";
  }
}

export const realtimeErrors = {
  browserUnsupported: () => new RealtimeClientError("browser_unsupported", "browser realtime APIs unavailable"),
  insecureContext: () => new RealtimeClientError("browser_insecure_context", "secure browser context required"),
  audioElementUnavailable: () => new RealtimeClientError("audio_element_unavailable", "audio element unavailable"),
  audioPlaybackBlocked: () => new RealtimeClientError("audio_playback_blocked", "audio playback blocked"),
  connectionTimeout: () => new RealtimeClientError("realtime_connection_timeout", "realtime connection timed out"),
  configurationAckTimeout: () => new RealtimeClientError("realtime_configuration_ack_timeout", "realtime configuration acknowledgement timed out"),
  connectionLost: () => new RealtimeClientError("realtime_connection_lost", "realtime connection disconnected"),
  clientSecretExpired: () => new RealtimeClientError("realtime_client_secret_expired", "ephemeral client secret expired"),
  bootstrapMismatch: () => new RealtimeClientError("realtime_bootstrap_mismatch", "realtime bootstrap values do not match"),
  signalingFailed: (details?: Readonly<Record<string, unknown>>) => new RealtimeClientError(
    "realtime_signaling_failed",
    "realtime signaling failed",
    details,
  ),
  providerRequestFailed: (details?: Readonly<Record<string, unknown>>) => {
    const responseCreateRejected = details?.providerErrorParam === "response.create";
    return new RealtimeClientError(
      responseCreateRejected ? "realtime_response_create_rejected" : "realtime_provider_request_failed",
      responseCreateRejected ? "realtime response creation was rejected" : "realtime provider request failed",
      details,
    );
  },
  microphoneEnded: () => new RealtimeClientError("microphone_access_ended", "microphone track ended"),
};
