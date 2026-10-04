import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { WebSocketServer } from "ws";
import type { AbeBackend } from "./backend.js";
import { VoiceCall } from "./call.js";
import type { Config } from "./config.js";
import type { Logger } from "./log.js";
import type { NovaFactory } from "./nova/types.js";
import {
  callSid,
  incomingTwiml,
  twilioAdapter,
  validSignature,
} from "./telephony.js";

function reply(
  response: ServerResponse,
  status: number,
  body: string,
  contentType = "text/plain",
) {
  response.writeHead(status, {
    "Content-Type": contentType,
    "Cache-Control": "no-store",
  });
  response.end(body);
}

async function readForm(
  request: IncomingMessage,
): Promise<Record<string, string>> {
  if (
    !request.headers["content-type"]?.startsWith(
      "application/x-www-form-urlencoded",
    )
  )
    throw new Error("Invalid form");
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of request) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += data.length;
    if (bytes > 16_384) throw new Error("Form too large");
    chunks.push(data);
  }
  const params = new URLSearchParams(Buffer.concat(chunks).toString("utf8"));
  const result: Record<string, string> = Object.create(null);
  for (const [key, value] of params) {
    if (key in result) throw new Error("Duplicate form field");
    result[key] = value;
  }
  return result;
}

export function createGateway(
  config: Config,
  backend: AbeBackend,
  novaFactory: NovaFactory,
  log: Logger,
) {
  let stopping = false;
  const calls = new Set<VoiceCall>();
  const streams = new WebSocketServer({
    noServer: true,
    maxPayload: 32_768,
    perMessageDeflate: false,
  });
  const server = createServer((request, response) => {
    void (async () => {
      if (request.url === "/health" && request.method === "GET") {
        reply(response, 200, '{"status":"ok"}', "application/json");
        return;
      }
      if (request.url !== "/twilio/incoming" || request.method !== "POST") {
        reply(response, 404, "Not found");
        return;
      }
      const params = await readForm(request);
      if (
        !validSignature(
          config.authToken,
          request.headers["x-twilio-signature"],
          `${config.publicBaseUrl}/twilio/incoming`,
          params,
        ) ||
        params.AccountSid !== config.accountSid
      ) {
        reply(response, 403, "Forbidden");
        return;
      }
      if (!callSid.safeParse(params.CallSid).success) {
        reply(response, 400, "Invalid request");
        return;
      }
      if (stopping || calls.size >= 4) {
        reply(response, 503, "Unavailable");
        return;
      }
      log("incoming_call_received");
      reply(response, 200, incomingTwiml(config.publicBaseUrl), "text/xml");
    })().catch(() => {
      if (!response.headersSent) reply(response, 400, "Invalid request");
      else response.destroy();
    });
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5000;
  server.on("clientError", (_error, socket) => {
    socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
  });
  server.on("upgrade", (request, socket, head) => {
    socket.on("error", () => socket.destroy());
    if (
      stopping ||
      calls.size >= 4 ||
      request.url !== "/twilio/media" ||
      request.method !== "GET" ||
      !validSignature(
        config.authToken,
        request.headers["x-twilio-signature"],
        `${config.publicBaseUrl}/twilio/media`,
        {},
        true,
      )
    ) {
      socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
    streams.handleUpgrade(request, socket, head, (websocket) => {
      const call = new VoiceCall({
        socket: websocket,
        telephony: twilioAdapter,
        backend,
        novaFactory,
        accountSid: config.accountSid,
        log,
      });
      calls.add(call);
      websocket.once("close", () => {
        void call.close().finally(() => calls.delete(call));
      });
    });
  });
  return {
    server,
    async close() {
      stopping = true;
      const stopped = new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
      await Promise.allSettled([...calls].map((call) => call.close()));
      streams.close();
      server.closeAllConnections();
      await stopped;
    },
  };
}
