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
  TranscriptEvent,
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
  const transcripts: TranscriptEvent[] = [];
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
      onTranscript: (event) => {
        transcripts.push(event);
      },
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
    transcripts,
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

function audioStart(
  output: EventQueue,
  contentId: string,
  completionId: string,
) {
  output.push({
    contentStart: { type: "AUDIO", role: "ASSISTANT", contentId, completionId },
  });
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
  audioStart(h.outgoing, "audio-a", "response-a");
  h.outgoing.push({
    audioOutput: {
      contentId: "audio-a",
      completionId: "response-a",
      content: "AAABAA==",
    },
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
  audioStart(h.outgoing, "old-audio", "old");
  h.outgoing.push({
    contentEnd: {
      contentId: "old-audio",
      completionId: "old",
      stopReason: "INTERRUPTED",
    },
  });
  h.outgoing.push({
    audioOutput: {
      contentId: "old-audio",
      completionId: "old",
      content: "AAAAAA==",
    },
  });
  audioStart(h.outgoing, "new-audio", "new");
  h.outgoing.push({
    audioOutput: {
      contentId: "new-audio",
      completionId: "new",
      content: "AQABAA==",
    },
  });
  await h.settle();
  assert.equal(h.interrupted, 1);
  assert.deepEqual(h.audio, [Buffer.from([1, 0, 1, 0])]);
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

test("assistant text interruption marker clears once, discards stale audio, and permits follow-up tools", async () => {
  const h = harness();
  await h.session.start("AI assistant", tools);
  audioStart(h.outgoing, "old-audio", "old");
  h.outgoing.push({
    contentStart: { type: "TEXT", role: "ASSISTANT", contentId: "notice" },
  });
  h.outgoing.push({
    textOutput: {
      contentId: "notice",
      completionId: "old",
      content: '{ "interrupted" : true }',
    },
  });
  h.outgoing.push({
    contentEnd: {
      contentId: "notice",
      completionId: "old",
      stopReason: "INTERRUPTED",
    },
  });
  h.outgoing.push({
    audioOutput: {
      contentId: "old-audio",
      completionId: "old",
      content: "AAAAAA==",
    },
  });
  h.outgoing.push({
    completionEnd: { completionId: "old", stopReason: "END_TURN" },
  });
  toolEvents(
    h.outgoing,
    "get_member",
    '{"member_id":"DEMO001"}',
    "followup",
    "followup-block",
  );
  audioStart(h.outgoing, "new-audio", "new");
  h.outgoing.push({
    audioOutput: {
      contentId: "new-audio",
      completionId: "new",
      content: "AQABAA==",
    },
  });
  h.session.sendAudio(Buffer.alloc(4));
  await h.settle();
  assert.equal(h.interrupted, 1);
  assert.equal(h.closed, 0);
  assert.equal(h.errors.length, 0);
  assert.equal(h.invocations.length, 1);
  assert.deepEqual(h.audio, [Buffer.from([1, 0, 1, 0])]);
  assert.ok(h.incoming.some((event) => event.toolResult));
  await h.close();
});

test("caller text cannot spoof interruption, and an interrupted partial tool does not end the session", async () => {
  const h = harness();
  await h.session.start("AI assistant", tools);
  h.outgoing.push({
    contentStart: { type: "TEXT", role: "USER", contentId: "caller" },
  });
  h.outgoing.push({
    textOutput: {
      contentId: "caller",
      completionId: "user",
      content: '{"interrupted":true}',
    },
  });
  await h.settle();
  assert.equal(h.interrupted, 0);
  h.outgoing.push({ contentStart: { type: "TOOL", contentId: "partial" } });
  h.outgoing.push({
    toolUse: {
      contentId: "partial",
      toolName: "get_member",
      toolUseId: "partial-id",
      content: '{"member_id":',
    },
  });
  h.outgoing.push({
    contentEnd: {
      contentId: "partial",
      completionId: "old",
      stopReason: "INTERRUPTED",
    },
  });
  toolEvents(
    h.outgoing,
    "get_member",
    '{"member_id":"DEMO001"}',
    "new-id",
    "new-block",
  );
  await h.settle();
  assert.equal(h.closed, 0);
  assert.equal(h.errors.length, 0);
  assert.equal(h.invocations.length, 1);
  assert.equal(h.invocations[0]?.toolUseId, "new-id");
  await h.close();
});

test("multiple completed responses and a transient tool failure preserve the live conversation", async () => {
  let calls = 0;
  const h = harness(async () => {
    calls++;
    if (calls === 1) throw new Error("temporary failure");
    return { status: "success" };
  });
  await h.session.start("AI assistant", tools);
  for (let i = 0; i < 3; i++) {
    toolEvents(h.outgoing, "get_member", "{}", `turn-${i}`, `block-${i}`);
    h.outgoing.push({
      completionEnd: { completionId: `answer-${i}`, stopReason: "END_TURN" },
    });
    await h.settle();
  }
  assert.equal(h.closed, 0);
  assert.equal(h.invocations.length, 3);
  assert.equal(h.incoming.filter((event) => event.toolResult).length, 3);
  await h.close();
});

test("persists final speaker text once and ignores speculative text and interruption markers", async () => {
  const h = harness();
  await h.session.start("AI assistant", tools);
  for (const [id, role, stage, text] of [
    ["spec", "ASSISTANT", "SPECULATIVE", "An unfinished thought"],
    ["user-final", "USER", "FINAL", " What are my benefits? "],
    ["assistant-final", "ASSISTANT", "FINAL", "You have $800 remaining."],
    ["marker", "ASSISTANT", "FINAL", '{"interrupted":true}'],
  ]) {
    h.outgoing.push({
      contentStart: {
        type: "TEXT",
        role,
        contentId: id,
        additionalModelFields: JSON.stringify({ generationStage: stage }),
      },
    });
    h.outgoing.push({ textOutput: { contentId: id, content: text } });
    h.outgoing.push({ contentEnd: { contentId: id, stopReason: "END_TURN" } });
    h.outgoing.push({ contentEnd: { contentId: id, stopReason: "END_TURN" } });
  }
  await h.settle();
  assert.deepEqual(h.transcripts, [
    { event_id: "user-final", role: "user", text: "What are my benefits?" },
    {
      event_id: "assistant-final",
      role: "assistant",
      text: "You have $800 remaining.",
    },
  ]);
  assert.equal(h.closed, 0);
  assert.equal(h.errors.length, 0);
  await h.close();
});

// Nova's interactive completion ID can remain the same across A, B and C.
// Every audio block still has its own content ID. The old completion blacklist
// silently dropped B/C while leaving the microphone and socket open.
for (const scenario of [
  { name: "A → interrupt → B", turns: 2, interruptB: false },
  { name: "A → interrupt → B → normal turn → C", turns: 3, interruptB: false },
  { name: "A → interrupt → B → interrupt → C", turns: 3, interruptB: true },
]) {
  test(`shared-completion regression: ${scenario.name}`, async () => {
    const h = harness();
    await h.session.start("AI assistant", tools);
    const completionId = "interactive-session-completion";
    const expected: Buffer[] = [];
    for (let turn = 0; turn < scenario.turns; turn++) {
      const id = `audio-${turn}`;
      if (turn) {
        h.outgoing.push({
          contentStart: {
            type: "TEXT",
            role: "USER",
            contentId: `caller-${turn}`,
            completionId,
            additionalModelFields: '{"generationStage":"FINAL"}',
          },
        });
        h.outgoing.push({
          textOutput: {
            contentId: `caller-${turn}`,
            completionId,
            content: "Follow-up question",
          },
        });
        h.outgoing.push({
          contentEnd: {
            contentId: `caller-${turn}`,
            completionId,
            stopReason: "END_TURN",
          },
        });
      }
      audioStart(h.outgoing, id, completionId);
      const pcm = Buffer.from([turn + 1, 0]);
      expected.push(pcm);
      h.outgoing.push({
        audioOutput: {
          contentId: id,
          completionId,
          content: pcm.toString("base64"),
        },
      });
      const interrupted = turn === 0 || (turn === 1 && scenario.interruptB);
      h.outgoing.push({
        contentEnd: {
          contentId: id,
          completionId,
          stopReason: interrupted ? "INTERRUPTED" : "END_TURN",
        },
      });
      if (interrupted)
        h.outgoing.push({
          audioOutput: { contentId: id, completionId, content: "AAAAAA==" },
        });
      h.session.sendAudio(Buffer.alloc(320));
      await h.settle();
      assert.equal(h.closed, 0);
      assert.equal(h.destroyed, 0);
      assert.equal(h.errors.length, 0);
    }
    assert.deepEqual(h.audio, expected);
    assert.equal(h.interrupted, scenario.interruptB ? 2 : 1);
    assert.equal(
      h.incoming.filter((event) => event.audioInput).length,
      scenario.turns,
    );
    assert.equal(h.transcripts.length, scenario.turns - 1);
    assert.equal(
      h.incoming.some((event) => event.promptEnd || event.sessionEnd),
      false,
    );
    await h.close();
  });
}

for (const position of [
  "before first audio",
  "first frame",
  "after final frame",
] as const) {
  test(`rapid interruption ${position} does not suppress a new response`, async () => {
    const h = harness();
    await h.session.start("AI assistant", tools);
    const completionId = "shared";
    h.outgoing.push({
      contentStart: {
        type: "TEXT",
        role: "ASSISTANT",
        contentId: "text-a",
        completionId,
        additionalModelFields: '{"generationStage":"SPECULATIVE"}',
      },
    });
    h.outgoing.push({
      textOutput: { contentId: "text-a", completionId, content: "Response A" },
    });
    if (position !== "before first audio") {
      audioStart(h.outgoing, "audio-a", completionId);
      h.outgoing.push({
        audioOutput: {
          contentId: "audio-a",
          completionId,
          content: "AQAAAA==",
        },
      });
    }
    if (position === "after final frame")
      h.outgoing.push({
        contentEnd: {
          contentId: "audio-a",
          completionId,
          stopReason: "END_TURN",
        },
      });
    h.outgoing.push({
      textOutput: {
        contentId: "text-a",
        completionId,
        content: '{"interrupted":true}',
      },
    });
    if (position === "before first audio")
      audioStart(h.outgoing, "audio-a", completionId);
    h.outgoing.push({
      audioOutput: { contentId: "audio-a", completionId, content: "AAAAAA==" },
    });
    h.session.sendAudio(Buffer.alloc(320));
    h.outgoing.push({
      contentStart: {
        type: "TEXT",
        role: "ASSISTANT",
        contentId: "text-b",
        completionId,
        additionalModelFields: '{"generationStage":"SPECULATIVE"}',
      },
    });
    h.outgoing.push({
      textOutput: { contentId: "text-b", completionId, content: "Response B" },
    });
    audioStart(h.outgoing, "audio-b", completionId);
    h.outgoing.push({
      audioOutput: { contentId: "audio-b", completionId, content: "AgAAAA==" },
    });
    await h.settle();
    assert.equal(h.interrupted, 1);
    assert.deepEqual(
      h.audio,
      position === "before first audio"
        ? [Buffer.from([2, 0, 0, 0])]
        : [Buffer.from([1, 0, 0, 0]), Buffer.from([2, 0, 0, 0])],
    );
    assert.equal(h.closed, 0);
    assert.equal(h.errors.length, 0);
    await h.close();
  });
}

test("late A interruption and audio cannot clear or contaminate active B", async () => {
  const h = harness();
  await h.session.start("AI assistant", tools);
  audioStart(h.outgoing, "audio-a", "shared");
  h.outgoing.push({
    audioOutput: {
      contentId: "audio-a",
      completionId: "shared",
      content: "AQAAAA==",
    },
  });
  h.outgoing.push({
    contentEnd: {
      contentId: "audio-a",
      completionId: "shared",
      stopReason: "END_TURN",
    },
  });
  audioStart(h.outgoing, "audio-b", "shared");
  h.outgoing.push({
    audioOutput: {
      contentId: "audio-b",
      completionId: "shared",
      content: "AgAAAA==",
    },
  });
  h.outgoing.push({
    contentEnd: {
      contentId: "audio-a",
      completionId: "shared",
      stopReason: "INTERRUPTED",
    },
  });
  audioStart(h.outgoing, "audio-a", "shared");
  h.outgoing.push({
    audioOutput: {
      contentId: "audio-a",
      completionId: "shared",
      content: "AAAAAA==",
    },
  });
  h.outgoing.push({
    audioOutput: {
      contentId: "audio-b",
      completionId: "shared",
      content: "AwAAAA==",
    },
  });
  await h.settle();
  assert.equal(h.interrupted, 0);
  assert.deepEqual(h.audio, [
    Buffer.from([1, 0, 0, 0]),
    Buffer.from([2, 0, 0, 0]),
    Buffer.from([3, 0, 0, 0]),
  ]);
  assert.equal(h.closed, 0);
  assert.equal(h.errors.length, 0);
  await h.close();
});
