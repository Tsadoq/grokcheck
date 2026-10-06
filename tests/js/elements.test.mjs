import assert from "node:assert/strict";
import { test } from "node:test";

class StubElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.innerHTML = "";
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name.startsWith("data-")) {
      this.dataset[name.slice(5).replace(/-(\w)/g, (_, char) => char.toUpperCase())] = value;
    }
  }

  append(...nodes) {
    this.children.push(...nodes.filter((node) => typeof node !== "string"));
  }

  prepend(...nodes) {
    this.children.unshift(...nodes.filter((node) => typeof node !== "string"));
  }

  addEventListener(type, handler) {
    this.listeners = { ...this.listeners, [type]: handler };
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

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }
}

globalThis.document = {
  createElement: (tag) => new StubElement(tag),
  createDocumentFragment: () => new StubElement("#fragment"),
};
globalThis.hljs = { getLanguage: () => false };

const { renderElements } = await import("../../skills/grokcheck/web/elements.js");

test("renders prose and code elements in authored order with depth markers", () => {
  const section = {
    id: "ordering",
    elements: [
      { type: "prose", depth: "short", markdown: "First." },
      { type: "prose", depth: "detail", markdown: "Second, in depth." },
      { type: "code", depth: "short", caption: "", code: { language: "python", text: "x = 1\n", file: null, start_line: 1 } },
    ],
  };

  const fragment = renderElements(section, { sectionId: section.id });

  assert.equal(fragment.children.length, 3);
  assert.equal(fragment.children[0].dataset.depth, undefined, "a short element must not carry a depth marker");
  assert.equal(fragment.children[1].dataset.depth, "detail");
  assert.equal(fragment.children[2].tagName, "FIGURE", "the code element should render as a code view");
  assert.throws(() => renderElements({ elements: [{ type: "nope" }] }), /unknown element type 'nope'/);
});

test("a lines claim cites a button that marks its lines, an unverified claim warns", () => {
  const marked = [];
  const section = {
    elements: [
      {
        type: "prose",
        depth: "short",
        markdown: "Reads move the key.",
        claims: [
          { text: "A hit moves the key.", verified: "supported", backing: { file: "src/cache.py", lines: [12, 14] } },
          { text: "Reads never block.", verified: "unchecked", backing: { reason: "not traced" } },
        ],
      },
    ],
  };

  const fragment = renderElements(section, { markLines: (file, lines) => marked.push([file, lines]) });

  const claims = fragment.children[0].children.find((child) => child.attributes.class === "claims");
  assert.ok(claims, "an element with claims should end in a .claims list");
  const [cited, unverified] = claims.children.map((item) => item.children[1]);
  assert.equal(cited.tagName, "BUTTON");
  cited.listeners.click();
  assert.deepEqual(marked, [["src/cache.py", [12, 14]]]);
  assert.equal(unverified.attributes.class, "badge warn");
  assert.equal(unverified.textContent, "unverified: not traced");
});

test("code element renders its caption as markdown below the code", () => {
  const section = {
    elements: [
      { type: "code", depth: "short", caption: "The **hot** path.", code: { language: "python", text: "x = 1\n", file: null, start_line: 1 } },
    ],
  };

  const view = renderElements(section).children[0];

  const caption = view.querySelector(".caption");
  assert.ok(caption, "a code element with a caption should append a .caption block");
  assert.match(caption.innerHTML, /<strong>hot<\/strong>/, "the caption should be rendered as markdown");
  assert.equal(view.children.at(-1), caption, "the caption should come after the code");
});

test("code element without a caption renders no caption block", () => {
  const section = {
    elements: [{ type: "code", depth: "short", caption: "", code: { language: "python", text: "x = 1\n", file: null, start_line: 1 } }],
  };

  assert.equal(renderElements(section).children[0].querySelector(".caption"), null, "an empty caption must not add a .caption block");
});

test("vocab element completes once min_opened distinct terms are opened, not on repeat clicks", () => {
  let completed = false;
  const term = (name, owner) => ({ term: name, owner, definition: `${name} means`, lines: [1], detail: "", library_term: null, false_friend: false });
  const section = {
    elements: [
      {
        type: "vocab",
        depth: "short",
        code: { language: "python", text: "x = 1\n", file: null, start_line: 1 },
        terms: [term("run", "ours"), term("stream", "library"), term("deque", "stdlib")],
        min_opened: 3,
      },
    ],
  };

  const fragment = renderElements(section, { onComplete: () => { completed = true; } });

  const rows = fragment.children[0].querySelectorAll(".term");
  rows[0].listeners.click();
  rows[0].listeners.click();
  rows[0].listeners.click();
  assert.equal(completed, false, "reopening the same term must not count towards min_opened");
  rows[1].listeners.click();
  assert.equal(completed, false, "two of three required terms must not complete the element");
  rows[2].listeners.click();
  assert.equal(completed, true, "the third distinct term should complete the element");
});

test("vocab element tags each term with its owner", () => {
  const section = {
    elements: [
      {
        type: "vocab",
        depth: "short",
        code: null,
        terms: [{ term: "stream", owner: "library", definition: "a stream", lines: [], detail: "", library_term: null, false_friend: false }],
        min_opened: 1,
      },
    ],
  };

  const row = renderElements(section).children[0].querySelector(".term");

  assert.match(row.querySelector(".owner").className, /\blibrary\b/, "the term row should carry an owner tag classed by its owner");
});
