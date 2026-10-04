import { createBackendClient } from "./backend.js";
import { createDemoServer } from "./demo-server.js";

const token = process.env.ABE_INTERNAL_TOKEN;
if (!token || token.length < 32)
  throw new Error("Use bash scripts/run-demo.sh to launch the demo.");
const port = Number(process.env.ABE_DEMO_PORT || 3000);
const backendPort = Number(process.env.ABE_DEMO_BACKEND_PORT || 8000);
const frontendPort = Number(process.env.ABE_DEMO_FRONTEND_PORT || 5173);
const server = createDemoServer(
  createBackendClient(`http://127.0.0.1:${backendPort}`, token),
  port,
  frontendPort,
);
server.listen(port, "127.0.0.1");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    server.close();
    server.closeAllConnections();
  });
}
