import assert from "node:assert/strict";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import test from "node:test";
import twilio from "twilio";
import { WebSocket } from "ws";
import type { AbeBackend } from "../src/backend.js";
import { loadConfig } from "../src/config.js";
import type { NovaFactory } from "../src/nova/types.js";
import { createGateway } from "../src/server.js";
import { twilioAdapter, validSignature } from "../src/telephony.js";

const config = loadConfig({
  PUBLIC_BASE_URL: "https://example.test",
  TWILIO_ACCOUNT_SID: `AC${"a".repeat(32)}`,
  TWILIO_AUTH_TOKEN: "test-token-never-real",
  ABE_INTERNAL_TOKEN: "test-only-internal-token-32-characters",
});
const callSid = `CA${"b".repeat(32)}`;

async function start() {
  const backend: AbeBackend = {
    createSession: async () => {
      throw new Error("Not expected");
    },
    invokeTool: async () => {
      throw new Error("Not expected");
    },
    deleteSession: async () => {},
  };
  const nova: NovaFactory = {
    create: () => {
      throw new Error("Not expected");
    },
  };
  const gateway = createGateway(config, backend, nova, () => {});
  gateway.server.listen(0, "127.0.0.1");
  await once(gateway.server, "listening");
  const port = (gateway.server.address() as AddressInfo).port;
  return {
    ...gateway,
    url: `http://127.0.0.1:${port}`,
    ws: `ws://127.0.0.1:${port}`,
  };
}

test("signed incoming call returns bidirectional TwiML and public health works", async (t) => {
  const gateway = await start();
  t.after(() => gateway.close());
  assert.deepEqual(await (await fetch(`${gateway.url}/health`)).json(), {
    status: "ok",
  });
  const params = {
    CallSid: callSid,
    AccountSid: config.accountSid,
    FutureTwilioField: "kept for signature validation",
  };
  const signature = twilio.getExpectedTwilioSignature(
    config.authToken,
    `${config.publicBaseUrl}/twilio/incoming`,
    params,
  );
  const response = await fetch(`${gateway.url}/twilio/incoming`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-Twilio-Signature": signature,
    },
    body: new URLSearchParams(params),
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /text\/xml/);
  assert.match(
    await response.text(),
    /<Connect><Stream url="wss:\/\/example.test\/twilio\/media"\/><\/Connect>/,
  );
});

test("unsigned calls, wrong accounts, query paths, and malformed forms fail closed", async (t) => {
  const gateway = await start();
  t.after(() => gateway.close());
  const params = { CallSid: callSid, AccountSid: `AC${"c".repeat(32)}` };
  const signedWrongAccount = twilio.getExpectedTwilioSignature(
    config.authToken,
    `${config.publicBaseUrl}/twilio/incoming`,
    params,
  );
  for (const signature of ["", signedWrongAccount]) {
    const response = await fetch(`${gateway.url}/twilio/incoming`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Twilio-Signature": signature,
      },
      body: new URLSearchParams(params),
    });
    assert.equal(response.status, 403);
  }
  assert.equal(
    (
      await fetch(`${gateway.url}/twilio/incoming?unexpected=1`, {
        method: "POST",
      })
    ).status,
    404,
  );
  assert.equal(
    (
      await fetch(`${gateway.url}/twilio/incoming`, {
        method: "POST",
        body: "not a form",
      })
    ).status,
    400,
  );
});

test("WebSocket handshake authenticates and malformed stream events close only that socket", async (t) => {
  const gateway = await start();
  t.after(() => gateway.close());
  const bad = new WebSocket(`${gateway.ws}/twilio/media`);
  const rejected = await new Promise<number>((resolve, reject) => {
    bad.on("unexpected-response", (_request, response) => {
      response.resume();
      resolve(response.statusCode ?? 0);
      bad.terminate();
    });
    bad.on("open", () => reject(new Error("Unauthenticated socket accepted")));
    bad.on("error", () => {});
  });
  assert.equal(rejected, 403);
  const signature = twilio.getExpectedTwilioSignature(
    config.authToken,
    `${config.publicBaseUrl}/twilio/media`,
    {},
  );
  const socket = new WebSocket(`${gateway.ws}/twilio/media`, {
    headers: { "X-Twilio-Signature": signature },
  });
  await once(socket, "open");
  const closed = once(socket, "close");
  socket.send("malformed JSON");
  assert.equal((await closed)[0], 1008);
  assert.equal((await fetch(`${gateway.url}/health`)).status, 200);
});

test("configured WebSocket URL and documented trailing slash signature variants validate", () => {
  for (const url of [
    "https://example.test/twilio/media",
    "https://example.test/twilio/media/",
    "wss://example.test/twilio/media",
    "wss://example.test/twilio/media/",
  ]) {
    const signature = twilio.getExpectedTwilioSignature(
      config.authToken,
      url,
      {},
    );
    assert.equal(
      validSignature(
        config.authToken,
        signature,
        "https://example.test/twilio/media",
        {},
        true,
      ),
      true,
    );
    assert.equal(
      validSignature(
        config.authToken,
        signature,
        "https://other.test/twilio/media",
        {},
        true,
      ),
      false,
    );
  }
});

test("Twilio parsing validates format and base64 while tolerating new unrelated fields", () => {
  assert.deepEqual(
    twilioAdapter.parse(
      JSON.stringify({
        event: "connected",
        protocol: "Call",
        version: "1.0.0",
        future: true,
      }),
    ),
    { type: "connected" },
  );
  const streamSid = `MZ${"c".repeat(32)}`;
  const media = {
    event: "media",
    sequenceNumber: "2",
    streamSid,
    media: { track: "inbound", chunk: "1", timestamp: "0", payload: "//8=" },
  };
  assert.deepEqual(twilioAdapter.parse(JSON.stringify(media)), {
    type: "audio",
    streamId: streamSid,
    audio: Buffer.from([255, 255]),
  });
  assert.throws(() =>
    twilioAdapter.parse(
      JSON.stringify({
        ...media,
        media: { ...media.media, payload: "invalid!" },
      }),
    ),
  );
  assert.throws(() =>
    twilioAdapter.parse(
      JSON.stringify({
        ...media,
        media: { ...media.media, track: "outbound" },
      }),
    ),
  );
});
