import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import type { AbeBackend } from "./backend.js";
import { demoPage } from "./demo-page.js";

// Separate entry point: production Twilio authentication and audio are unchanged.
export function createDemoServer(
  backend: AbeBackend,
  port = 3000,
  frontendPort = 5173,
) {
  let busy = false;
  return createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    if (
      req.headers.host !== `127.0.0.1:${port}` &&
      req.headers.host !== `localhost:${port}`
    ) {
      res.writeHead(403).end();
      return;
    }
    if (req.method === "GET" && req.url === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" }).end(
        JSON.stringify({
          status: "ok",
          mode: "offline-demo",
          telephony: "disabled",
        }),
      );
      return;
    }
    if (req.method === "GET" && req.url === "/") {
      res
        .writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
        .end(
          demoPage.replaceAll("127.0.0.1:5173", `127.0.0.1:${frontendPort}`),
        );
      return;
    }
    if (req.method !== "POST" || req.url !== "/demo/run") {
      res.writeHead(404).end();
      return;
    }
    // Browser-only same-origin invocation, no CORS and no user-selected tool or arguments.
    if (
      req.headers.origin !== `http://${req.headers.host}` ||
      req.headers["x-abe-demo"] !== "1"
    ) {
      res.writeHead(403).end();
      return;
    }
    if (busy) {
      res.writeHead(429).end();
      return;
    }
    busy = true;
    let sessionId: string | undefined;
    try {
      const session = await backend.createSession(
        `CA${randomBytes(16).toString("hex")}`,
        AbortSignal.timeout(5000),
      );
      sessionId = session.session_id;
      const calls: [string, Record<string, unknown>][] = [
        ["resolve_member_id", { spoken_id: "D E M O double zero one" }],
        ["get_member", { member_id: "DEMO001" }],
        [
          "update_conversation_context",
          { intent: "estimate", procedure: "crown", provider_id: "DEMO_P1" },
        ],
        [
          "retrieve_plan_context",
          { query: "What does the plan say about crowns?" },
        ],
        [
          "search_providers",
          { zip_code: "27401", radius_miles: 10, procedure: "crown" },
        ],
        ["calculate_benefit", { treatment_date: "2026-11-01" }],
        [
          "optimize_benefits",
          { procedure: "crown", requested_date: "2026-11-01" },
        ],
      ];
      const results = [];
      for (const [toolName, input] of calls) {
        results.push({
          tool: toolName,
          result: await backend.invokeTool(
            sessionId,
            {
              toolName,
              toolUseId: `demo-${results.length}`,
              input,
            },
            AbortSignal.timeout(5000),
          ),
        });
      }
      res
        .writeHead(200, { "Content-Type": "application/json" })
        .end(JSON.stringify({ mode: "offline-demo", results }));
    } catch {
      res.writeHead(503, { "Content-Type": "application/json" }).end(
        JSON.stringify({
          error:
            "Demo tools unavailable. Check that the backend is running, then retry.",
        }),
      );
    } finally {
      if (sessionId) await backend.deleteSession(sessionId).catch(() => {});
      busy = false;
    }
  });
}
