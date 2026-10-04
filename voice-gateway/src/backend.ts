import { z } from "zod";
import type { ToolDefinition, ToolInvocation } from "./nova/types.js";

const toolDefinition = z
  .object({
    name: z.literal("get_member"),
    description: z.string().min(1),
    input_schema: z.record(z.string(), z.unknown()),
  })
  .strict();

const sessionSchema = z
  .object({
    session_id: z.uuid(),
    system_prompt: z.string().min(1),
    tools: z.array(toolDefinition).min(1).max(16),
  })
  .strict();

const amount = z.number().int().nonnegative();
const memberSchema = z
  .object({
    member_id: z.string().min(1),
    name: z.string().min(1),
    plan_id: z.string().min(1),
    currency: z.literal("USD"),
    annual_maximum: amount,
    annual_maximum_used: amount,
    annual_maximum_remaining: amount,
    deductible_total: amount,
    deductible_used: amount,
    deductible_remaining: amount,
    fsa_balance: amount,
  })
  .strict();
const resultSchema = z.discriminatedUnion("status", [
  z.object({ status: z.literal("success"), member: memberSchema }).strict(),
  z
    .object({ status: z.literal("not_found"), member_id: z.string().min(1) })
    .strict(),
]);

export interface BackendSession {
  session_id: string;
  system_prompt: string;
  tools: ToolDefinition[];
}

export interface AbeBackend {
  createSession(callId: string, signal: AbortSignal): Promise<BackendSession>;
  invokeTool(
    sessionId: string,
    tool: ToolInvocation,
    signal: AbortSignal,
  ): Promise<unknown>;
  deleteSession(sessionId: string): Promise<void>;
}

export class BackendError extends Error {
  constructor(
    readonly code: "unavailable" | "invalid_request" | "invalid_response",
  ) {
    super(`Backend ${code}`);
  }
}

export function createBackendClient(
  baseUrl: string,
  token: string,
  timeoutMs = 5000,
): AbeBackend {
  async function request(
    path: string,
    method: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<unknown> {
    const timeout = AbortSignal.timeout(timeoutMs);
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        redirect: "error",
      });
      if (!response.ok) {
        await response.body?.cancel();
        throw new BackendError(
          [400, 422].includes(response.status)
            ? "invalid_request"
            : "unavailable",
        );
      }
      if (response.status === 204) return undefined;
      // The service is private; still bound response memory and reject malformed outputs.
      const reader = response.body?.getReader();
      if (!reader) throw new BackendError("invalid_response");
      const chunks: Uint8Array[] = [];
      let bytes = 0;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        bytes += chunk.value.byteLength;
        if (bytes > 64 * 1024) {
          await reader.cancel();
          throw new BackendError("invalid_response");
        }
        chunks.push(chunk.value);
      }
      try {
        return JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        throw new BackendError("invalid_response");
      }
    } catch (error) {
      if (error instanceof BackendError) throw error;
      throw new BackendError("unavailable");
    }
  }

  return {
    async createSession(callId, signal) {
      const result = sessionSchema.safeParse(
        await request("/sessions", "POST", { call_id: callId }, signal),
      );
      if (!result.success) throw new BackendError("invalid_response");
      return result.data;
    },
    async invokeTool(sessionId, tool, signal) {
      const body = {
        tool_name: tool.toolName,
        tool_call_id: tool.toolUseId,
        arguments: tool.input,
      };
      const result = z
        .object({
          tool_name: z.literal("get_member"),
          tool_call_id: z.string(),
          result: resultSchema,
        })
        .strict()
        .safeParse(
          await request(
            `/sessions/${encodeURIComponent(sessionId)}/tools`,
            "POST",
            body,
            signal,
          ),
        );
      if (
        !result.success ||
        result.data.tool_call_id !== tool.toolUseId ||
        result.data.tool_name !== tool.toolName
      ) {
        throw new BackendError("invalid_response");
      }
      return result.data.result;
    },
    async deleteSession(sessionId) {
      await request(`/sessions/${encodeURIComponent(sessionId)}`, "DELETE");
    },
  };
}
