import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { BackendError, createBackendClient } from "../src/backend.js";

// Exercises the real Python HTTP serialization against the gateway's wire validation.
// TestClient runs in-process: no dev server, port, AWS, Twilio, or Nova is required.
test("all advertised backend tools and real benefit results cross the gateway boundary", async (t) => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const execution = spawnSync(
    `${root}backend/.venv/bin/python`,
    ["scripts/smoke-benefits.py", "--contracts"],
    { cwd: root, encoding: "utf8", timeout: 15000 },
  );
  assert.equal(execution.status, 0, execution.stderr);
  const contracts = JSON.parse(execution.stdout);
  let response: unknown = contracts.session;
  t.mock.method(globalThis, "fetch", async () => Response.json(response));
  const backend = createBackendClient("http://127.0.0.1:8000", "test");
  const signal = new AbortController().signal;
  assert.equal(
    (await backend.createSession(`CA${"a".repeat(32)}`, signal)).tools.length,
    7,
  );
  for (const result of contracts.results) {
    response = result;
    assert.deepEqual(
      await backend.invokeTool(
        contracts.session.session_id,
        {
          toolName: result.tool_name,
          toolUseId: result.tool_call_id,
          input: {},
        },
        signal,
      ),
      result.result,
    );
  }
  const estimate = contracts.results.find(
    (result: { tool_name: string }) => result.tool_name === "calculate_benefit",
  );
  for (const changes of [
    { plan_payment: -1 },
    { plan_payment: "NaN" },
    { plan_payment: 600 },
    { invented: "fact" },
    { sources: [{ document: "invented citation" }] },
  ]) {
    response = { ...estimate, result: { ...estimate.result, ...changes } };
    await assert.rejects(
      backend.invokeTool(
        contracts.session.session_id,
        {
          toolName: estimate.tool_name,
          toolUseId: estimate.tool_call_id,
          input: {},
        },
        signal,
      ),
      (error: unknown) =>
        error instanceof BackendError && error.code === "invalid_response",
    );
  }
});
