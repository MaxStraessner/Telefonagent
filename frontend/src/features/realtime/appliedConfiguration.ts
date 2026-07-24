import type { AppliedRealtimeConfiguration, RuntimeManifest, RuntimeToolDefinition } from "../../types/api";

type JsonObject = Record<string, unknown>;
const CANONICAL_TOOL_KEYS = new Set([
  "type", "name", "description", "parameters", "strict", "deferLoading", "providerData",
  "needsApproval", "timeoutMs", "timeoutBehavior", "inputGuardrails", "outputGuardrails",
]);

export interface ToolProjectionDiagnostics {
  canonical_tools_digest: string;
  outbound_wire_tools_digest: string;
  acknowledged_tools_digest: string;
  transformation_stage: "wire_projection" | "acknowledged_projection";
}

export class ToolProjectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolProjectionError";
  }
}

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

function schemaAllowsNull(schema: unknown): boolean {
  const value = objectValue(schema);
  if (!value) return false;
  if (value.type === "null") return true;
  if (Array.isArray(value.type) && value.type.includes("null")) return true;
  return ["anyOf", "oneOf", "allOf"].some((key) => {
    const entries = value[key];
    return Array.isArray(entries) && entries.some(schemaAllowsNull);
  });
}

function wrapNullable(schema: unknown): unknown {
  if (!objectValue(schema) || schemaAllowsNull(schema)) return schema;
  const wrapped: JsonObject = {};
  if (typeof objectValue(schema)?.description === "string") wrapped.description = objectValue(schema)?.description;
  wrapped.anyOf = [schema, { type: "null" }];
  return wrapped;
}

/** Mirrors Agents 0.13.5 strictToolSchema.ts for plain JSON schemas. */
export function strictSchemaProjection(schema: unknown): unknown {
  if (Array.isArray(schema)) return schema.map(strictSchemaProjection);
  const source = objectValue(schema);
  if (!source) return schema;
  const record: JsonObject = structuredClone(source);
  if (record.type === "object" && objectValue(record.properties)) {
    const properties = objectValue(record.properties) ?? {};
    const originalRequired = new Set(
      Array.isArray(record.required) ? record.required.filter((item): item is string => typeof item === "string") : [],
    );
    const normalizedProperties: JsonObject = {};
    Object.entries(properties).forEach(([key, value]) => {
      const normalized = strictSchemaProjection(value);
      normalizedProperties[key] = originalRequired.has(key) ? normalized : wrapNullable(normalized);
    });
    record.properties = normalizedProperties;
    record.required = Object.keys(normalizedProperties);
    record.additionalProperties = false;
  }
  (["$defs", "definitions"] as const).forEach((key) => {
    const nested = objectValue(record[key]);
    if (nested) record[key] = Object.fromEntries(Object.entries(nested).map(([name, value]) => [name, strictSchemaProjection(value)]));
  });
  (["anyOf", "allOf", "oneOf"] as const).forEach((key) => {
    if (Array.isArray(record[key])) record[key] = record[key].map(strictSchemaProjection);
  });
  if (Array.isArray(record.items)) record.items = record.items.map(strictSchemaProjection);
  else if (objectValue(record.items)) record.items = strictSchemaProjection(record.items);
  if (record.default === null) delete record.default;
  return record;
}

export function validateCanonicalTools(tools: RuntimeToolDefinition[]): RuntimeToolDefinition[] {
  if (!Array.isArray(tools)) throw new ToolProjectionError("Der kanonische Toolvertrag ist keine Liste.");
  return tools.map((toolDefinition, index) => {
    const unknown = Object.keys(toolDefinition).filter(
      (key) => !CANONICAL_TOOL_KEYS.has(key),
    );
    if (unknown.length) throw new ToolProjectionError(`Unbekannte kanonische Toolfelder: ${unknown.join(", ")}.`);
    if (toolDefinition.type !== "function" || !toolDefinition.name || toolDefinition.strict !== true) {
      throw new ToolProjectionError(`Tool ${toolDefinition.name ?? index} muss strict=true verwenden.`);
    }
    if (!toolDefinition.description || !objectValue(toolDefinition.parameters)) {
      throw new ToolProjectionError(`Tool ${toolDefinition.name} enthält kein gültiges Schema.`);
    }
    return toolDefinition;
  });
}

