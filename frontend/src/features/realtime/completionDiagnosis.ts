export interface ResponseCompletionDiagnosis {
  status: string | null;
  reason: string;
  interruption: boolean;
}

interface ResponseEvidence {
  status?: unknown;
  status_details?: unknown;
  incomplete_details?: unknown;
  output?: unknown;
}

export function diagnoseResponseCompletion(
  response: unknown,
  functionCallRequested = false,
  functionCallArgumentsComplete = false,
): ResponseCompletionDiagnosis {
  if (!response || typeof response !== "object") {
    return { status: null, reason: "missing_response", interruption: false };
  }
  const value = response as ResponseEvidence;
  const status = typeof value.status === "string" ? value.status : null;
  const detail = JSON.stringify(value.status_details ?? value.incomplete_details ?? "").toLowerCase();
  if (status === "completed") return { status, reason: "completed", interruption: false };
  if (status === "cancelled") return { status, reason: "cancelled", interruption: true };
  if (status === "failed") return { status, reason: detail || "response_failed", interruption: false };
  if (status !== "incomplete") return { status, reason: status ?? "unknown", interruption: false };
  if (/turn_detected|interrupt|cancel|truncat/.test(detail)) {
    return { status, reason: "interrupted", interruption: true };
  }
  if (functionCallRequested && !functionCallArgumentsComplete) {
    return { status, reason: "incomplete_function_call", interruption: false };
  }
  if (/max_output_tokens|max_tokens|token/.test(detail)) {
    return { status, reason: "output_token_limit", interruption: false };
  }
  if (/content_filter/.test(detail)) {
    return { status, reason: "content_filter", interruption: false };
  }
  return { status, reason: detail || "incomplete_unknown", interruption: false };
}
