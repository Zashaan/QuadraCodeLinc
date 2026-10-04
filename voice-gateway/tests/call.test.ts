import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { WebSocket } from "ws";
import {
  type AbeBackend,
  BackendError,
  type BackendSession,
} from "../src/backend.js";
import { VoiceCall } from "../src/call.js";
import type { NovaCallbacks, NovaFactory } from "../src/nova/types.js";
import { twilioAdapter } from "../src/telephony.js";

const accountSid = `AC${"a".repeat(32)}`;
const callSid = `CA${"b".repeat(32)}`;
const streamSid = `MZ${"c".repeat(32)}`;
const session: BackendSession = {
  session_id: "f032dbb7-69b2-4ab5-b0f0-b8b81ed1be08",
  system_prompt: "Abe",
  tools: [],
};
const start = {
  event: "start",
  sequenceNumber: "1",
  streamSid,
  start: {
    accountSid,
    callSid,
    streamSid,
    tracks: ["inbound"],
    mediaFormat: { encoding: "audio/x-mulaw", sampleRate: 8000, channels: 1 },
  },
};
const media = {
  event: "media",
  sequenceNumber: "2",
  streamSid,
  media: { track: "inbound", chunk: "1", timestamp: "0", payload: "//8=" },
};

class Socket extends EventEmitter {
  readyState: number = WebSocket.OPEN;
  bufferedAmount = 0;
  sent: string[] = [];
  closeCode = 0;
  send(value: string, callback: (error?: Error) => void) {
    this.sent.push(value);
    callback();
  }
  close(code: number) {
    this.closeCode = code;
    this.readyState = WebSocket.CLOSED;
    queueMicrotask(() => this.emit("close"));
  }
  terminate() {
    this.readyState = WebSocket.CLOSED;
  }
  ping() {}
  message(value: unknown) {
    this.emit("message", Buffer.from(JSON.stringify(value)), false);
  }
}

function setup(
  overrides: Partial<AbeBackend> = {},
  startNova: () => Promise<void> = async () => {},
) {
  const socket = new Socket();
  const received: Buffer[] = [];
  let callbacks: NovaCallbacks | undefined;
  let deleted = 0;
  let novaClosed = 0;
  const backend: AbeBackend = {
    createSession: async () => session,
    invokeTool: async () => ({
      status: "success",
      member: { annual_maximum_remaining: 800 },
    }),
    deleteSession: async () => {
      deleted += 1;
    },
    ...overrides,
  };
  const factory: NovaFactory = {
    create(value) {
      callbacks = value;
      return {
        start: startNova,
        sendAudio: (pcm) => received.push(pcm),
        close: async () => {
          novaClosed += 1;
          value.onClose();
        },
      };
    },
  };
  const call = new VoiceCall({
    socket: socket as unknown as WebSocket,
    telephony: twilioAdapter,
    backend,
    novaFactory: factory,
    accountSid,
    log: () => {},
  });
  return {
    socket,
    call,
    received,
    get callbacks() {
      assert.ok(callbacks);
      return callbacks;
    },
    get deleted() {
      return deleted;
    },
    get novaClosed() {
      return novaClosed;
    },
  };
}

test("call buffers startup audio, forwards tool result, converts audio and clears on interruption", async () => {
  const state = setup();
  state.socket.message(start);
  state.socket.message(media);
  await setImmediate();
  assert.deepEqual(state.received, [Buffer.alloc(4)]);
  const tool = await state.callbacks.onToolUse({
    toolName: "get_member",
    toolUseId: "t1",
    input: { member_id: "DEMO001" },
  });
  assert.deepEqual(tool, {
    status: "success",
    member: { annual_maximum_remaining: 800 },
  });
  state.callbacks.onAudio(Buffer.alloc(4));
  state.callbacks.onInterrupted();
  assert.deepEqual(
    state.socket.sent.map((message) => JSON.parse(message)),
    [
      { event: "media", streamSid, media: { payload: "//8=" } },
      { event: "clear", streamSid },
    ],
  );
  await state.call.close();
  await state.call.close();
  assert.equal(state.deleted, 1);
  assert.equal(state.novaClosed, 1);
});

test("disconnect during backend setup removes a late-created session without opening Nova", async () => {
  let resolveSession: (value: BackendSession) => void = () => {};
  const state = setup({
    createSession: () =>
      new Promise((resolve) => {
        resolveSession = resolve;
      }),
  });
  state.socket.message(start);
  const closed = state.call.close();
  resolveSession(session);
  await closed;
  assert.equal(state.deleted, 1);
  assert.equal(state.novaClosed, 0);
});

test("disconnect during Nova startup closes the upstream once and removes backend session", async () => {
  let finishStart: () => void = () => {};
  const state = setup(
    {},
    () =>
      new Promise((resolve) => {
        finishStart = resolve;
      }),
  );
  state.socket.message(start);
  await setImmediate();
  const closed = state.call.close();
  finishStart();
  await closed;
  assert.equal(state.deleted, 1);
  assert.equal(state.novaClosed, 1);
});

