// Raw mono audio only: Twilio G.711 µ-law ↔ Nova signed PCM16LE, both 8 kHz.
// G.711 reference: https://www.itu.int/rec/T-REC-G.711
// Negative quantization follows WebRTC's ITU-bitexact G.711 implementation.
const BIAS = 0x84;
const MAX_MAGNITUDE = 32635;

/** Decode one µ-law byte per sample into little-endian signed 16-bit PCM. */
export function mulawToPcm16(payload: Buffer): Buffer {
  const pcm = Buffer.allocUnsafe(payload.length * 2);
  for (let i = 0; i < payload.length; i++) {
    const code = ~payload.readUInt8(i) & 0xff;
    const magnitude = (((code & 0x0f) << 3) + BIAS) << ((code >> 4) & 7);
    pcm.writeInt16LE(
      (code & 0x80) !== 0 ? BIAS - magnitude : magnitude - BIAS,
      i * 2,
    );
  }
  return pcm;
}

/** Encode complete PCM16LE samples without a WAV header or sample-rate change. */
export function pcm16ToMulaw(payload: Buffer): Buffer {
  if (payload.length % 2 !== 0) {
    throw new RangeError(
      "PCM16LE payload must contain complete two-byte samples",
    );
  }

  const mulaw = Buffer.allocUnsafe(payload.length / 2);
  for (let i = 0; i < mulaw.length; i++) {
    const sample = payload.readInt16LE(i * 2);
    const negative = sample < 0;
    // G.711 uses one's-complement magnitude for negative linear samples.
    const magnitude =
      Math.min(negative ? -sample - 1 : sample, MAX_MAGNITUDE) + BIAS;
    const exponent = 24 - Math.clz32(magnitude);
    const mantissa = (magnitude >> (exponent + 3)) & 0x0f;
    mulaw[i] = ((exponent << 4) | mantissa) ^ (negative ? 0x7f : 0xff);
  }
  return mulaw;
}
