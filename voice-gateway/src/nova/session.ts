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

interface ResponseState {
  interrupted: boolean;
  audioStarted: boolean;
}

interface OutputBlock {
  type: unknown;
  response: ResponseState | undefined;
  startsResponse: boolean;
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
  // A completion can span several conversational turns. Never blacklist its ID.
  // Keep bounded block ownership so a late event from A cannot clear response B.
  private readonly outputBlocks = new Map<string, OutputBlock>();
  private response: ResponseState | undefined;
  private nextResponse = true;
  private readonly assistantTextBlocks = new Set<string>();
  private readonly finalText = new Map<
    string,
    { role: "user" | "assistant"; text: string }
  >();
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
      const contentId = identifier(start.contentId);
      // Duplicate/stale announcements cannot reassign an old block to B.
      if (this.outputBlocks.has(contentId)) return;
      let final = false;
      if (
        start.type === "TEXT" &&
        (start.role === "USER" || start.role === "ASSISTANT")
      ) {
        let stage: unknown;
        try {
          stage =
            typeof start.additionalModelFields === "string"
              ? JSON.parse(start.additionalModelFields)
              : undefined;
        } catch {
          stage = undefined;
        }
        if (
          stage &&
          typeof stage === "object" &&
          "generationStage" in stage &&
          stage.generationStage === "FINAL"
        ) {
          final = true;
          if (this.finalText.size >= 32)
            throw new Error("Too many transcript blocks.");
          this.finalText.set(identifier(start.contentId), {
            role: start.role === "USER" ? "user" : "assistant",
            text: "",
          });
        }
      }
      if (start.type === "TEXT" && start.role === "USER")
        this.nextResponse = true;
      if (start.role === "ASSISTANT" || start.type === "TOOL") {
        if (start.type === "AUDIO" || start.type === "TOOL") {
          // A cancelled speculative response may still announce its first audio
          // block. Keep it suppressed until a fresh response starts.
          if (
            this.nextResponse &&
            !(
              start.type === "AUDIO" &&
              this.response?.interrupted &&
              !this.response.audioStarted
            )
          )
            this.beginResponse();
          if (start.type === "AUDIO" && this.response)
            this.response.audioStarted = true;
        }
        this.outputBlocks.set(contentId, {
          type: start.type,
          response: this.response,
          startsResponse: this.nextResponse && !final,
        });
        // Evicted stale block IDs cannot affect a newer response.
        if (this.outputBlocks.size > 1024) {
          const oldest = this.outputBlocks.keys().next().value;
          if (oldest) this.outputBlocks.delete(oldest);
        }
      }
      if (start.type === "TEXT" && start.role === "ASSISTANT") {
        if (this.assistantTextBlocks.size >= 32)
          throw new Error("Too many text blocks.");
        this.assistantTextBlocks.add(identifier(start.contentId));
      }
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
    if (event.textOutput) {
      const output = record(event.textOutput);
      const contentId = identifier(output.contentId);
      const transcript = this.finalText.get(contentId);
      if (transcript && typeof output.content === "string") {
        if (transcript.text.length + output.content.length > 4000)
          throw new Error("Transcript block too large.");
        transcript.text += output.content;
      }
      // AWS samples also emit a JSON interruption marker in assistant text.
      // Only recognize the exact structured marker, never ordinary caller text.
      if (
        this.assistantTextBlocks.has(identifier(output.contentId)) &&
        typeof output.content === "string" &&
        output.content.length <= 100
      ) {
        let marker: unknown;
        try {
          marker = JSON.parse(output.content);
        } catch {
          marker = undefined;
        }
        if (
          marker &&
          typeof marker === "object" &&
          !Array.isArray(marker) &&
          Object.keys(marker).length === 1 &&
          "interrupted" in marker &&
          marker.interrupted === true
        ) {
          this.interrupt(this.outputBlocks.get(contentId)?.response);
          return;
        }
      }
      const block = this.outputBlocks.get(contentId);
      if (block?.startsResponse && typeof output.content === "string") {
        block.response = this.beginResponse();
        block.startsResponse = false;
      }
    }
    if (event.audioOutput) {
      const audio = record(event.audioOutput);
      const block = this.outputBlocks.get(identifier(audio.contentId));
      if (
        block?.type !== "AUDIO" ||
        !block.response ||
        block.response !== this.response ||
        block.response.interrupted
      )
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
      const contentId = identifier(end.contentId);
      const outputBlock = this.outputBlocks.get(contentId);
      if (end.stopReason === "INTERRUPTED") {
        this.interrupt(outputBlock?.response);
      } else if (
        end.stopReason === "END_TURN" &&
        outputBlock?.type === "AUDIO" &&
        outputBlock.response === this.response
      ) {
        this.nextResponse = true;
      }
      const transcript = this.finalText.get(contentId);
      this.finalText.delete(contentId);
      if (
        transcript?.text.trim() &&
        !/^\s*\{\s*"interrupted"\s*:\s*true\s*\}\s*$/.test(transcript.text)
      ) {
        this.callbacks.onTranscript?.({
          event_id: contentId,
          role: transcript.role,
          text: transcript.text.trim(),
        });
      }
      this.assistantTextBlocks.delete(contentId);
      const block = this.toolBlocks.get(contentId);
      if (block) {
        this.toolBlocks.delete(contentId);
        // An interrupted partial tool request is not a broken conversation.
        if (end.stopReason === "INTERRUPTED") return;
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

  private beginResponse(): ResponseState {
    this.response = { interrupted: false, audioStarted: false };
    this.nextResponse = false;
    return this.response;
  }

  private interrupt(response: ResponseState | undefined): void {
    if (!response || response.interrupted) return;
    response.interrupted = true;
    if (response !== this.response) return;
    this.nextResponse = true;
    this.callbacks.onInterrupted();
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
    this.outputBlocks.clear();
    this.response = undefined;
    this.assistantTextBlocks.clear();
    this.finalText.clear();
    this.transport.destroy();
    try {
      if (error) this.callbacks.onError(error);
    } finally {
      this.callbacks.onClose();
    }
  }
}
