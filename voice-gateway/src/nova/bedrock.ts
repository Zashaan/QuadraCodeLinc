import {
  BedrockRuntimeClient,
  InvokeModelWithBidirectionalStreamCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { NodeHttp2Handler } from "@smithy/node-http-handler";
import { StreamingNovaSession } from "./session.js";
import type { NovaFactory } from "./types.js";

export interface BedrockNovaConfig {
  region: string;
  modelId: string;
  voiceId: string;
}

export function createBedrockNovaFactory(
  config: BedrockNovaConfig,
): NovaFactory {
  return {
    create(callbacks) {
      // Each phone call owns its connection and can destroy it independently.
      const client = new BedrockRuntimeClient({
        region: config.region,
        maxAttempts: 1,
        requestHandler: new NodeHttp2Handler({
          requestTimeout: 30_000,
          sessionTimeout: 30_000,
          disableConcurrentStreams: true,
        }),
      });
      return new StreamingNovaSession(config.voiceId, callbacks, {
        async open(input, signal) {
          async function* body() {
            for await (const bytes of input) yield { chunk: { bytes } };
          }
          const response = await client.send(
            new InvokeModelWithBidirectionalStreamCommand({
              modelId: config.modelId,
              body: body(),
            }),
            { abortSignal: signal },
          );
          const output = response.body;
          if (!output) throw new Error("Nova returned no output stream.");
          return (async function* () {
            for await (const item of output) {
              if (item.chunk?.bytes) yield item.chunk.bytes;
              else if (!item.$unknown)
                throw new Error("Nova stream service error.");
            }
          })();
        },
        destroy: () => client.destroy(),
      });
    },
  };
}
