import assert from "node:assert/strict";
import { once } from "node:events";
import { request } from "node:http";
import type { AddressInfo } from "node:net";
import test from "node:test";
import type { AbeBackend } from "../src/backend.js";
import { createDemoServer } from "../src/demo-server.js";

async function fixture(fail = false) {
  const calls: string[] = [];
  let deleted = 0;
  const backend: AbeBackend = {
    createSession: async () => ({
      session_id: "demo",
      system_prompt: "demo",
      tools: [],
    }),
    invokeTool: async (_id, tool) => {
      calls.push(tool.toolName);
      if (fail) throw new Error("private failure details");
      return { status: "resolved", member_id: "DEMO001" };
    },
    deleteSession: async () => {
      deleted++;
    },
    appendTranscripts: async () => {},
  };
  const server = createDemoServer(backend);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = (server.address() as AddressInfo).port;
  async function send(
    path: string,
    origin = "http://127.0.0.1:3000",
    host = "127.0.0.1:3000",
  ) {
    return new Promise<{ status: number; body: string }>((resolve, reject) => {
      const req = request(
        {
          hostname: "127.0.0.1",
          port,
          path,
          method: "POST",
          headers: { Host: host, Origin: origin, "X-Abe-Demo": "1" },
        },
        (res) => {
          let body = "";
          res.on("data", (chunk) => {
            body += chunk;
          });
          res.on("end", () => resolve({ status: res.statusCode || 0, body }));
        },
      );
      req.on("error", reject);
      req.end();
    });
  }
  return { server, send, calls, deleted: () => deleted };
}

test("offline demo invokes seven fixed tools and cleans up its session", async () => {
  const f = await fixture();
  try {
    const result = await f.send("/demo/run");
    assert.equal(result.status, 200);
    assert.equal(JSON.parse(result.body).results.length, 7);
    assert.equal(f.calls[0], "resolve_member_id");
    assert.equal(f.calls[6], "optimize_benefits");
    assert.equal(f.deleted(), 1);
  } finally {
    f.server.close();
  }
});

test("demo rejects cross-origin, hostile Host, and production telephony requests", async () => {
  const f = await fixture();
  try {
    assert.equal(
      (await f.send("/demo/run", "https://evil.example")).status,
      403,
    );
    assert.equal(
      (await f.send("/demo/run", "http://evil.example", "evil.example")).status,
      403,
    );
    assert.equal((await f.send("/twilio/incoming")).status, 404);
    assert.equal(f.calls.length, 0);
  } finally {
    f.server.close();
  }
});

test("failed demo calls clean up and never disclose upstream errors", async () => {
  const f = await fixture(true);
  try {
    const result = await f.send("/demo/run");
    assert.equal(result.status, 503);
    assert.equal(result.body.includes("private failure"), false);
    assert.equal(f.deleted(), 1);
    assert.equal((await f.send("/demo/run")).status, 503);
    assert.equal(f.deleted(), 2);
  } finally {
    f.server.close();
  }
});
