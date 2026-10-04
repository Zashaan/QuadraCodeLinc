import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { EventQueue } from "../src/nova/queue.js";
import {
  type NovaTransport,
  StreamingNovaSession,
} from "../src/nova/session.js";
import type {
  NovaCallbacks,
  ToolDefinition,
  ToolInvocation,
} from "../src/nova/types.js";

const tools: ToolDefinition[] = [
  {
    name: "get_member",
    description: "Look up a demo member",
    input_schema: {
      type: "object",
      properties: { member_id: { type: "string" } },
      required: ["member_id"],
    },
  },
];

// A scripted byte transport tests event handling only. It does not emulate Nova inference.
function harness(
  toolHandler: NovaCallbacks["onToolUse"] = async () => ({ status: "success" }),
) {
  const incoming: Record<string, unknown>[] = [];
  const outgoing = new EventQueue();
  const audio: Buffer[] = [];
  const invocations: ToolInvocation[] = [];
  const errors: Error[] = [];
  let interrupted = 0;
  let closed = 0;
  let destroyed = 0;
  let inputDone = Promise.resolve();
  const transport: NovaTransport = {
    async open(input, signal) {
      signal.addEventListener("abort", () => outgoing.end(true), {
        once: true,
      });
      inputDone = (async () => {
        for await (const bytes of input) {
          const event = JSON.parse(Buffer.from(bytes).toString("utf8")).event;
          incoming.push(event);
          if (event.sessionEnd) outgoing.end();
        }
      })();
      return outgoing;
    },
    destroy() {
      destroyed += 1;
    },
  };
  const session = new StreamingNovaSession(
    "matthew",
    {
      onAudio: (pcm) => {
        audio.push(pcm);
      },
      onInterrupted: () => {
        interrupted += 1;
      },
      onToolUse: async (invocation) => {
        invocations.push(invocation);
        return toolHandler(invocation);
      },
      onError: (error) => {
        errors.push(error);
      },
      onClose: () => {
        closed += 1;
      },
    },
    transport,
  );
  return {
    session,
    incoming,
    outgoing,
    audio,
    invocations,
    errors,
    get interrupted() {
      return interrupted;
    },
    get closed() {
      return closed;
    },
    get destroyed() {
      return destroyed;
    },
    async settle() {
      await setImmediate();
      await setImmediate();
    },
    async close() {
      await session.close();
      await inputDone;
    },
  };
}

function toolEvents(
  output: EventQueue,
  name: string,
  json: string,
  id = "call-1",
  block = "block-1",
) {
  output.push({ contentStart: { type: "TOOL", contentId: block } });
  output.push({
    toolUse: { contentId: block, toolUseId: id, toolName: name, content: json },
  });
  output.push({ contentEnd: { contentId: block, stopReason: "TOOL_USE" } });
}

test("starts a real protocol session with 8 kHz PCM, tools, and a separate speak-first turn", async () => {
  const h = harness();
  await h.session.start("You are an AI assistant.", tools);
  await h.settle();
  const [
    sessionStart,
    promptStart,
    systemStart,
    systemText,
    systemEnd,
    audioStart,
    greetingStart,
    greetingText,
    greetingEnd,
  ] = h.incoming;
  assert.ok(sessionStart?.sessionStart);
  const prompt = promptStart?.promptStart as Record<string, unknown>;
  assert.equal(
    (prompt.audioOutputConfiguration as Record<string, unknown>)
      .sampleRateHertz,
    8000,
  );
  const advertised = prompt.toolConfiguration as {
    tools: { toolSpec: { inputSchema: { json: string } } }[];
  };
  assert.deepEqual(
    JSON.parse(advertised.tools[0]?.toolSpec.inputSchema.json ?? "null"),
    tools[0]?.input_schema,
  );
  const system = systemStart?.contentStart as Record<string, unknown>;
  assert.equal(system.role, "SYSTEM");
  assert.equal(system.interactive, false);
  assert.ok(systemText && systemEnd && greetingText && greetingEnd);
  assert.equal(
    (systemText.textInput as Record<string, unknown>).contentName,
    system.contentName,
  );
  assert.equal(
    (systemEnd.contentEnd as Record<string, unknown>).contentName,
    system.contentName,
  );
  const audio = audioStart?.contentStart as Record<string, unknown>;
  assert.equal(
    (audio.audioInputConfiguration as Record<string, unknown>).sampleRateHertz,
    8000,
  );
  const greeting = greetingStart?.contentStart as Record<string, unknown>;
  assert.equal(greeting.role, "USER");
  assert.equal(greeting.interactive, true);
  assert.notEqual(greeting.contentName, system.contentName);
  assert.equal(
    (greetingText.textInput as Record<string, unknown>).contentName,
    greeting.contentName,
  );
  assert.equal(
    (greetingEnd.contentEnd as Record<string, unknown>).contentName,
    greeting.contentName,
  );
  h.session.sendAudio(Buffer.from([0, 0, 1, 0]));
  await h.settle();
  const frame = h.incoming.at(-1)?.audioInput as Record<string, unknown>;
  assert.equal(frame.contentName, audio.contentName);
  assert.equal(frame.content, "AAABAA==");
  await h.close();
  assert.deepEqual(
    h.incoming.slice(-3).map((event) => Object.keys(event)[0]),
    ["contentEnd", "promptEnd", "sessionEnd"],
  );
  await h.session.close();
  assert.equal(h.closed, 1);
  assert.equal(h.destroyed, 1);
});

