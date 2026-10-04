import twilio from "twilio";
import { z } from "zod";

const streamSid = z.string().regex(/^MZ[0-9a-fA-F]{32}$/);
export const callSid = z.string().regex(/^CA[0-9a-fA-F]{32}$/);
const accountSid = z.string().regex(/^AC[0-9a-fA-F]{32}$/);
const sequence = z.string().regex(/^\d+$/);
const base64 = z
  .string()
  .min(4)
  .max(16000)
  .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/);

const eventSchema = z.discriminatedUnion("event", [
  z.object({
    event: z.literal("connected"),
    protocol: z.literal("Call"),
    version: z.literal("1.0.0"),
  }),
  z.object({
    event: z.literal("start"),
    sequenceNumber: sequence,
    streamSid,
    start: z.object({
      accountSid,
      callSid,
      streamSid,
      tracks: z.array(z.literal("inbound")).length(1),
      mediaFormat: z.object({
        encoding: z.literal("audio/x-mulaw"),
        sampleRate: z.literal(8000),
        channels: z.literal(1),
      }),
    }),
  }),
  z.object({
    event: z.literal("media"),
    sequenceNumber: sequence,
    streamSid,
    media: z.object({
      track: z.literal("inbound"),
      chunk: sequence,
      timestamp: sequence,
      payload: base64,
    }),
  }),
  z.object({
    event: z.literal("stop"),
    sequenceNumber: sequence,
    streamSid,
    stop: z.object({ accountSid, callSid }),
  }),
  z.object({
    event: z.literal("mark"),
    sequenceNumber: sequence,
    streamSid,
    mark: z.object({ name: z.string().max(128) }),
  }),
  z.object({
    event: z.literal("dtmf"),
    sequenceNumber: sequence,
    streamSid,
    dtmf: z.object({
      track: z.literal("inbound_track"),
      digit: z.string().regex(/^[0-9*#ABCD]$/),
    }),
  }),
]);

export type TelephonyEvent =
  | { type: "connected" }
  | { type: "start"; streamId: string; callId: string; accountId: string }
  | { type: "audio"; streamId: string; audio: Buffer }
  | { type: "stop"; streamId: string; callId: string; accountId: string }
  | { type: "ignored"; streamId: string };

export interface TelephonyAdapter {
  parse(raw: string): TelephonyEvent;
  audio(streamId: string, mulaw: Buffer): string;
  clear(streamId: string): string;
}

export const twilioAdapter: TelephonyAdapter = {
  parse(raw) {
    const event = eventSchema.parse(JSON.parse(raw));
    switch (event.event) {
      case "connected":
        return { type: "connected" };
      case "start":
        if (event.streamSid !== event.start.streamSid)
          throw new Error("Stream ID mismatch");
        return {
          type: "start",
          streamId: event.streamSid,
          callId: event.start.callSid,
          accountId: event.start.accountSid,
        };
      case "media":
        return {
          type: "audio",
          streamId: event.streamSid,
          audio: Buffer.from(event.media.payload, "base64"),
        };
      case "stop":
        return {
          type: "stop",
          streamId: event.streamSid,
          callId: event.stop.callSid,
          accountId: event.stop.accountSid,
        };
      default:
        return { type: "ignored", streamId: event.streamSid };
    }
  },
  audio: (streamId, mulaw) =>
    JSON.stringify({
      event: "media",
      streamSid: streamId,
      media: { payload: mulaw.toString("base64") },
    }),
  clear: (streamId) => JSON.stringify({ event: "clear", streamSid: streamId }),
};

export function incomingTwiml(publicBaseUrl: string): string {
  const response = new twilio.twiml.VoiceResponse();
  const url = new URL("/twilio/media", publicBaseUrl);
  url.protocol = "wss:";
  response.connect().stream({ url: url.toString() });
  // Executed only if the bidirectional stream exits while the caller remains connected.
  response.say(
    "I'm sorry, this demo call has ended. Please call again to speak with Abe.",
  );
  response.hangup();
  return response.toString();
}

export function validSignature(
  token: string,
  signature: string | string[] | undefined,
  publicUrl: string,
  params: Record<string, string>,
  websocket = false,
): boolean {
  if (typeof signature !== "string" || signature.length > 128) return false;
  // Validate only configured public URLs, never proxy-supplied Host headers.
  const urls = websocket
    ? [
        publicUrl,
        `${publicUrl}/`,
        publicUrl.replace(/^https:/, "wss:"),
        `${publicUrl.replace(/^https:/, "wss:")}/`,
      ]
    : [publicUrl];
  return urls.some((url) =>
    twilio.validateRequest(token, signature, url, params),
  );
}
