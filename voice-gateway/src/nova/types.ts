export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface ToolInvocation {
  toolName: string;
  toolUseId: string;
  input: unknown;
}

export interface NovaCallbacks {
  onAudio: (pcm: Buffer) => void;
  onInterrupted: () => void;
  onToolUse: (invocation: ToolInvocation) => Promise<unknown>;
  onError: (error: Error) => void;
  onClose: () => void;
}

export interface NovaSession {
  start(systemPrompt: string, tools: ToolDefinition[]): Promise<void>;
  sendAudio(pcm: Buffer): void;
  close(): Promise<void>;
}

export interface NovaFactory {
  create(callbacks: NovaCallbacks): NovaSession;
}
