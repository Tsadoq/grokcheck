import assert from "node:assert/strict";
import { test } from "node:test";

class StubElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.innerHTML = "";
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name.startsWith("data-")) {
      this.dataset[name.slice(5)] = value;
    }
  }

  getAttribute(name) {
    return this.attributes[name];
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

  get className() {
    return this.attributes.class ?? "";
  }

  querySelectorAll(selector) {
    const wanted = selector.slice(1);
    return this.children.flatMap((child) => [
      ...(child.className.split(" ").includes(wanted) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }

  text() {
    return [this.textContent, this.innerHTML, ...this.children.map((child) => child.text())].join(" ");
  }
}

const sectionAskBox = { value: "", focus() {} };

globalThis.document = {
  createElement: (tag) => {
    const node = new StubElement(tag);
    node.closest = () => ({ querySelector: () => sectionAskBox });
    return node;
  },
};

const { renderDiff } = await import("../../skills/grokcheck/web/diffview.js");

const DIFF = {
  type: "diff",
  id: "d1",
  file: "runs/stream.py",
  old_start: 44,
  new_start: 45,
  lines: [
    { op: " ", text: "        yield event" },
    { op: "-", text: "        pass" },
    { op: "+", text: '        if event.kind == "finished":' },
    { op: "+", text: "            return" },
  ],
  notes: [{ lines: [46, 47], cite: { file: "runs/stream.py", lines: [46, 47] }, text: "Stop once finished." }],
  asks: [{ line: 47, question: "Does returning here leave the queue registered?" }],
};

function rendered() {
  const marked = [];
  const asked = [];
  const view = renderDiff(DIFF, {
    markLines: (file, lines) => marked.push([file, lines]),
    askLine: async (elementId, line) => {
      asked.push([elementId, line]);
      return line === 47 ? { question: DIFF.asks[0].question, answer: "Yes, as written." } : {};
    },
  });
  const row = (line) => view.querySelectorAll(".diff-line").find((candidate) => candidate.dataset.line === String(line));
  return { view, marked, asked, row };
}

test("rows number the old and new sides, and only new-side rows are clickable", () => {
  const { view } = rendered();
  const rows = view.querySelectorAll(".diff-line");
  assert.deepEqual(
    rows.map((row) => [row.children[0].textContent, row.dataset.line ?? null]),
    [["45", "45"], ["45", null], ["46", "46"], ["47", "47"]],
  );
  assert.equal(view.querySelectorAll(".code-line").length, 3, "a removed line must not count as a new-side line");
});

test("clicking a note presses it and marks its lines", () => {
  const { view, marked } = rendered();
  const [note] = view.querySelectorAll(".diff-note");
  note.listeners.click();
  assert.equal(note.getAttribute("aria-pressed"), "true");
  assert.deepEqual(marked, [["runs/stream.py", [46, 47]]]);
});

test("clicking a line fetches its prepared ask, or prefills the section's ask box", async () => {
  const { view, marked, asked, row } = rendered();
  await row(47).listeners.click();
  assert.deepEqual(asked, [["d1", 47]]);
  assert.deepEqual(marked, [["runs/stream.py", [47, 47]]]);
  assert.equal(row(47).getAttribute("aria-pressed"), "true");
  assert.match(view.querySelectorAll(".diff-ask")[0].text(), /Yes, as written\./);

  await row(46).listeners.click();
  assert.equal(sectionAskBox.value, "About line 46: ");
});