test("waits for complete tool blocks, correlates results, and leaves audio streaming responsive", async () => {
  let resolveTool: ((value: unknown) => void) | undefined;
  const h = harness(
    () =>
      new Promise((resolve) => {
        resolveTool = resolve;
      }),
  );
  await h.session.start("AI assistant", tools);
  h.outgoing.push({ contentStart: { type: "TOOL", contentId: "block-a" } });
  h.outgoing.push({
    toolUse: {
      contentId: "block-a",
      toolUseId: "call-a",
      toolName: "get_member",
      content: '{"member_id":',
    },
  });
  await h.settle();
  assert.equal(h.invocations.length, 0);
  h.outgoing.push({
    toolUse: {
      contentId: "block-a",
      toolUseId: "call-a",
      toolName: "get_member",
      content: '"DEMO001"}',
    },
  });
  h.outgoing.push({
    contentEnd: { contentId: "block-a", stopReason: "TOOL_USE" },
  });
  h.outgoing.push({
    audioOutput: { completionId: "response-a", content: "AAABAA==" },
  });
  await h.settle();
  assert.deepEqual(h.invocations, [
    {
      toolName: "get_member",
      toolUseId: "call-a",
      input: { member_id: "DEMO001" },
    },
  ]);
  assert.deepEqual(h.audio, [Buffer.from([0, 0, 1, 0])]);
  assert.equal(
    h.incoming.some((event) => event.toolResult),
    false,
  );
  resolveTool?.({ status: "success", member: { member_id: "DEMO001" } });
  await h.settle();
  const resultIndex = h.incoming.findIndex((event) => event.toolResult);
  const start = h.incoming[resultIndex - 1]?.contentStart as Record<
    string,
    unknown
  >;
  const result = h.incoming[resultIndex]?.toolResult as Record<string, unknown>;
  const end = h.incoming[resultIndex + 1]?.contentEnd as Record<
    string,
    unknown
  >;
  assert.equal(
    (start.toolResultInputConfiguration as Record<string, unknown>).toolUseId,
    "call-a",
  );
  assert.equal(start.contentName, result.contentName);
  assert.equal(result.contentName, end.contentName);
  assert.deepEqual(JSON.parse(String(result.content)), {
    status: "success",
    member: { member_id: "DEMO001" },
  });
  await h.close();
});

test("rejects unknown tools and malformed JSON without dispatching; sanitizes backend errors", async () => {
  const h = harness(async () => {
    throw new Error("sensitive backend content");
  });
  await h.session.start("AI assistant", tools);
  toolEvents(h.outgoing, "arbitrary_shell", "{}", "id-1", "b-1");
  toolEvents(h.outgoing, "get_member", "{broken", "id-2", "b-2");
  toolEvents(
    h.outgoing,
    "get_member",
    '{"member_id":"DEMO001"}',
    "id-3",
    "b-3",
  );
  await h.settle();
  assert.equal(h.invocations.length, 1);
  const results = h.incoming
    .filter((event) => event.toolResult)
    .map((event) =>
      JSON.parse(String((event.toolResult as Record<string, unknown>).content)),
    );
  assert.deepEqual(results.map((result) => result.error.code).sort(), [
    "invalid_arguments",
    "tool_unavailable",
    "unknown_tool",
  ]);
  assert.equal(
    JSON.stringify(h.incoming).includes("sensitive backend content"),
    false,
  );
  await h.close();
});

