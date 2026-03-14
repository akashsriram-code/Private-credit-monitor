import test from "node:test";
import assert from "node:assert/strict";

import { consumeRateLimit } from "../lib/rate-limit.js";
import { bodySizeOkay, isAllowedOrigin, resolveAllowedOrigin, validatePayload } from "../lib/validation.js";

function validPayload() {
  return {
    event_name: "page_view",
    session_id: "session-123",
    page_path: "/Private-credit-monitor/",
    occurred_at: new Date().toISOString(),
    meta: {
      referrer_domain: "github.com",
      viewport_class: "desktop",
    },
  };
}

test("validatePayload accepts valid event payloads", () => {
  const result = validatePayload(validPayload());
  assert.equal(result.valid, true);
});

test("validatePayload rejects malformed payloads", () => {
  const payload = validPayload();
  payload.event_name = "bad_event";
  const result = validatePayload(payload);
  assert.equal(result.valid, false);
});

test("isAllowedOrigin enforces configured origins", () => {
  process.env.ALLOWED_ORIGINS = "https://akashsriram-code.github.io,https://example.com";
  assert.equal(isAllowedOrigin({ headers: { origin: "https://akashsriram-code.github.io" } }), true);
  assert.equal(isAllowedOrigin({ headers: { origin: "https://bad.example" } }), false);
  delete process.env.ALLOWED_ORIGINS;
});

test("resolveAllowedOrigin returns wildcard or matched origin", () => {
  delete process.env.ALLOWED_ORIGINS;
  assert.equal(resolveAllowedOrigin({ headers: { origin: "https://akashsriram-code.github.io" } }), "https://akashsriram-code.github.io");

  process.env.ALLOWED_ORIGINS = "https://akashsriram-code.github.io";
  assert.equal(resolveAllowedOrigin({ headers: { origin: "https://akashsriram-code.github.io" } }), "https://akashsriram-code.github.io");
  assert.equal(resolveAllowedOrigin({ headers: { origin: "https://bad.example" } }), "");
  delete process.env.ALLOWED_ORIGINS;
});

test("bodySizeOkay rejects oversized bodies", () => {
  assert.equal(bodySizeOkay("x".repeat(200)), true);
  assert.equal(bodySizeOkay("x".repeat(5000)), false);
});

test("consumeRateLimit survives bursty usage then blocks", () => {
  const now = Date.now();
  let allowedCount = 0;
  for (let index = 0; index < 121; index += 1) {
    const result = consumeRateLimit("burst-key", now);
    if (result.allowed) {
      allowedCount += 1;
    }
  }
  assert.equal(allowedCount, 120);
  assert.equal(consumeRateLimit("burst-key", now).allowed, false);
});
