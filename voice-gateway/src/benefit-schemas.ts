/** Wire validation only. All benefit rules and arithmetic belong to Python. */
import { z } from "zod";

const text = z.string().min(1).max(200);
const network = z.enum(["in_network", "out_of_network"]);
const money = z.string().regex(/^\d{1,7}(?:\.\d{1,2})?$/);
const date = z.iso.date();
export const unavailableSchema = z
  .object({
    status: z.enum(["missing_information", "unverified", "unavailable"]),
    reason: z.string().min(1).max(300),
    missing_fields: z.array(text).max(20),
  })
  .strict();

export const sourceSchema = z
  .object({
    source_id: text,
    document: text,
    section: text.optional(),
    page: z.number().int().positive().optional(),
    plan_id: text,
    employer: text,
    plan_year: z.number().int().min(2000).max(2100),
    state: z.string().regex(/^[A-Z]{2}$/),
    doc_type: text,
    synthetic: z.boolean(),
    uri: z.string().min(1).max(1024).optional(),
  })
  .strict();

const contextSchema = z
  .object({
    intent: text.optional(),
    procedure: text.optional(),
    provider_id: text.optional(),
    zip_code: z
      .string()
      .regex(/^\d{5}$/)
      .optional(),
    constraints: z.array(text).max(5).optional(),
    latest_correction: text.optional(),
  })
  .strict();
export const contextResultSchema = z.union([
  unavailableSchema,
  z
    .object({
      status: z.literal("updated"),
      context: contextSchema,
      member_id: text.optional(),
      plan_id: text.optional(),
    })
    .strict(),
]);

export const retrievalResultSchema = z.union([
  unavailableSchema,
  z
    .object({
      status: z.literal("verified"),
      chunks: z
        .array(
          z
            .object({
              text: z.string().min(1).max(6000),
              source: sourceSchema,
            })
            .strict(),
        )
        .min(1)
        .max(4),
    })
    .strict(),
]);

export const providerSchema = z
  .object({
    provider_id: text,
    name: text,
    specialty: text,
    plan_id: text,
    network_status: network,
    zip_code: z.string().regex(/^\d{5}$/),
    distances_miles: z.record(z.string(), z.number().int().nonnegative()),
    fees: z.record(
      z.string(),
      z
        .object({
          provider_charge: money.optional(),
          allowed_amount: money.optional(),
          source: text,
        })
        .strict(),
    ),
    source: text,
    verification_status: z.literal("synthetic"),
    verified_on: text,
    synthetic: z.literal(true),
  })
  .strict();
export const providerResultSchema = z.union([
  unavailableSchema,
  z
    .object({
      status: z.literal("success"),
      providers: z.array(providerSchema).max(10),
      ordering: z.literal("provider_id; no recommendation or ranking"),
    })
    .strict(),
]);

export const estimateSchema = z
  .object({
    status: z.literal("estimated"),
    member_id: text,
    plan_id: text,
    treatment_date: date,
    balances_as_of: date,
    procedure: text,
    network_status: network,
    provider_charge: money,
    allowed_amount: money,
    deductible_applied: money,
    amount_after_deductible: money,
    plan_payment: money,
    estimated_member_payment: money,
    amount_not_covered: money,
    contractual_write_off: money,
    annual_maximum_consumed: money,
    annual_maximum_remaining_afterward: money,
    steps: z.array(z.string()).max(20),
    assumptions: z.array(z.string()).max(20),
    warnings: z.array(z.string()).max(20),
    sources: z.array(sourceSchema).max(4),
    fee_source: text,
    allowed_amount_source: text,
    network_source: text,
  })
  .strict();
export const calculationResultSchema = z.union([
  unavailableSchema,
  estimateSchema,
]);
