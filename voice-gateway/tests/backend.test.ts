import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { BackendError, createBackendClient } from "../src/backend.js";

const sessionId = "10f5d636-bbaf-4a09-a51f-32dc823a9088";
const tool = {
  toolName: "get_member",
  toolUseId: "lookup-1",
  input: { member_id: "DEMO001" },
};
const member = JSON.parse(
  readFileSync(
    new URL("../../data/demo/members.json", import.meta.url),
    "utf8",
  ),
)[0];
const session = {
  session_id: sessionId,
  system_prompt: "You are Abe, an AI assistant.",
  tools: [
    {
      name: "get_member",
      description: "Look up a demo member",
      input_schema: {
        type: "object",
        properties: { member_id: { type: "string" } },
        required: ["member_id"],
      },
    },
  ],
};
const signal = () => new AbortController().signal;
const client = () =>
  createBackendClient("http://127.0.0.1:8000", "synthetic-internal-token");
const result = (value: unknown) => ({
  tool_name: "get_member",
  tool_call_id: tool.toolUseId,
  result: value,
});
const backendError = (code: BackendError["code"]) => (error: unknown) =>
  error instanceof BackendError && error.code === code;

test("session creation authenticates and sends only the expected request fields", async (t) => {
  const mock = t.mock.method(
    globalThis,
    "fetch",
    async (url: string | URL | Request, options?: RequestInit) => {
      assert.equal(url, "http://127.0.0.1:8000/sessions");
      assert.equal(options?.method, "POST");
      assert.equal(
        new Headers(options?.headers).get("Authorization"),
        "Bearer synthetic-internal-token",
      );
      assert.equal(
        new Headers(options?.headers).get("Content-Type"),
        "application/json",
      );
      assert.equal(options?.redirect, "error");
      assert.deepEqual(JSON.parse(String(options?.body)), {
        call_id: `CA${"1".repeat(32)}`,
      });
      return Response.json(session);
    },
  );
  assert.deepEqual(
    await client().createSession(`CA${"1".repeat(32)}`, signal()),
    session,
  );
  assert.equal(mock.mock.callCount(), 1);
});

test("tool invocation returns stored demo values and explicit member-not-found results", async (t) => {
  let response: unknown = { status: "success", member };
  t.mock.method(
    globalThis,
    "fetch",
    async (url: string | URL | Request, options?: RequestInit) => {
      assert.equal(url, `http://127.0.0.1:8000/sessions/${sessionId}/tools`);
      assert.deepEqual(JSON.parse(String(options?.body)), {
        tool_name: tool.toolName,
        tool_call_id: tool.toolUseId,
        arguments: tool.input,
      });
      return Response.json(result(response));
    },
  );
  assert.deepEqual(await client().invokeTool(sessionId, tool, signal()), {
    status: "success",
    member,
  });
  response = { status: "not_found", member_id: "UNKNOWN" };
  assert.deepEqual(
    await client().invokeTool(sessionId, tool, signal()),
    response,
  );
});

test("tool responses reject correlation mismatch and unexpected member data", async (t) => {
  const invalid = [
    { ...result({ status: "success", member }), tool_call_id: "another-call" },
    { ...result({ status: "success", member }), tool_name: "unknown_tool" },
    result({
      status: "success",
      member: { ...member, annual_maximum_remaining: "800" },
    }),
    result({
      status: "success",
      member: { ...member, coverage_guaranteed: true },
    }),
    result({ status: "success", member: { ...member, fsa_balance: -1 } }),
    result({ status: "unknown", member_id: "DEMO001" }),
    result({ status: "not_found", member_id: "DEMO001", member }),
  ];
  let response: unknown;
  t.mock.method(globalThis, "fetch", async () => Response.json(response));
  for (const value of invalid) {
    response = value;
    await assert.rejects(
      client().invokeTool(sessionId, tool, signal()),
      backendError("invalid_response"),
    );
  }
});

test("session response validation rejects unknown tools and malformed sessions", async (t) => {
  let response: unknown;
  t.mock.method(globalThis, "fetch", async () => Response.json(response));
  for (const value of [
    { ...session, session_id: "invalid" },
    { ...session, tools: [] },
    { ...session, tools: [{ ...session.tools[0], name: "run_shell" }] },
  ]) {
    response = value;
    await assert.rejects(
      client().createSession("call-id", signal()),
      backendError("invalid_response"),
    );
  }
});

