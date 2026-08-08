import type { RuntimeManifest } from "../../types/api";

export function manifestTurnDetection(manifest: RuntimeManifest) {
  const vad = manifest.vad;
  if (vad.type === "semantic_vad") {
    if (!vad.eagerness) throw new Error("Semantic VAD benötigt eine Reaktionsbereitschaft.");
    return {
      type: "semantic_vad" as const,
      eagerness: vad.eagerness,
      createResponse: vad.create_response,
      interruptResponse: vad.interrupt_response,
    };
  }
  if (vad.threshold === null || vad.prefix_padding_ms === null || vad.silence_duration_ms === null) {
    throw new Error("Server VAD ist unvollständig konfiguriert.");
  }
  return {
    type: "server_vad" as const,
    threshold: vad.threshold,
    prefixPaddingMs: vad.prefix_padding_ms,
    silenceDurationMs: vad.silence_duration_ms,
    createResponse: vad.create_response,
    interruptResponse: vad.interrupt_response,
  };
}
