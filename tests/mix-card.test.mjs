// Run: node --test tests/mix-card.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RAW_MAX,
  labelFromRaw,
  rawFromState,
  stateFromRaw,
} from "../custom_components/casambi_bt/casambi-mix-card.js";

test("label matches the app at the measured raw values", () => {
  assert.equal(labelFromRaw(0), "100% / 0%");
  assert.equal(labelFromRaw(31), "100% / 100%");
  assert.equal(labelFromRaw(42), "66% / 100%"); // scene "Late Evening Cozy"
  assert.equal(labelFromRaw(63), "0% / 100%");
});

test("raw values map to the number values HA showed on 2026-10-13", () => {
  for (const [raw, state] of [[0, 0], [31, 49], [42, 67], [63, 100]]) {
    assert.equal(stateFromRaw(raw), state);
    assert.equal(rawFromState(String(state)), raw);
  }
});

test("every raw value survives the way over the number entity", () => {
  for (let raw = 0; raw <= RAW_MAX; raw++) {
    assert.equal(rawFromState(String(stateFromRaw(raw))), raw);
  }
});

test("card is defined in the registry that exists once <home-assistant> is defined", async () => {
  // HA 2026.10 swaps in a scoped registry polyfill after this file has loaded.
  const registry = () => {
    const names = new Map();
    return { names, get: (n) => names.get(n), define: (n, c) => names.set(n, c) };
  };
  const native = registry();
  let haReady;
  native.whenDefined = () => new Promise((resolve) => (haReady = resolve));
  globalThis.window = globalThis;
  globalThis.customElements = native;
  await import("../custom_components/casambi_bt/casambi-mix-card.js?registry");
  assert.equal(native.names.size, 0, "defined before HA patched the registry");

  const polyfill = registry();
  globalThis.customElements = polyfill;
  haReady();
  await new Promise((resolve) => setTimeout(resolve));
  assert.ok(polyfill.get("casambi-mix-card"));
  assert.deepEqual(window.customCards.map((c) => c.type), ["casambi-mix-card"]);
  delete globalThis.customElements;
});
