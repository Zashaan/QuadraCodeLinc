import { createBackendClient } from "./backend.js";
import { loadConfig } from "./config.js";
import { log } from "./log.js";
import { createBedrockNovaFactory } from "./nova/bedrock.js";
import { createGateway } from "./server.js";

try {
  const config = loadConfig(process.env);
  const gateway = createGateway(
    config,
    createBackendClient(config.backendUrl, config.internalToken),
    createBedrockNovaFactory({
      region: config.region,
      modelId: config.modelId,
      voiceId: config.voiceId,
    }),
    log,
  );
  gateway.server.on("error", () => {
    log("server_error");
    process.exitCode = 1;
  });
  gateway.server.listen(config.port, "127.0.0.1", () =>
    log("server_started", { port: config.port }),
  );
  let stopping = false;
  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, () => {
      if (stopping) return;
      stopping = true;
      void gateway.close().catch(() => {
        log("shutdown_error");
        process.exitCode = 1;
      });
    });
  }
} catch (error) {
  // loadConfig emits only field names; never serialize SDK errors or the environment.
  log("configuration_error", {
    message:
      error instanceof Error &&
      error.message.startsWith("Invalid configuration:")
        ? error.message
        : "Startup failed",
  });
  process.exitCode = 1;
}
