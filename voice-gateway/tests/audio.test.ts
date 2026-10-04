import assert from "node:assert/strict";
import test from "node:test";
import { mulawToPcm16, pcm16ToMulaw } from "../src/audio.js";

function pcm(samples: number[]): Buffer {
  const bytes = Buffer.alloc(samples.length * 2);
  samples.forEach((sample, i) => {
    bytes.writeInt16LE(sample, i * 2);
  });
  return bytes;
}

test("decodes G.711 silence, signs, segment boundaries and full-scale vectors", () => {
  // Standard reconstruction levels, independently specified from G.711 Table 2.
  const bytes = [
    0xff, 0x7f, 0xfe, 0x7e, 0xf0, 0x70, 0xef, 0x6f, 0xe7, 0x67, 0xd5, 0x55,
    0x90, 0x10, 0x8f, 0x0f, 0x80, 0x00,
  ];
  const samples = [
    0, 0, 8, -8, 120, -120, 132, -132, 260, -260, 716, -716, 15996, -15996,
    16764, -16764, 32124, -32124,
  ];
  assert.deepEqual(mulawToPcm16(Buffer.from(bytes)), pcm(samples));
});

test("encodes silence, signed quantization boundaries and saturation", () => {
  const samples = [
    0, 1, 3, 4, -1, -4, -5, 8, -8, 120, -120, 132, -132, 32124, -32124, 32767,
    -32768,
  ];
  const bytes = [
    0xff, 0xff, 0xff, 0xfe, 0x7f, 0x7f, 0x7e, 0xfe, 0x7e, 0xf0, 0x70, 0xef,
    0x6f, 0x80, 0x00, 0x80, 0x00,
  ];
  assert.deepEqual(pcm16ToMulaw(pcm(samples)), Buffer.from(bytes));
});

test("all 256 µ-law codes roundtrip, normalizing negative zero", () => {
  const codes = Buffer.from(Array.from({ length: 256 }, (_, i) => i));
  const expected = Buffer.from(codes);
  expected[0x7f] = 0xff;
  assert.deepEqual(pcm16ToMulaw(mulawToPcm16(codes)), expected);
});

test("all PCM16 values stay within the µ-law quantization error bound", () => {
  const samples = Array.from({ length: 65536 }, (_, i) => i - 32768);
  const reconstructed = mulawToPcm16(pcm16ToMulaw(pcm(samples)));
  for (let i = 0; i < samples.length; i++) {
    assert.ok(Math.abs(reconstructed.readInt16LE(i * 2) - (i - 32768)) <= 644);
  }
});

test("aligned streaming chunks produce the same audio as a single buffer", () => {
  const source = pcm([-32768, -1000, -5, -1, 0, 1, 4, 1000, 32767]);
  const original = Buffer.from(source);
  const encoded = pcm16ToMulaw(source);
  const chunked = Buffer.concat([
    pcm16ToMulaw(source.subarray(0, 6)),
    pcm16ToMulaw(source.subarray(6)),
  ]);
  assert.deepEqual(chunked, encoded);
  assert.deepEqual(
    Buffer.concat([
      mulawToPcm16(encoded.subarray(0, 3)),
      mulawToPcm16(encoded.subarray(3)),
    ]),
    mulawToPcm16(encoded),
  );
  assert.deepEqual(source, original);
});

test("handles empty frames and rejects truncated PCM samples", () => {
  assert.deepEqual(mulawToPcm16(Buffer.alloc(0)), Buffer.alloc(0));
  assert.deepEqual(pcm16ToMulaw(Buffer.alloc(0)), Buffer.alloc(0));
  assert.throws(
    () => pcm16ToMulaw(Buffer.from([0x01])),
    /complete two-byte samples/,
  );
});
