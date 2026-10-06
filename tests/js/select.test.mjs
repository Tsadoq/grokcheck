import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { matches } from "../../skills/grokcheck/web/select.js";

const vectors = JSON.parse(readFileSync(new URL("../fixtures/selectors.json", import.meta.url), "utf-8"));

test("every shared selector vector matches as in Python", () => {
  assert.ok(vectors.match.length >= 40);
  for (const { selector, item, expect } of vectors.match) {
    assert.equal(matches(selector, item), expect, JSON.stringify({ selector, item }));
  }
});
