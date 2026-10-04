import { z } from "zod";

const origin = z.url().refine((value) => {
  const url = new URL(value);
  return (
    !url.username &&
    !url.password &&
    !url.search &&
    !url.hash &&
    url.pathname === "/"
  );
}, "Must be an origin without credentials, path, query, or fragment");

const environment = z.object({
  PORT: z.coerce.number().int().min(1).max(65535).default(3000),
  PUBLIC_BASE_URL: origin.refine(
    (value) => new URL(value).protocol === "https:",
  ),
  TWILIO_ACCOUNT_SID: z.string().regex(/^AC[0-9a-fA-F]{32}$/),
  TWILIO_AUTH_TOKEN: z.string().min(16),
  AWS_REGION: z
    .string()
    .regex(/^[a-z]{2}(-[a-z]+)+-\d$/)
    .default("us-east-1"),
  NOVA_MODEL_ID: z.string().min(1).default("amazon.nova-2-sonic-v1:0"),
  NOVA_VOICE_ID: z.string().min(1).default("tiffany"),
  ABE_BACKEND_URL: origin.default("http://127.0.0.1:8000").refine((value) => {
    const url = new URL(value);
    return (
      url.protocol === "https:" ||
      (url.protocol === "http:" &&
        ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
    );
  }, "Use HTTPS or local loopback HTTP"),
  ABE_INTERNAL_TOKEN: z
    .string()
    .min(32)
    .regex(/^[\x21-\x7e]+$/),
});

export type Config = ReturnType<typeof loadConfig>;

export function loadConfig(env: NodeJS.ProcessEnv) {
  const parsed = environment.safeParse(env);
  if (!parsed.success) {
    // Do not include Zod's input values: environment variables can contain secrets.
    throw new Error(
      `Invalid configuration: ${[...new Set(parsed.error.issues.map((issue) => issue.path[0]))].join(", ")}`,
    );
  }
  const value = parsed.data;
  return {
    port: value.PORT,
    publicBaseUrl: new URL(value.PUBLIC_BASE_URL).origin,
    accountSid: value.TWILIO_ACCOUNT_SID,
    authToken: value.TWILIO_AUTH_TOKEN,
    region: value.AWS_REGION,
    modelId: value.NOVA_MODEL_ID,
    voiceId: value.NOVA_VOICE_ID,
    backendUrl: new URL(value.ABE_BACKEND_URL).origin,
    internalToken: value.ABE_INTERNAL_TOKEN,
  };
}
