import assert from "node:assert/strict";
import { test } from "node:test";

class StubElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.classList = { add() {} };
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name.startsWith("data-")) {
      this.dataset[name.slice(5)] = value;
    }
  }

  append(...nodes) {
    this.children.push(...nodes.filter((node) => typeof node !== "string"));
  }

  prepend(...nodes) {
    this.children.unshift(...nodes.filter((node) => typeof node !== "string"));
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  querySelectorAll() {
    return [];
  }

  find(test) {
    return [this, ...this.children.flatMap((child) => child.find(test) ?? [])].find(test);
  }
}

globalThis.document = {
  createElement: (tag) => new StubElement(tag),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = {};
globalThis.location = { hash: "" };
globalThis.history = { replaceState: (_state, _title, url) => (globalThis.location.hash = url) };

const { createStepper, stepState } = await import("../../skills/grokcheck/web/stepper.js");

const shown = (curLine, queue, sentence) => ({ cur_line: curLine, state: { queue }, narration: [sentence] });

const TRACE = {
  type: "trace",
  trace_id: "reconnect",
  gate: "cp-predict",
  panels: [{ id: "queue", label: "Reader's queue", kind: "nullable_chips" }],
  versions: [
    {
      label: "before",
      steps: [
        shown(null, null, "The browser is away."),
        shown(41, [], "follow() creates an empty queue."),
        shown(45, [], "The log is never read."),
        { cur_line: 49 },
        { cur_line: 49 },
      ],
    },
    {
      label: "after",
      steps: [shown(41, [], "Subscribe first."), shown(43, [2, 3], "Replay the log.")],
    },
  ],
};

test("stepState clamps to the version bounds and marks gated steps", () => {
  const visible = stepState(TRACE, "before", 1);
  assert.deepEqual(
    visible,
    { index: 1, count: 5, curLine: 41, state: { queue: [] }, narration: ["follow() creates an empty queue."], gated: false },
    "a revealed step carries its line, state and narration",
  );
  assert.equal(stepState(TRACE, "before", 99).index, TRACE.versions[0].steps.length - 1, "an index past the end clamps to the last step");
  assert.equal(stepState(TRACE, "before", -4).index, 0, "a negative index clamps to the first step");
  const withheld = stepState(TRACE, "before", 3);
  assert.ok(withheld.gated && withheld.narration.length === 0, "a withheld step is gated with no narration");
  assert.equal(withheld.curLine, 49, "a withheld step still shows the line the run was on");
  assert.equal(stepState(TRACE, "after", 0).curLine, 41, "the version label picks the steps");
});

test("two steppers on one page keep their steps under separate hash keys", () => {
  const code = { language: "python", text: "a\nb\n", file: null, start_line: 1 };
  const trace = (id) => ({
    trace_id: id,
    title: id,
    panels: [],
    versions: [{ label: "only", code, steps: [shown(1, [], "One."), shown(2, [], "Two."), shown(1, [], "Three.")] }],
  });
  location.hash = "#step-first=3";

  const first = createStepper(trace("first"));
  const second = createStepper(trace("second"));
  second.find((node) => node.textContent === "Next step").listeners.click();

  const progress = (root) => root.find((node) => node.attributes.class === "progress").textContent;
  const params = new URLSearchParams(location.hash.slice(1));
  assert.equal(progress(first), "Step 3 of 3", "the first stepper should start at the step its own hash key names");
  assert.equal(progress(second), "Step 2 of 3", "the second stepper should ignore the first stepper's hash key");
  assert.equal(params.get("step-first"), "3", "stepping the second stepper must not overwrite the first stepper's step");
  assert.equal(params.get("step-second"), "2", "the second stepper should write its step under its own key");
});
