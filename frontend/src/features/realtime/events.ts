const NORMALIZED_EVENT_TYPES: Record<string, string> = {
  "input_audio_buffer.speech_started": "speech_started",
  "input_audio_buffer.speech_stopped": "speech_stopped",
  "response.created": "response_created",
  "response.done": "response_completed",
};

const REDACTED_KEYS = /authorization|api.?key|client.?secret|value|audio|delta|transcript|text/i;

export function normalizedRealtimeEventType(type: string): string {
  return NORMALIZED_EVENT_TYPES[type] ?? type;
}

export function sanitizedRealtimeEventDetail(event: unknown): string | undefined {
  try {
    return JSON.stringify(event, (key, value) => {
      if (key && REDACTED_KEYS.test(key)) return "[redacted]";
      if (value instanceof ArrayBuffer) return `[binary:${value.byteLength}]`;
      return value;
    }).slice(0, 1000);
  } catch {
    return undefined;
  }
}