test("upstream failure closes only this call and cleans up its session", async () => {
  const state = setup();
  state.socket.message(start);
  await setImmediate();
  state.callbacks.onError(new Error("secret exception text"));
  await state.call.close();
  assert.equal(state.novaClosed, 1);
  assert.equal(state.deleted, 1);
  assert.equal(state.socket.readyState, WebSocket.CLOSED);
});

test("malformed, mismatched and pre-start events are rejected without opening upstream", async () => {
  for (const message of [
    { event: "bogus" },
    media,
    { ...start, start: { ...start.start, accountSid: `AC${"d".repeat(32)}` } },
  ]) {
    const state = setup();
    state.socket.message(message);
    await state.call.close();
    assert.equal(state.socket.closeCode, 1008);
    assert.equal(state.novaClosed, 0);
    assert.equal(state.deleted, 0);
  }
});

test("backend tool failures return safe structured errors without inventing a balance", async () => {
  for (const [code, expected] of [
    ["invalid_request", "invalid_tool_input"],
    ["unavailable", "backend_unavailable"],
  ] as const) {
    const state = setup({
      invokeTool: async () => {
        throw new BackendError(code);
      },
    });
    state.socket.message(start);
    await setImmediate();
    const result = await state.callbacks.onToolUse({
      toolName: "get_member",
      toolUseId: "t1",
      input: {},
    });
    assert.equal((result as { code: string }).code, expected);
    assert.ok(!JSON.stringify(result).includes("800"));
    await state.call.close();
  }
});

test("startup buffer overflow fails safely and bounded resources are released", async () => {
  let resolveSession: (value: BackendSession) => void = () => {};
  const state = setup({
    createSession: () =>
      new Promise((resolve) => {
        resolveSession = resolve;
      }),
  });
  state.socket.message(start);
  const large = {
    ...media,
    media: { ...media.media, payload: Buffer.alloc(6000).toString("base64") },
  };
  for (let i = 0; i < 8; i += 1) state.socket.message(large);
  resolveSession(session);
  await state.call.close();
  assert.equal(state.socket.closeCode, 1008);
  assert.equal(state.deleted, 1);
});

test("barge-in preserves incoming audio, follow-up tool use and exactly-once hangup cleanup", async () => {
  const state = setup();
  state.socket.message(start);
  await setImmediate();
  state.callbacks.onAudio(Buffer.alloc(4));
  state.callbacks.onInterrupted();
  state.socket.message(media);
  const result = await state.callbacks.onToolUse({
    toolName: "get_member",
    toolUseId: "follow-up",
    input: { member_id: "DEMO001" },
  });
  state.callbacks.onAudio(Buffer.from([1, 0]));
  assert.equal(state.socket.readyState, WebSocket.OPEN);
  assert.equal(state.deleted, 0);
  assert.equal(state.novaClosed, 0);
  assert.equal(state.received.length, 1);
  assert.equal((result as { status: string }).status, "success");
  assert.deepEqual(
    state.socket.sent.map((value) => JSON.parse(value).event),
    ["media", "clear", "media"],
  );
  state.socket.emit("close");
  await state.call.close();
  assert.equal(state.deleted, 1);
  assert.equal(state.novaClosed, 1);
});

test("transcript saving does not block audio and finishes before session deletion", async () => {
  let finishSave: () => void = () => {};
  const order: string[] = [];
  const state = setup({
    appendTranscripts: async (_id, events) => {
      assert.equal(events[0]?.text, "Hello Abe");
      await new Promise<void>((resolve) => {
        finishSave = resolve;
      });
      order.push("saved");
    },
    deleteSession: async () => {
      order.push("deleted");
    },
  });
  state.socket.message(start);
  await setImmediate();
  state.callbacks.onTranscript?.({
    event_id: "text-1",
    role: "user",
    text: "Hello Abe",
  });
  state.callbacks.onAudio(Buffer.alloc(4));
  assert.equal(state.socket.sent.length, 1);
  const closed = state.call.close();
  finishSave();
  await closed;
  assert.deepEqual(order, ["saved", "deleted"]);
});

test("failed transcript writes mark the call partial without interrupting it", async () => {
  const partial: boolean[] = [];
  const state = setup({
    appendTranscripts: async (_id, _events, incomplete) => {
      if (!incomplete) throw new Error("Storage unavailable");
      partial.push(incomplete);
    },
  });
  state.socket.message(start);
  await setImmediate();
  state.callbacks.onTranscript?.({
    event_id: "text-1",
    role: "user",
    text: "Hello Abe",
  });
  await setImmediate();
  assert.equal(state.socket.readyState, WebSocket.OPEN);
  await state.call.close();
  assert.deepEqual(partial, [true]);
  assert.equal(state.deleted, 1);
});
