export class RealtimeClientError extends Error {
  constructor(public readonly code: string, message: string) {
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
  configurationMismatch: () => new RealtimeClientError("realtime_configuration_mismatch", "realtime configuration mismatch"),
  microphoneEnded: () => new RealtimeClientError("microphone_access_ended", "microphone track ended"),
};
