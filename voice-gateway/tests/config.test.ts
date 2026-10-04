import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig } from "../src/config.js";

function environment(): NodeJS.ProcessEnv {
  return {
    PUBLIC_BASE_URL: "https://abe.example.test/",
    TWILIO_ACCOUNT_SID: `AC${"1".repeat(32)}`,
    TWILIO_AUTH_TOKEN: "synthetic-twilio-token",
    ABE_INTERNAL_TOKEN: "synthetic-internal-token-with-32-characters",
  };
}

test("configuration normalizes origins and supplies local-development defaults", () => {
  const config = loadConfig(environment());
  assert.equal(config.publicBaseUrl, "https://abe.example.test");
  assert.equal(config.backendUrl, "http://127.0.0.1:8000");
  assert.equal(config.port, 3000);
  assert.equal(config.region, "us-east-1");
  assert.equal(config.modelId, "amazon.nova-2-sonic-v1:0");
  assert.equal(config.voiceId, "tiffany");
});

test("remote HTTP is rejected while HTTPS and exact loopback HTTP are accepted", () => {
  for (const url of [
    "http://backend.example.test",
    "http://192.168.1.5",
    "http://127.0.0.1.example.test",
    "ftp://localhost",
  ]) {
    assert.throws(
      () => loadConfig({ ...environment(), ABE_BACKEND_URL: url }),
      /ABE_BACKEND_URL/,
    );
  }
  for (const url of [
    "https://backend.example.test",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://[::1]:8000",
  ]) {
    assert.equal(
      loadConfig({ ...environment(), ABE_BACKEND_URL: url }).backendUrl,
      url,
    );
  }
  assert.throws(
    () =>
      loadConfig({
        ...environment(),
        PUBLIC_BASE_URL: "http://localhost:3000",
      }),
    /PUBLIC_BASE_URL/,
  );
});

test("URL configuration rejects credentials, paths, queries and fragments", () => {
  for (const url of [
    "https://user:password@abe.example.test",
    "https://abe.example.test/path",
    "https://abe.example.test?token=secret",
    "https://abe.example.test#fragment",
  ]) {
    assert.throws(
      () => loadConfig({ ...environment(), PUBLIC_BASE_URL: url }),
      /PUBLIC_BASE_URL/,
    );
    assert.throws(
      () => loadConfig({ ...environment(), ABE_BACKEND_URL: url }),
      /ABE_BACKEND_URL/,
    );
  }
});

test("configuration errors name fields without exposing submitted secret values", () => {
  const input = {
    ...environment(),
    PUBLIC_BASE_URL: "https://private-user:private-password@abe.example.test",
    TWILIO_AUTH_TOKEN: "private-token",
    ABE_INTERNAL_TOKEN: "private-internal-token\nnot-a-valid-header",
  };
  assert.throws(
    () => loadConfig(input),
    (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.equal(
        error.message,
        "Invalid configuration: PUBLIC_BASE_URL, TWILIO_AUTH_TOKEN, ABE_INTERNAL_TOKEN",
      );
      assert.ok(!error.message.includes("private"));
      return true;
    },
  );
});

test("invalid ports, account IDs and missing credentials are rejected", () => {
  for (const port of ["0", "65536", "1.5", "not-a-port"]) {
    assert.throws(() => loadConfig({ ...environment(), PORT: port }), /PORT/);
  }
  assert.throws(
    () => loadConfig({ ...environment(), TWILIO_ACCOUNT_SID: "not-a-sid" }),
    /TWILIO_ACCOUNT_SID/,
  );
  assert.throws(
    () => loadConfig({}),
    /PUBLIC_BASE_URL.*TWILIO_ACCOUNT_SID.*TWILIO_AUTH_TOKEN.*ABE_INTERNAL_TOKEN/,
  );
});
