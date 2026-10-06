import assert from "node:assert/strict";
import { test } from "node:test";

class StubNode {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
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

  findAll(predicate) {
    return this.children.flatMap((child) => [...(predicate(child) ? [child] : []), ...child.findAll(predicate)]);
  }
}

globalThis.document = { createElement: (tag) => new StubNode(tag) };
globalThis.hljs = { getLanguage: () => false };

const { createPlayground, evaluate } = await import("../../skills/grokcheck/web/playground.js");

const state = (key, outcome, lost = 0) => ({
  key,
  cells: [["live", outcome === "complete" ? "live" : "lost"]],
  outcomes: [{ outcome, lost }],
  explain: `State ${key}.`,
});

const PLAYGROUND = {
  type: "playground",
  inputs: [
    { id: "drop", label: "Drops at", min: 1, max: 3, step: 1, value: 1 },
    { id: "back", label: "Back at", min: 2, max: 6, step: 2, value: 2 },
  ],
  constraints: [{ after: "back", gt: "drop" }],
  presets: [{ label: "Late return", values: { drop: 1, back: 6 } }],
  variants: ["old"],
  ticks: 2,
  rows: [{ id: "old", label: "old code" }],
  legend: [
    { cell: "live", label: "received live", colour: "good" },
    { cell: "lost", label: "never delivered", colour: "bad" },
  ],
  states: [
    state("1,2", "complete"),
    state("1,4", "lost", 1),
    state("1,6", "hang", 2),
    state("2,2", "complete"),
    state("2,4", "lost", 1),
    state("2,6", "hang", 2),
    state("3,2", "complete"),
    state("3,4", "complete"),
    state("3,6", "lost", 1),
  ],
  tasks: [
    { text: "Make it hang.", when: [{ variant: "old", outcome: "hang", min_lost: 0 }] },
    { text: "Lose an event without hanging.", when: [{ variant: "old", outcome: "lost", min_lost: 1 }] },
  ],
};

test("evaluate moves a constrained input onto its next value above and reports met tasks", () => {
  const result = evaluate(PLAYGROUND, { drop: 3, back: 2 });

  assert.deepEqual(result.values, { drop: 3, back: 4 }, "back must land on its own grid, above drop");
  assert.equal(result.state.key, "3,4");
  assert.deepEqual(result.met, [false, false]);
  assert.deepEqual(evaluate(PLAYGROUND, { drop: 3, back: 6 }).met, [false, true]);
  assert.deepEqual(evaluate(PLAYGROUND, { drop: 3, back: 6 }).values, { drop: 3, back: 6 }, "an input above its bound is left alone");
});

test("tasks stay met once reached and the playground completes exactly once", () => {
  let completions = 0;
  const view = createPlayground(PLAYGROUND, { onComplete: () => { completions += 1; } });
  const [drop, back] = view.findAll((node) => node.tagName === "INPUT");
  const tasks = view.findAll((node) => node.tagName === "LI");
  const preset = view.findAll((node) => node.tagName === "BUTTON")[0];

  assert.deepEqual(tasks.map((task) => task.attributes["data-met"]), ["false", "false"]);
  back.value = "4";
  back.listeners.input();
  assert.deepEqual(tasks.map((task) => task.attributes["data-met"]), ["false", "true"]);
  back.value = "2";
  back.listeners.input();
  assert.equal(tasks[1].attributes["data-met"], "true", "a met task must stay met after the sliders move away");
  assert.equal(completions, 0);

  preset.listeners.click();
  assert.equal(back.value, "6", "a preset should move the sliders");
  assert.equal(completions, 1);
  drop.value = "2";
  drop.listeners.input();
  assert.equal(completions, 1, "completion must be reported once");
});

test("a playground without tasks completes as soon as it renders", () => {
  let completions = 0;
  createPlayground({ ...PLAYGROUND, tasks: [] }, { onComplete: () => { completions += 1; } });

  assert.equal(completions, 1);
});
