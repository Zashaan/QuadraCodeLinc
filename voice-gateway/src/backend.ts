import { z } from "zod";
import type { ToolDefinition, ToolInvocation } from "./nova/types.js";

const toolDefinition = z
  .object({
    name: z.enum([
      "get_member",
      "resolve_member_id",
      "retrieve_plan_context",
      "search_providers",
      "calculate_benefit",
    ]),
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
    employer_id: z.string().min(1),
    plan_id: z.string().min(1),
    plan_year: z.number().int(),
    state: z.string().regex(/^[A-Z]{2}$/),
    zip_code: z.string().regex(/^\d{5}$/),
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

const canonicalId = z.string().regex(/^[A-Z0-9]{1,32}$/);
const resolutionSchema = z.discriminatedUnion("status", [
  z.object({ status: z.literal("resolved"), member_id: canonicalId }).strict(),
  z.object({ status: z.literal("repeat") }).strict(),
  z
    .object({
      status: z.literal("confirm"),
      candidates: z
        .array(canonicalId)
        .min(2)
        .max(16)
        .refine((ids) => new Set(ids).size === ids.length),
    })
    .strict(),
]);
const sourceMetadataSchema = z
  .object({
    source_document: z.string().min(1),
    source_id: z.string().min(1),
    section: z.string().min(1).nullable().optional(),
    page: z.number().int().positive().nullable().optional(),
    plan_id: z.string().min(1),
    employer: z.string().min(1),
    plan_year: z.number().int(),
    state: z.string().regex(/^[A-Z]{2}$/),
    doc_type: z.string().min(1),
  })
  .strict();
const retrievalSchema = z
  .object({
    status: z.enum(["verified", "unverified"]),
    chunks: z.array(
      z
        .object({
          text: z.string().min(1),
          metadata: sourceMetadataSchema,
          score: z.number().nonnegative().nullable().optional(),
        })
        .strict(),
    ),
    message: z.string().nullable().optional(),
  })
  .strict();
const moneyString = z.string().regex(/^\d+(?:\.\d+)?$/);
const feeSchema = z
  .object({
    provider_charge: moneyString,
    allowed_amount: moneyString.nullable().optional(),
  })
  .strict();
const providerSchema = z
  .object({
    provider_id: z.string().min(1),
    name: z.string().min(1),
    specialty: z.string().min(1),
    network_status: z.enum(["in_network", "out_of_network"]),
    zip_code: z.string().regex(/^\d{5}$/),
    latitude: moneyString.or(z.string().regex(/^-\d+(?:\.\d+)?$/)),
    longitude: moneyString.or(z.string().regex(/^-\d+(?:\.\d+)?$/)),
    fees: z.record(z.string(), feeSchema),
    source: z.string().min(1),
    verification_status: z.enum(["synthetic", "verified", "unverified"]),
    verified_at: z.string().nullable().optional(),
  })
  .strict();
const providerSearchSchema = z
  .object({
    status: z.enum(["success", "unavailable"]),
    providers: z.array(
      z
        .object({
          provider: providerSchema,
          distance_miles: moneyString.nullable().optional(),
        })
        .strict(),
    ),
    message: z.string().nullable().optional(),
    source: z.literal("synthetic_demo_provider_repository"),
  })
  .strict();
const estimateSchema = z.discriminatedUnion("status", [
  z
    .object({
      status: z.literal("estimated"),
      procedure_id: z.string().min(1),
      procedure: z.string().min(1),
      category: z.string().min(1),
      treatment_date: z.string(),
      network_status: z.enum(["in_network", "out_of_network"]),
      provider_charge: moneyString,
      allowed_amount: moneyString,
      deductible_applied: moneyString,
      amount_after_deductible: moneyString,
      coverage_rate: moneyString,
      plan_payment_before_annual_maximum: moneyString,
      plan_payment: moneyString,
      estimated_member_payment: moneyString,
      amount_not_covered: moneyString,
      annual_maximum_consumed: moneyString,
      annual_maximum_remaining_after: moneyString,
      calculation_steps: z.array(z.string()),
      assumptions: z.array(z.string()),
      warnings: z.array(z.string()),
      provenance: z.record(z.string(), z.string()),
    })
    .strict(),
  z
    .object({
      status: z.enum(["missing_input", "unavailable"]),
      missing: z.array(z.string()),
      message: z.string().min(1),
    })
    .strict(),
]);
const toolResponseSchema = z.discriminatedUnion("tool_name", [
  z
    .object({
      tool_name: z.literal("get_member"),
      tool_call_id: z.string(),
      result: resultSchema,
    })
    .strict(),
  z
    .object({
      tool_name: z.literal("resolve_member_id"),
      tool_call_id: z.string(),
      result: resolutionSchema,
    })
    .strict(),
  z
    .object({
      tool_name: z.literal("retrieve_plan_context"),
      tool_call_id: z.string(),
      result: retrievalSchema,
    })
    .strict(),
  z
    .object({
      tool_name: z.literal("search_providers"),
      tool_call_id: z.string(),
      result: providerSearchSchema,
    })
    .strict(),
  z
    .object({
      tool_name: z.literal("calculate_benefit"),
      tool_call_id: z.string(),
      result: estimateSchema,
    })
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
      const result = toolResponseSchema.safeParse(
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