test("422 and unavailable responses produce safe errors without server details", async (t) => {
  let status = 422;
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({ detail: "private server detail and token" }, { status }),
  );
  await assert.rejects(
    client().invokeTool(
      sessionId,
      { ...tool, toolName: "unknown_tool" },
      signal(),
    ),
    (error: unknown) => {
      assert.ok(error instanceof BackendError);
      assert.equal(error.code, "invalid_request");
      assert.equal(error.message, "Backend invalid_request");
      return true;
    },
  );
  status = 503;
  await assert.rejects(
    client().createSession("call-id", signal()),
    backendError("unavailable"),
  );
});

test("oversized backend response is canceled and rejected", async (t) => {
  let canceled = false;
  t.mock.method(
    globalThis,
    "fetch",
    async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new Uint8Array(65537));
          },
          cancel() {
            canceled = true;
          },
        }),
      ),
  );
  await assert.rejects(
    client().createSession("call-id", signal()),
    backendError("invalid_response"),
  );
  assert.equal(canceled, true);
});

test("backend requests are bounded by timeout and respect caller cancellation", async (t) => {
  t.mock.method(
    globalThis,
    "fetch",
    async (_url: string | URL | Request, options?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        // A referenced timer keeps this test alive while AbortSignal.timeout runs.
        const fallback = setTimeout(
          () => reject(new Error("request did not abort")),
          1000,
        );
        const abort = () => {
          clearTimeout(fallback);
          reject(options?.signal?.reason);
        };
        if (options?.signal?.aborted) abort();
        else options?.signal?.addEventListener("abort", abort, { once: true });
      }),
  );
  const started = performance.now();
  await assert.rejects(
    createBackendClient("http://127.0.0.1:8000", "token", 20).createSession(
      "call-id",
      signal(),
    ),
    backendError("unavailable"),
  );
  assert.ok(
    performance.now() - started < 800,
    "timeout should abort before the fallback",
  );
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    client().createSession("call-id", controller.signal),
    backendError("unavailable"),
  );
});

test("session deletion authenticates and accepts an empty 204 response", async (t) => {
  t.mock.method(
    globalThis,
    "fetch",
    async (url: string | URL | Request, options?: RequestInit) => {
      assert.equal(url, `http://127.0.0.1:8000/sessions/${sessionId}`);
      assert.equal(options?.method, "DELETE");
      assert.equal(options?.body, undefined);
      assert.equal(
        new Headers(options?.headers).get("Authorization"),
        "Bearer synthetic-internal-token",
      );
      return new Response(null, { status: 204 });
    },
  );
  await client().deleteSession(sessionId);
});

test("spoken-ID resolution is advertised and its results stay separate from member facts", async (t) => {
  const resolver = {
    toolName: "resolve_member_id",
    toolUseId: "resolve-1",
    input: { spoken_id: "demo double zero one" },
  };
  let payload: unknown = { status: "resolved", member_id: "DEMO001" };
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({
      tool_name: resolver.toolName,
      tool_call_id: resolver.toolUseId,
      result: payload,
    }),
  );
  assert.deepEqual(
    await client().invokeTool(sessionId, resolver, signal()),
    payload,
  );
  payload = { status: "repeat" };
  assert.deepEqual(
    await client().invokeTool(sessionId, resolver, signal()),
    payload,
  );
  payload = { status: "confirm", candidates: ["DEMO001", "DEM0001"] };
  assert.deepEqual(
    await client().invokeTool(sessionId, resolver, signal()),
    payload,
  );
  for (const invalid of [
    { status: "resolved", member_id: "demo zero zero one" },
    { status: "confirm", candidates: ["DEMO001"] },
    { status: "confirm", candidates: ["DEMO001", "DEMO001"] },
    { status: "repeat", member_id: "DEMO001" },
    { status: "success", member },
  ]) {
    payload = invalid;
    await assert.rejects(
      client().invokeTool(sessionId, resolver, signal()),
      backendError("invalid_response"),
    );
  }
});

test("session can advertise the backend ID resolver", async (t) => {
  const advertised = {
    ...session,
    tools: [
      ...session.tools,
      {
        name: "resolve_member_id",
        description: "Resolve spoken ID",
        input_schema: { type: "object" },
      },
    ],
  };
  t.mock.method(globalThis, "fetch", async () => Response.json(advertised));
  assert.deepEqual(
    await client().createSession(`CA${"1".repeat(32)}`, signal()),
    advertised,
  );
});
