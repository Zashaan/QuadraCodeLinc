import { randomUUID } from "node:crypto";
import { EventQueue } from "./queue.js";
import type { NovaCallbacks, NovaSession, ToolDefinition } from "./types.js";

/** Transport seam for protocol unit tests; production uses the AWS SDK HTTP/2 stream. */
export interface NovaTransport {
  open(
    input: AsyncIterable<Uint8Array>,
    signal: AbortSignal,
  ): Promise<AsyncIterable<Uint8Array>>;
  destroy(): void;
}

interface ToolBlock {
  name: string;
  id: string;
  json: string;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid Nova event.");
  }
  return value as Record<string, unknown>;
}

function identifier(value: unknown): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 256) {
    throw new Error("Invalid Nova event identifier.");
  }
  return value;
}

function toolError(code: string): Record<string, unknown> {
  return {
    status: "error",
    error: { code, message: "The tool could not complete this request." },
  };
}

async function deadline<T>(
  work: Promise<T>,
  ms: number,
  signal: AbortSignal,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: () => void = () => {};
  try {
    return await Promise.race([
      work,
      new Promise<never>((_, reject) => {
        abort = () => reject(new Error("Nova session closed."));
        timer = setTimeout(
          () => reject(new Error("Nova operation timed out.")),
          ms,
        );
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      }),
    ]);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}

export class StreamingNovaSession implements NovaSession {
  private state: "idle" | "starting" | "active" | "closing" | "closed" = "idle";
  private readonly queue = new EventQueue();
  private readonly abort = new AbortController();
  private readonly promptName = randomUUID();
  private readonly audioName = randomUUID();
  private readonly allowedTools = new Set<string>();
  private readonly toolBlocks = new Map<string, ToolBlock>();
  private readonly seenToolIds = new Set<string>();
  private readonly interruptedCompletions = new Set<string>();
  private activeTools = 0;
  private running: Promise<void> | undefined;
  private closing: Promise<void> | undefined;

  constructor(
    private readonly voiceId: string,
    private readonly callbacks: NovaCallbacks,
    private readonly transport: NovaTransport,
  ) {}

  async start(systemPrompt: string, tools: ToolDefinition[]): Promise<void> {
    if (this.state !== "idle")
      throw new Error("Nova session has already started.");
    this.state = "starting";
    try {
      for (const tool of tools) this.allowedTools.add(tool.name);
      this.queue.push({
        sessionStart: {
          inferenceConfiguration: {
            maxTokens: 1024,
            topP: 0.9,
            temperature: 0.5,
          },
          turnDetectionConfiguration: { endpointingSensitivity: "MEDIUM" },
        },
      });
      this.queue.push({
        promptStart: {
          promptName: this.promptName,
          textOutputConfiguration: { mediaType: "text/plain" },
          audioOutputConfiguration: {
            ...this.audioConfiguration(),
            voiceId: this.voiceId,
          },
          toolUseOutputConfiguration: { mediaType: "application/json" },
          toolConfiguration: {
            tools: tools.map((tool) => ({
              toolSpec: {
                name: tool.name,
                description: tool.description,
                inputSchema: { json: JSON.stringify(tool.input_schema) },
              },
            })),
          },
        },
      });
      this.text(systemPrompt, "SYSTEM", false);
      this.queue.push({
        contentStart: {
          promptName: this.promptName,
          contentName: this.audioName,
          type: "AUDIO",
          role: "USER",
          interactive: true,
          audioInputConfiguration: this.audioConfiguration(),
        },
      });
      // Nova 2 cross-modal input explicitly supports model-start-first conversations.
      this.text(
        "Begin the conversation now with your brief greeting and opening question.",
        "USER",
        true,
      );
      const output = await deadline(
        this.transport.open(this.queue, this.abort.signal),
        10_000,
        this.abort.signal,
      );
      if (this.abort.signal.aborted) throw new Error("Nova session closed.");
      this.state = "active";
      this.running = this.consume(output);
    } catch {
      const error = new Error("Unable to start Nova voice session.");
      this.fail(error);
      throw error;
    }
  }

  sendAudio(pcm: Buffer): void {
    if (this.state !== "active" && this.state !== "starting") return;
    if (pcm.length === 0) return;
    try {
      if (pcm.length % 2 !== 0 || pcm.length > 64 * 1024)
        throw new Error("Invalid PCM frame.");
      this.queue.push({
        audioInput: {
          promptName: this.promptName,
          contentName: this.audioName,
          content: pcm.toString("base64"),
        },
      });
    } catch {
      this.fail(
        new Error("Nova audio input failed or exceeded its buffer limit."),
      );
    }
  }

  close(): Promise<void> {
    if (!this.closing) this.closing = this.closeStream();
    return this.closing;
  }

  private async closeStream(): Promise<void> {
    if (this.state === "closed") return;
    if (this.state === "idle" || this.state === "starting") {
      this.finish();
      return;
    }
    this.state = "closing";
    try {
      this.queue.push({
        contentEnd: {
          promptName: this.promptName,
          contentName: this.audioName,
        },
      });
      this.queue.push({ promptEnd: { promptName: this.promptName } });
      this.queue.push({ sessionEnd: {} });
      this.queue.end();
      await deadline(
        this.running ?? Promise.resolve(),
        1000,
        this.abort.signal,
      );
    } catch {
      // Peer shutdown and forced abort are normal during call teardown.
    } finally {
      this.finish();
    }
  }

  private audioConfiguration(): Record<string, unknown> {
    return {
      mediaType: "audio/lpcm",
      sampleRateHertz: 8000,
      sampleSizeBits: 16,
      channelCount: 1,
      encoding: "base64",
      audioType: "SPEECH",
    };
  }

  private text(
    content: string,
    role: "SYSTEM" | "USER",
    interactive: boolean,
  ): void {
    const contentName = randomUUID();
    this.queue.push({
      contentStart: {
        promptName: this.promptName,
        contentName,
        type: "TEXT",
        role,
        interactive,
        textInputConfiguration: { mediaType: "text/plain" },
      },
    });
    this.queue.push({
      textInput: { promptName: this.promptName, contentName, content },
    });
    this.queue.push({
      contentEnd: { promptName: this.promptName, contentName },
    });
  }

  private async consume(output: AsyncIterable<Uint8Array>): Promise<void> {
    try {
      for await (const bytes of output) {
        if (this.state === "closed") break;
        if (bytes.byteLength > 1024 * 1024)
          throw new Error("Nova event too large.");
        const envelope = record(
          JSON.parse(Buffer.from(bytes).toString("utf8")),
        );
        this.handle(record(envelope.event));
      }
      this.finish();
    } catch {
      if (this.state === "closing" || this.state === "closed") this.finish();
      else this.fail(new Error("Nova output stream failed."));
    }
  }

  private handle(event: Record<string, unknown>): void {
    if (this.state !== "active") return;
    if (event.contentStart) {
      const start = record(event.contentStart);
      if (start.type === "TOOL") {
        if (this.toolBlocks.size + this.activeTools >= 4)
          throw new Error("Too many Nova tools.");
        this.toolBlocks.set(identifier(start.contentId), {
          name: "",
          id: "",
          json: "",
        });
      }
    }
    if (event.audioOutput) {
      const audio = record(event.audioOutput);
      if (this.interruptedCompletions.has(identifier(audio.completionId)))
        return;
      if (
        typeof audio.content !== "string" ||
        !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
          audio.content,
        )
      ) {
        throw new Error("Invalid Nova audio payload.");
      }
      const pcm = Buffer.from(audio.content, "base64");
      if (pcm.length % 2 !== 0) throw new Error("Invalid Nova PCM payload.");
      this.callbacks.onAudio(pcm);
    }
    if (event.toolUse) {
      const tool = record(event.toolUse);
      const block = this.toolBlocks.get(identifier(tool.contentId));
      if (!block || typeof tool.content !== "string")
        throw new Error("Uncorrelated Nova tool.");
      const name = identifier(tool.toolName);
      const id = identifier(tool.toolUseId);
      if ((block.id && block.id !== id) || (block.name && block.name !== name))
        throw new Error("Conflicting Nova tool.");
      block.id = id;
      block.name = name;
      block.json += tool.content;
      if (Buffer.byteLength(block.json) > 64 * 1024)
        throw new Error("Nova tool input too large.");
    }
    if (event.contentEnd) {
      const end = record(event.contentEnd);
      if (end.stopReason === "INTERRUPTED") {
        this.interruptedCompletions.add(identifier(end.completionId));
        if (this.interruptedCompletions.size > 256)
          throw new Error("Nova interruption limit reached.");
        this.callbacks.onInterrupted();
      }
      const contentId = identifier(end.contentId);
      const block = this.toolBlocks.get(contentId);
      if (block) {
        this.toolBlocks.delete(contentId);
        if (end.stopReason !== "TOOL_USE" || !block.id)
          throw new Error("Incomplete Nova tool.");
        if (this.seenToolIds.has(block.id) || this.seenToolIds.size >= 128)
          throw new Error("Duplicate or excessive Nova tool calls.");
        this.seenToolIds.add(block.id);
        this.activeTools += 1;
        void this.executeTool(block).finally(() => {
          this.activeTools -= 1;
        });
      }
    }
  }

  private async executeTool(block: ToolBlock): Promise<void> {
    let result: unknown;
    try {
      if (!this.allowedTools.has(block.name))
        result = toolError("unknown_tool");
      else {
        let input: unknown;
        try {
          input = JSON.parse(block.json);
        } catch {
          result = toolError("invalid_arguments");
        }
        if (!result) {
          result = await deadline(
            this.callbacks.onToolUse({
              toolName: block.name,
              toolUseId: block.id,
              input,
            }),
            15_000,
            this.abort.signal,
          );
        }
      }
    } catch {
      result = toolError("tool_unavailable");
    }
    if (this.state !== "active") return;
    try {
      let content: string;
      try {
        const json = JSON.stringify(result);
        if (json === undefined || Buffer.byteLength(json) > 64 * 1024)
          throw new Error("Invalid tool result.");
        content = json;
      } catch {
        content = JSON.stringify(toolError("invalid_tool_result"));
      }
      const contentName = randomUUID();
      this.queue.push({
        contentStart: {
          promptName: this.promptName,
          contentName,
          type: "TOOL",
          role: "TOOL",
          interactive: false,
          toolResultInputConfiguration: {
            toolUseId: block.id,
            type: "TEXT",
            textInputConfiguration: { mediaType: "text/plain" },
          },
        },
      });
      this.queue.push({
        toolResult: { promptName: this.promptName, contentName, content },
      });
      this.queue.push({
        contentEnd: { promptName: this.promptName, contentName },
      });
    } catch {
      this.fail(new Error("Nova tool result delivery failed."));
    }
  }

  private fail(error: Error): void {
    if (this.state === "closed") return;
    this.finish(error);
  }

  private finish(error?: Error): void {
    if (this.state === "closed") return;
    this.state = "closed";
    this.abort.abort();
    this.queue.end(true);
    this.toolBlocks.clear();
    this.transport.destroy();
    try {
      if (error) this.callbacks.onError(error);
    } finally {
      this.callbacks.onClose();
    }
  }
}