export function outboundWireTools(tools: RuntimeToolDefinition[]): JsonObject[] {
  return validateCanonicalTools(tools).map((toolDefinition) => ({
      type: toolDefinition.type,
      name: toolDefinition.name,
      description: toolDefinition.description,
      parameters: strictSchemaProjection(toolDefinition.parameters),
    }));
}

export function normalizeAcknowledgedWireTools(value: unknown): JsonObject[] {
  if (!Array.isArray(value)) throw new ToolProjectionError("session.updated enthält keine Toolliste.");
  return value.map((entry, index) => {
    const tool = objectValue(entry);
    if (!tool) throw new ToolProjectionError(`Bestätigtes Tool ${index} ist kein Objekt.`);
    const unknown = Object.keys(tool).filter((key) => !["type", "name", "description", "parameters", "strict"].includes(key));
    if (unknown.length) throw new ToolProjectionError(`Unbekannte Provider-Toolfelder: ${unknown.join(", ")}.`);
    if (tool.type !== "function" || typeof tool.name !== "string" || typeof tool.description !== "string" || !objectValue(tool.parameters)) {
      throw new ToolProjectionError(`Bestätigtes Tool ${index} enthält kein gültiges Wire-Schema.`);
    }
    return {
      type: tool.type,
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
    };
  });
}

function sessionValue(event: unknown): JsonObject {
  return objectValue(objectValue(event)?.session) ?? {};
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
  manifest: RuntimeManifest,
): Promise<{ applied: AppliedRealtimeConfiguration; diagnostics: ToolProjectionDiagnostics }> {
  const session = sessionValue(event);
  const audio = objectValue(session.audio) ?? {};
  const input = objectValue(audio.input) ?? {};
  const output = objectValue(audio.output) ?? {};
  const transcription = objectValue(input.transcription);
  const canonicalTools = validateCanonicalTools(manifest.tools);
  const outboundTools = outboundWireTools(canonicalTools);
  const canonicalDigest = await digestRuntimeValue(canonicalTools);
  if (canonicalDigest !== manifest.tools_digest) {
    throw new ToolProjectionError("Der lokale kanonische Tool-Digest stimmt nicht mit dem Manifest überein.");
  }
  const outboundDigest = await digestRuntimeValue(outboundTools);
  const rawTools = session.tools;
  if (!Array.isArray(rawTools) && canonicalTools.length > 0) {
    throw new ToolProjectionError("session.updated bestätigt keine aktivierten Tools.");
  }
  const acknowledgedTools = normalizeAcknowledgedWireTools(Array.isArray(rawTools) ? rawTools : []);
  const acknowledgedDigest = await digestRuntimeValue(acknowledgedTools);
  const instructions = session.instructions;
  const applied = Object.fromEntries(Object.entries({
    model: typeof session.model === "string" ? session.model : undefined,
    voice: typeof output.voice === "string" ? output.voice : undefined,
    speed: typeof output.speed === "number" ? output.speed : undefined,
    language: typeof transcription?.language === "string" ? transcription.language : undefined,
    prompt_digest: typeof instructions === "string" ? await sha256(instructions) : undefined,
    tool_names: acknowledgedTools.map((tool) => String(tool.name)),
    tools_digest: acknowledgedDigest,
    vad: normalizedVad(firstDefined(input.turn_detection, input.turnDetection)),
  }).filter(([, value]) => value !== undefined)) as AppliedRealtimeConfiguration;
  return {
    applied,
    diagnostics: {
      canonical_tools_digest: canonicalDigest,
      outbound_wire_tools_digest: outboundDigest,
      acknowledged_tools_digest: acknowledgedDigest,
      transformation_stage: "acknowledged_projection",
    },
  };
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
