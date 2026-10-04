import { randomUUID } from "node:crypto";
import { WebSocket } from "ws";
import { mulawToPcm16, pcm16ToMulaw } from "./audio.js";
import { type AbeBackend, BackendError } from "./backend.js";
import type { Logger } from "./log.js";
import type {
  NovaFactory,
  NovaSession,
  ToolInvocation,
  TranscriptEvent,
} from "./nova/types.js";
import type { TelephonyAdapter, TelephonyEvent } from "./telephony.js";

export interface CallOptions {
  socket: WebSocket;
  telephony: TelephonyAdapter;
  backend: AbeBackend;
  novaFactory: NovaFactory;
  accountSid: string;
  log: Logger;
  maxCallMs?: number;
  startupTimeoutMs?: number;
}

/** One call owns all resources. A failed caller cannot take down other calls. */
export class VoiceCall {
  private readonly id = randomUUID();
  private readonly abort = new AbortController();
  private streamId: string | undefined;
  private callId: string | undefined;
  private sessionId: string | undefined;
  private nova: NovaSession | undefined;
  private opening: Promise<void> | undefined;
  private closing: Promise<void> | undefined;
  private closed = false;
  private ready = false;
  private pending: Buffer[] = [];
  private pendingBytes = 0;
  private pcmRemainder = Buffer.alloc(0);
  private readonly startupTimer: NodeJS.Timeout;
  private readonly durationTimer: NodeJS.Timeout;
  private readonly heartbeat: NodeJS.Timeout;
  private alive = true;
  private transcriptQueue: TranscriptEvent[] = [];
  private transcriptWork: Promise<void> | undefined;
  private transcriptIncomplete = false;

  constructor(private readonly options: CallOptions) {
    this.startupTimer = setTimeout(
      () => void this.close("startup_timeout"),
      options.startupTimeoutMs ?? 20_000,
    ).unref();
    // Bedrock bidirectional sessions are limited to eight minutes. This demo closes at seven.
    this.durationTimer = setTimeout(
      () => void this.close("call_limit"),
      options.maxCallMs ?? 420_000,
    ).unref();
    this.heartbeat = setInterval(() => {
      if (!this.alive) {
        void this.close("connection_timeout");
        return;
      }
      this.alive = false;
      if (options.socket.readyState === WebSocket.OPEN) options.socket.ping();
    }, 30_000).unref();
    options.socket.on("pong", () => {
      this.alive = true;
    });
    options.socket.on("message", (raw, binary) => {
      if (this.closed) return;
      try {
        if (binary) throw new Error("Expected JSON text");
        this.receive(options.telephony.parse(raw.toString()));
      } catch {
        void this.close("invalid_media_event");
      }
    });
    options.socket.on("close", () => void this.close("caller_disconnected"));
    options.socket.on("error", () => void this.close("socket_error"));
  }

  private event(name: string, reason?: string) {
    this.options.log(name, {
      connection_id: this.id,
      ...(reason ? { reason } : {}),
    });
  }

  private receive(event: TelephonyEvent) {
    if (event.type === "connected") return;
    if (event.type === "start") {
      if (this.streamId || event.accountId !== this.options.accountSid)
        throw new Error("Unexpected stream");
      this.streamId = event.streamId;
      this.callId = event.callId;
      this.event("twilio_stream_started");
      this.opening = this.open(event.callId);
      return;
    }
    if (!this.streamId || event.streamId !== this.streamId)
      throw new Error("Unexpected stream");
    if (event.type === "stop") {
      if (
        event.callId !== this.callId ||
        event.accountId !== this.options.accountSid
      )
        throw new Error("Unexpected caller");
      this.event("twilio_stream_stopped");
      void this.close("caller_stopped");
    } else if (event.type === "audio") {
      const pcm = mulawToPcm16(event.audio);
      if (this.ready) this.nova?.sendAudio(pcm);
      else {
        this.pendingBytes += pcm.byteLength;
        if (this.pendingBytes > 80_000)
          throw new Error("Startup audio buffer full");
        this.pending.push(pcm);
      }
    }
  }

  private async open(callId: string): Promise<void> {
    try {
      const session = await this.options.backend.createSession(
        callId,
        this.abort.signal,
      );
      this.sessionId = session.session_id;
      if (this.closed) return;
      this.event("session_created");
      this.nova = this.options.novaFactory.create({
        onTranscript: (event) => {
          if (this.closed || !this.options.backend.appendTranscripts) return;
          if (this.transcriptQueue.length >= 100) {
            this.transcriptIncomplete = true;
            return;
          }
          this.transcriptQueue.push(event);
          this.saveTranscripts();
        },
        onAudio: (pcm) => this.sendAudio(pcm),
        onInterrupted: () => {
          this.pcmRemainder = Buffer.alloc(0);
          if (this.streamId)
            this.send(this.options.telephony.clear(this.streamId));
        },
        onToolUse: (tool) => this.invokeTool(tool),
        onError: () => {
          this.event("nova_error");
          void this.close("nova_error");
        },
        onClose: () => {
          void this.close("nova_closed");
        },
      });
      await this.nova.start(session.system_prompt, session.tools);
      if (this.closed) return;
      this.ready = true;
      clearTimeout(this.startupTimer);
      this.event("nova_connection_opened");
      for (const audio of this.pending) this.nova.sendAudio(audio);
      this.pending = [];
      this.pendingBytes = 0;
    } catch {
      if (!this.closed) {
        this.event("call_setup_failed");
        void this.close("upstream_unavailable");
      }
    }
  }