test("interruptions notify playback and discard late audio from the interrupted completion", async () => {
  const h = harness();
  await h.session.start("AI assistant", tools);
  h.outgoing.push({
    contentEnd: {
      contentId: "old-text",
      completionId: "old",
      stopReason: "INTERRUPTED",
    },
  });
  h.outgoing.push({
    audioOutput: { completionId: "old", content: "AAAAAA==" },
  });
  h.outgoing.push({
    audioOutput: { completionId: "new", content: "AQABAA==" },
  });
  toolEvents(
    h.outgoing,
    "get_member",
    '{"member_id":"DEMO001"}',
    "after-interruption",
    "after-interruption-block",
  );
  await h.settle();
  assert.equal(h.interrupted, 1);
  assert.deepEqual(h.audio, [Buffer.from([1, 0, 1, 0])]);
  assert.equal(h.invocations.at(-1)?.toolUseId, "after-interruption");
  assert.equal(h.closed, 0);
  await h.close();
});

test("closing during an unfinished tool call cancels its wait and prevents late result delivery", async () => {
  let resolveTool: ((value: unknown) => void) | undefined;
  const h = harness(
    () =>
      new Promise((resolve) => {
        resolveTool = resolve;
      }),
  );
  await h.session.start("AI assistant", tools);
  toolEvents(h.outgoing, "get_member", "{}");
  await h.settle();
  assert.equal(h.invocations.length, 1);
  await h.close();
  resolveTool?.({ status: "success" });
  await h.settle();
  assert.equal(
    h.incoming.some((event) => event.toolResult),
    false,
  );
  assert.equal(h.errors.length, 0);
  assert.equal(h.closed, 1);
});

test("invalid audio and duplicate tool IDs fail closed with one lifecycle notification", async () => {
  const h = harness();
  await h.session.start("AI assistant", tools);
  toolEvents(h.outgoing, "get_member", "{}", "same", "a");
  await h.settle();
  toolEvents(h.outgoing, "get_member", "{}", "same", "b");
  await h.settle();
  assert.equal(h.invocations.length, 1);
  assert.equal(h.closed, 1);
  assert.equal(h.errors.length, 1);
  await h.close();
  const badAudio = harness();
  await badAudio.session.start("AI assistant", tools);
  badAudio.session.sendAudio(Buffer.from([1]));
  assert.equal(badAudio.closed, 1);
  assert.equal(badAudio.errors.length, 1);
  await badAudio.close();
});

test("queue limits bytes and item count, then unblocks consumers on teardown", async () => {
  const small = new EventQueue(30, 2);
  assert.throws(() => small.push({ content: "a".repeat(100) }), /buffer limit/);
  const queue = new EventQueue(1000, 1);
  queue.push({ a: 1 });
  assert.throws(() => queue.push({ b: 2 }), /buffer limit/);
  const iterator = queue[Symbol.asyncIterator]();
  assert.equal((await iterator.next()).done, false);
  const waiting = iterator.next();
  queue.end(true);
  assert.equal((await waiting).done, true);
  assert.throws(() => queue.push({ c: 3 }), /closed/);
});

test("transport startup errors destroy resources without revealing service error text", async () => {
  const errors: Error[] = [];
  let closed = 0;
  let destroyed = 0;
  const session = new StreamingNovaSession(
    "matthew",
    {
      onAudio() {},
      onInterrupted() {},
      async onToolUse() {},
      onError: (error) => {
        errors.push(error);
      },
      onClose: () => {
        closed += 1;
      },
    },
    {
      async open() {
        throw new Error("sensitive service response");
      },
      destroy() {
        destroyed += 1;
      },
    },
  );
  await assert.rejects(session.start("AI assistant", tools), /Unable to start/);
  await session.close();
  assert.equal(errors.length, 1);
  assert.equal(errors[0]?.message.includes("sensitive"), false);
  assert.equal(closed, 1);
  assert.equal(destroyed, 1);
});
