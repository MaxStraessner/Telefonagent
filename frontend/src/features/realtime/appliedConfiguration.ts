import type { AppliedRealtimeConfiguration, RuntimeManifest } from "../../types/api";

type JsonObject = Record<string, unknown>;

function objectValue(value: unknown): JsonObject | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : undefined;
}

function firstDefined(...values: unknown[]) {
  return values.find((value) => value !== undefined && value !== null);
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  const object = objectValue(value);
  if (!object) return value;
  return Object.fromEntries(
    Object.keys(object).sort().map((key) => [key, canonicalize(object[key])]),
  );
}

async function sha256(value: unknown): Promise<string | undefined> {
  if (!globalThis.crypto?.subtle) return undefined;
  const encoded = new TextEncoder().encode(JSON.stringify(canonicalize(value)));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", encoded);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function digestRuntimeValue(value: unknown): Promise<string> {
  const digest = await sha256(value);
  if (digest) return digest;
  const canonical = JSON.stringify(canonicalize(value));
  let fallback = 2166136261;
  for (let index = 0; index < canonical.length; index += 1) {
    fallback ^= canonical.charCodeAt(index);
    fallback = Math.imul(fallback, 16777619);
  }
  return `local-${(fallback >>> 0).toString(16).padStart(8, "0")}`;
}

function toolDefinitions(value: unknown): unknown[] | undefined {
  return Array.isArray(value) ? value : undefined;
}

function toolNames(tools: unknown[] | undefined): string[] | undefined {
  if (!tools) return undefined;
  return tools.flatMap((entry) => {
    const name = objectValue(entry)?.name;
    return typeof name === "string" ? [name] : [];
  });
}

function normalizedVad(value: unknown): Record<string, unknown> | undefined {
  const vad = objectValue(value);
  if (!vad) return undefined;
  const type = vad.type;
  if (type === "semantic_vad") {
    return {
      type,
      eagerness: vad.eagerness,
      create_response: firstDefined(vad.create_response, vad.createResponse),
      interrupt_response: firstDefined(vad.interrupt_response, vad.interruptResponse),
    };
  }
  if (type === "server_vad") {
    return Object.fromEntries(Object.entries({
      type,
      threshold: vad.threshold,
      prefix_padding_ms: firstDefined(vad.prefix_padding_ms, vad.prefixPaddingMs),
      silence_duration_ms: firstDefined(vad.silence_duration_ms, vad.silenceDurationMs),
      idle_timeout_ms: firstDefined(vad.idle_timeout_ms, vad.idleTimeoutMs),
      create_response: firstDefined(vad.create_response, vad.createResponse),
      interrupt_response: firstDefined(vad.interrupt_response, vad.interruptResponse),
    }).filter(([, item]) => item !== undefined && item !== null));
  }
  return undefined;
}

export async function normalizeAppliedConfiguration(
  event: unknown,
): Promise<AppliedRealtimeConfiguration> {
  const session = objectValue(objectValue(event)?.session) ?? {};
  const audio = objectValue(session.audio) ?? {};
  const input = objectValue(audio.input) ?? {};
  const output = objectValue(audio.output) ?? {};
  const transcription = objectValue(input.transcription);
  const instructions = session.instructions;
  const tools = toolDefinitions(session.tools);
  return Object.fromEntries(Object.entries({
    model: typeof session.model === "string" ? session.model : undefined,
    voice: typeof output.voice === "string" ? output.voice : undefined,
    speed: typeof output.speed === "number" ? output.speed : undefined,
    language: typeof transcription?.language === "string" ? transcription.language : undefined,
    prompt_digest: typeof instructions === "string" ? await sha256(instructions) : undefined,
    tool_names: toolNames(tools),
    tools_digest: tools ? await sha256(tools) : undefined,
    vad: normalizedVad(firstDefined(input.turn_detection, input.turnDetection)),
  }).filter(([, value]) => value !== undefined)) as AppliedRealtimeConfiguration;
}

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
  if (
    vad.threshold === null
    || vad.prefix_padding_ms === null
    || vad.silence_duration_ms === null
  ) {
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
