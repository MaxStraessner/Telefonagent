import { describe, expect, it } from "vitest";
import type { RuntimeToolDefinition } from "../src/types/api";
import {
  digestRuntimeValue,
  normalizeAcknowledgedWireTools,
  outboundWireTools,
  strictSchemaProjection,
  validateCanonicalTools,
} from "../src/features/realtime/appliedConfiguration";

function makeTool(parameters: Record<string, unknown>, overrides: Record<string, unknown> = {}): RuntimeToolDefinition {
  return {
    type: "function",
    name: "read_only_test",
    description: "Read-only test tool.",
    strict: true,
    parameters: parameters as RuntimeToolDefinition["parameters"],
    ...overrides,
  } as RuntimeToolDefinition;
}

describe("Realtime tool projections", () => {
  it("rejects missing and false strict values before the session starts", () => {
    expect(() => validateCanonicalTools([makeTool({ type: "object", properties: {}, required: [], additionalProperties: false }, { strict: false })])).toThrow(/strict=true/);
    const missing = makeTool({ type: "object", properties: {}, required: [], additionalProperties: false });
    delete (missing as unknown as Record<string, unknown>).strict;
    expect(() => validateCanonicalTools([missing])).toThrow(/strict=true/);
  });

  it("preserves mandatory nullable fields while making only optional fields nullable", () => {
    const schema = {
      type: "object",
      properties: {
        requiredNullable: { type: ["string", "null"], format: "email" },
        optionalText: { type: "string", pattern: "^[A-Z]+$", default: null },
        nested: {
          type: "object",
          properties: { amount: { type: "number", minimum: 1, maximum: 10 } },
          required: ["amount"],
          additionalProperties: false,
        },
        values: {
          type: "array",
          items: { type: "string", enum: ["a", "b"], const: "a" },
        },
        reference: { $ref: "#/$defs/Entry" },
        empty: { type: "object", properties: {}, required: [], additionalProperties: false },
      },
      required: ["requiredNullable", "nested", "values", "reference", "empty"],
      additionalProperties: false,
      $defs: {
        Entry: {
          type: "object",
          properties: { id: { type: "string" } },
          required: ["id"],
          additionalProperties: false,
        },
      },
      anyOf: [{ type: "string" }, { type: "null" }],
    };

    const projected = strictSchemaProjection(schema) as Record<string, unknown>;
    const properties = projected.properties as Record<string, Record<string, unknown>>;
    expect(projected.required).toEqual(["requiredNullable", "optionalText", "nested", "values", "reference", "empty"]);
    expect(properties.requiredNullable).toEqual({ type: ["string", "null"], format: "email" });
    expect(properties.optionalText).toEqual({
      anyOf: [{ type: "string", pattern: "^[A-Z]+$" }, { type: "null" }],
    });
    expect((properties.nested.properties as Record<string, unknown>)).toBeDefined();
    expect((properties.values.items as Record<string, unknown>).enum).toEqual(["a", "b"]);
    expect((projected.$defs as Record<string, Record<string, unknown>>).Entry.additionalProperties).toBe(false);
    expect(properties.empty.required).toEqual([]);
  });

  it("creates separate canonical, outbound and acknowledged projections", async () => {
    const tool = makeTool({
      type: "object",
      properties: { optionalText: { type: "string" } },
      required: [],
      additionalProperties: false,
    });
    const outbound = outboundWireTools([tool]);
    const acknowledged = normalizeAcknowledgedWireTools([{ ...outbound[0], strict: true }]);
    expect(outbound[0]).not.toHaveProperty("strict");
    expect(acknowledged).toEqual(outbound);
    await expect(digestRuntimeValue(outbound)).resolves.toBe(await digestRuntimeValue(acknowledged));
  });

  it("fails closed on unknown provider schema fields", () => {
    expect(() => normalizeAcknowledgedWireTools([{
      type: "function",
      name: "read_only_test",
      description: "Read-only test tool.",
      parameters: { type: "object", properties: {}, required: [], additionalProperties: false },
      provider_changed_schema: true,
    }])).toThrow(/Unbekannte Provider-Toolfelder/);
  });
});

