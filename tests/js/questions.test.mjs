import assert from "node:assert/strict";
import { test } from "node:test";

class StubElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.innerHTML = "";
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  get disabled() {
    return "disabled" in this.attributes;
  }

  append(...nodes) {
    this.children.push(...nodes.filter((node) => typeof node !== "string"));
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  focus() {}

  find(label) {
    return [this, ...this.children.flatMap((child) => child.find(label) ?? [])].find((node) => node.attributes["aria-label"] === label);
  }
}

globalThis.document = { createElement: (tag) => new StubElement(tag) };

const { moveStep, renderQuestion } = await import("../../skills/grokcheck/web/questions.js");

test("moveStep moves a step up and leaves the order unchanged at the top edge", () => {
  assert.deepEqual(moveStep(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveStep(["a", "b", "c"], 0, -1), ["a", "b", "c"]);
});

test("parsons renderer returns text and indent per line in display order", () => {
  const rendered = renderQuestion({ id: "p", type: "parsons", prompt: "Assemble it.", lines: ["a", "b", "c"] });
  const click = (label) => rendered.element.find(label).listeners.click();

  click('Move "c" up');
  click('Move "c" up');
  click('Indent "c"');
  const response = rendered.getResponse();

  assert.equal(response.length, 3);
  assert.equal(response[0].text, "c");
  assert.equal(response[0].indent, 1);
});

const descendants = (node) => [node, ...node.children.flatMap(descendants)];

test("mutation_quiz renders one checkbox per test, labelled by the test name", () => {
  const rendered = renderQuestion({
    id: "m",
    type: "mutation_quiz",
    prompt: "Which tests fail?",
    mutation: { file: "cache.py", line: 12, replacement: "return None" },
    tests: [{ name: "test_hit" }, { name: "test_miss" }],
  });

  const boxes = descendants(rendered.element).filter((node) => node.tagName === "INPUT" && node.attributes.type === "checkbox");

  assert.deepEqual(
    boxes.map((box) => box.attributes.value),
    ["test_hit", "test_miss"],
    "each test of the mutation quiz needs its own checkbox carrying the test name",
  );
});

test("fix_the_bug renders a Run tests button and no response before a run", () => {
  const rendered = renderQuestion({
    id: "f",
    type: "fix_the_bug",
    prompt: "Fix it.",
    mutation: { file: "cache.py", line: 12 },
    worktree: "/tmp/wt",
    test_command: ["pytest", "-q"],
  });

  const buttons = descendants(rendered.element).filter((node) => node.tagName === "BUTTON");

  assert.deepEqual(buttons.map((button) => button.textContent), ["Run tests"], "fix_the_bug must offer exactly one Run tests button");
  assert.equal(rendered.getResponse(), null, "fix_the_bug must have no response until the tests have run");
});
