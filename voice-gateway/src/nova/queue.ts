/** Single-consumer queue: fail closed instead of accumulating delayed call audio. */
export class EventQueue implements AsyncIterable<Uint8Array> {
  private readonly items: Uint8Array[] = [];
  private bytes = 0;
  private ended = false;
  private wake: (() => void) | undefined;

  constructor(
    private readonly maxBytes = 256 * 1024,
    private readonly maxItems = 256,
  ) {}

  push(event: Record<string, unknown>): void {
    if (this.ended) throw new Error("Nova input stream is closed.");
    const bytes = Buffer.from(JSON.stringify({ event }));
    if (
      this.bytes + bytes.length > this.maxBytes ||
      this.items.length >= this.maxItems
    ) {
      throw new Error("Nova input buffer limit reached.");
    }
    this.items.push(bytes);
    this.bytes += bytes.length;
    this.wake?.();
    this.wake = undefined;
  }

  end(discard = false): void {
    this.ended = true;
    if (discard) {
      this.items.length = 0;
      this.bytes = 0;
    }
    this.wake?.();
    this.wake = undefined;
  }

  async *[Symbol.asyncIterator](): AsyncIterator<Uint8Array> {
    while (true) {
      const item = this.items.shift();
      if (item) {
        this.bytes -= item.length;
        yield item;
      } else if (this.ended) {
        return;
      } else {
        await new Promise<void>((resolve) => {
          this.wake = resolve;
        });
      }
    }
  }
}