  private async invokeTool(tool: ToolInvocation): Promise<unknown> {
    if (this.closed || !this.sessionId)
      return { status: "error", code: "session_closed" };
    this.event("tool_invoked");
    try {
      return await this.options.backend.invokeTool(
        this.sessionId,
        tool,
        this.abort.signal,
      );
    } catch (error) {
      const code =
        error instanceof BackendError && error.code === "invalid_request"
          ? "invalid_tool_input"
          : "backend_unavailable";
      this.event("backend_error", code);
      return {
        status: "error",
        code,
        message:
          code === "invalid_tool_input"
            ? "The tool arguments were invalid. Check the requested tool's schema and ask for the missing or corrected information."
            : "This tool is temporarily unavailable. Explain that its information cannot currently be verified, and continue the conversation. Do not guess.",
      };
    }
  }

  private saveTranscripts(): void {
    if (
      this.transcriptWork ||
      !this.sessionId ||
      !this.options.backend.appendTranscripts
    )
      return;
    this.transcriptWork = (async () => {
      while (this.transcriptQueue.length && this.sessionId) {
        const batch = this.transcriptQueue.splice(0, 20);
        try {
          await this.options.backend.appendTranscripts?.(this.sessionId, batch);
        } catch {
          this.transcriptIncomplete = true;
          this.event("transcript_save_failed");
        }
      }
    })().finally(() => {
      this.transcriptWork = undefined;
    });
  }

  private sendAudio(pcm: Buffer) {
    if (this.closed || !this.streamId) return;
    const aligned = this.pcmRemainder.length
      ? Buffer.concat([this.pcmRemainder, pcm])
      : pcm;
    const evenLength = aligned.length - (aligned.length % 2);
    this.pcmRemainder = Buffer.from(aligned.subarray(evenLength));
    if (evenLength)
      this.send(
        this.options.telephony.audio(
          this.streamId,
          pcm16ToMulaw(aligned.subarray(0, evenLength)),
        ),
      );
  }

  private send(message: string) {
    if (this.closed || this.options.socket.readyState !== WebSocket.OPEN)
      return;
    if (this.options.socket.bufferedAmount > 128_000) {
      void this.close("slow_consumer");
      return;
    }
    this.options.socket.send(message, (error) => {
      if (error) void this.close("send_failed");
    });
  }

  close(reason = "server_shutdown"): Promise<void> {
    if (this.closed) return this.closing ?? Promise.resolve();
    this.closed = true;
    this.abort.abort();
    clearTimeout(this.startupTimer);
    clearTimeout(this.durationTimer);
    clearInterval(this.heartbeat);
    this.pending = [];
    this.pendingBytes = 0;
    this.pcmRemainder = Buffer.alloc(0);
    const socket = this.options.socket;
    if (socket.readyState === WebSocket.OPEN)
      socket.close(
        reason === "invalid_media_event" ? 1008 : 1000,
        "Call ended",
      );
    const forceClose = setTimeout(() => socket.terminate(), 1000).unref();
    socket.once("close", () => clearTimeout(forceClose));
    // Assign the promise before upstream callbacks can re-enter close().
    this.closing = Promise.resolve().then(async () => {
      // Abort the upstream before waiting for setup, so disconnect-during-start cannot leak a stream.
      await this.nova?.close().catch(() => this.event("nova_cleanup_failed"));
      await this.opening;
      if (this.sessionId) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        await Promise.race([
          this.transcriptWork,
          new Promise<void>((resolve) => {
            timer = setTimeout(() => {
              this.transcriptIncomplete = true;
              resolve();
            }, 2000);
          }),
        ]);
        clearTimeout(timer);
        if (this.transcriptIncomplete) {
          this.transcriptQueue = [];
          await this.options.backend
            .appendTranscripts?.(this.sessionId, [], true)
            .catch(() => this.event("transcript_status_failed"));
        }
        await this.options.backend
          .deleteSession(this.sessionId)
          .catch(() => this.event("session_cleanup_failed"));
      }
      this.event("call_disconnected", reason);
    });
    return this.closing;
  }
}
