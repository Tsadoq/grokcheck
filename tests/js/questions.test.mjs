import assert from "node:assert/strict";
import { test } from "node:test";

import { moveStep } from "../../skills/grokcheck/web/questions.js";

test("moveStep moves a step up and leaves the order unchanged at the top edge", () => {
  assert.deepEqual(moveStep(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveStep(["a", "b", "c"], 0, -1), ["a", "b", "c"]);
});
