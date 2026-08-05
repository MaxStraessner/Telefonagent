import { RealtimeAgent, RealtimeSession, tool } from "@openai/agents/realtime";
import { describe, expect, it, vi } from "vitest";

class FakeRealtimeTransport {
  status = "disconnected" as const;
  muted = false;
  private readonly handlers = new Map<string, Set<(...args: unknown[]) => void>>();

  connect = vi.fn(async () => undefined);
  close = vi.fn();
  mute = vi.fn();
  interrupt = vi.fn();
  resetHistory = vi.fn();
  updateSessionConfig = vi.fn();
  sendEvent = vi.fn();
  sendMessage = vi.fn();
  addImage = vi.fn();
  sendAudio = vi.fn();
  sendMcpResponse = vi.fn();
  requestResponse = vi.fn();
  sendFunctionCallOutput = vi.fn((toolCall: { callId: string }, output: string, startResponse: boolean) => {
    if (startResponse) this.requestResponse();
    return { toolCall, output };
  });

  on(type: string, handler: (...args: unknown[]) => void) {
    const listeners = this.handlers.get(type) ?? new Set();
    listeners.add(handler);
    this.handlers.set(type, listeners);
    return this;
  }

  off(type: string, handler: (...args: unknown[]) => void) {
    this.handlers.get(type)?.delete(handler);
    return this;
  }

  emit(type: string, ...args: unknown[]) {
    this.handlers.get(type)?.forEach((handler) => handler(...args));
  }
}

describe("@openai/agents-realtime tool continuation contract", () => {
  it("submits the result with the original call_id and requests exactly one response", async () => {
    const transport = new FakeRealtimeTransport();
    const execute = vi.fn().mockResolvedValue({ available: true });
    const availabilityTool = tool({
      name: "check_appointment_availability",
      description: "Checks availability.",
      parameters: {
        type: "object",
        properties: { appointment_type_id: { type: "string" } },
        required: ["appointment_type_id"],
        additionalProperties: false,
      },
      strict: true,
      execute,
    });
    const agent = new RealtimeAgent({
      name: "SDK contract test",
      instructions: "Continue after every tool result.",
      tools: [availabilityTool],
    });
    const session = new RealtimeSession(agent, {
      transport: transport as never,
      model: "gpt-realtime-2.1",
      tracingDisabled: true,
    });
    await session.connect({ apiKey: "ek_local_test" });

    const completed = new Promise<void>((resolve, reject) => {
      session.on("agent_tool_end", () => resolve());
      session.on("error", (event) => reject(event.error));
    });
    transport.emit("turn_started", {
      type: "response_started",
      providerData: { response: { id: "response-tool" } },
    });
    transport.emit("function_call", {
      id: "item-tool",
      type: "function_call",
      name: "check_appointment_availability",
      callId: "call-original",
      arguments: JSON.stringify({ appointment_type_id: "type-1" }),
      responseId: "response-tool",
    });
    await completed;

    expect(execute).toHaveBeenCalledOnce();
    expect(transport.sendFunctionCallOutput).toHaveBeenCalledOnce();
    expect(transport.sendFunctionCallOutput).toHaveBeenCalledWith(
      expect.objectContaining({ callId: "call-original" }),
      JSON.stringify({ available: true }),
      true,
    );
    expect(transport.requestResponse).toHaveBeenCalledOnce();
    session.close();
  });
});
