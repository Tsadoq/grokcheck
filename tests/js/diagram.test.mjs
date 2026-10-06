import assert from "node:assert/strict";
import { test } from "node:test";

class StubElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.innerHTML = "";
    this.textContent = "";
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
}

const tokens = { "--bg": " rgb(13, 17, 23)", "--fg": "#e6edf3", "--accent": "rgb(68, 147, 248)", "--muted": "#9198a1", "--border": "#3d444d" };
globalThis.document = { createElement: (tag) => new StubElement(tag), documentElement: {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: (token) => tokens[token] ?? "" });
globalThis.matchMedia = () => ({ matches: true, addEventListener: () => {} });
globalThis.window = {};

const { renderDiagram, themeVariables } = await import("../../skills/grokcheck/web/diagram.js");

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("theme variables come from the page tokens as hex, in dark mode when the page is", () => {
  const theme = themeVariables();

  assert.equal(theme.background, "#0d1117", "an rgb() token should become hex");
  assert.equal(theme.primaryBorderColor, "#4493f8");
  assert.equal(theme.primaryTextColor, "#e6edf3", "a hex token should pass through");
  assert.equal(theme.darkMode, true);
});

test("a diagram renders as SVG, and one Mermaid cannot parse shows its source", async () => {
  window.mermaid = {
    initialize: () => {},
    parse: async (text) => !text.includes("broken"),
    render: async (id) => ({ svg: `<svg id="${id}"></svg>` }),
  };

  const good = renderDiagram({ mermaid: "flowchart TD\n A -->|x| B", caption: "Flow.", legend: "", kind: "flowchart" });
  const bad = renderDiagram({ mermaid: "flowchart TD\n broken", caption: "Flow.", legend: "", kind: "flowchart" });
  await settle();

  const [goodCanvas, badCanvas] = [good, bad].map((figure) => figure.children[0]);
  assert.match(goodCanvas.innerHTML, /^<svg /, "a parsed diagram should be drawn as SVG");
  const source = badCanvas.children.find((child) => child.tagName === "PRE");
  assert.ok(source, "an unparsable diagram should show its source in a <pre>");
  assert.equal(source.textContent, "flowchart TD\n broken");
});
