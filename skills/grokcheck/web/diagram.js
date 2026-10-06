import { renderMarkdown } from "./markdown.js";
import { element } from "./questions.js";

const MERMAID_SRC = "/vendor/mermaid/mermaid.tiny.js";
const DARK = "(prefers-color-scheme: dark)";
const THEME_TOKENS = {
  background: "--bg",
  primaryColor: "--bg",
  primaryTextColor: "--fg",
  primaryBorderColor: "--accent",
  textColor: "--fg",
  lineColor: "--muted",
  secondaryColor: "--border",
  tertiaryColor: "--bg",
};

let loading = null;
let rendered = 0;

export function ensureMermaid() {
  loading ??= new Promise((resolve, reject) => {
    if (window.mermaid) {
      resolve(window.mermaid);
      return;
    }
    const script = document.createElement("script");
    script.src = MERMAID_SRC;
    script.addEventListener("load", () => resolve(window.mermaid));
    script.addEventListener("error", () => {
      loading = null;
      reject(new Error("Mermaid did not load."));
    });
    document.head.append(script);
  });
  return loading;
}

export function themeVariables() {
  const style = getComputedStyle(document.documentElement);
  const variables = Object.fromEntries(
    Object.entries(THEME_TOKENS).map(([name, token]) => [name, toHex(style.getPropertyValue(token).trim())]),
  );
  return { ...variables, darkMode: matchMedia(DARK).matches };
}

function toHex(colour) {
  const rgb = colour.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/);
  return rgb ? `#${rgb.slice(1, 4).map((part) => Number(part).toString(16).padStart(2, "0")).join("")}` : colour;
}

export function renderDiagram(item) {
  const canvas = element("div", { class: "diagram-canvas", "aria-live": "polite" });
  const draw = () => drawInto(canvas, item.mermaid);
  draw();
  matchMedia(DARK).addEventListener("change", draw);
  return element("figure", { class: "diagram-element", "data-kind": item.kind }, [
    canvas,
    element("figcaption", { html: renderMarkdown(item.caption) }),
    item.legend ? element("div", { class: "legend", html: renderMarkdown(item.legend) }) : "",
  ]);
}

async function drawInto(canvas, text) {
  try {
    const mermaid = await ensureMermaid();
    const theme = themeVariables();
    const natural = { useMaxWidth: false };
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: { ...theme, fontSize: "16px" },
      darkMode: theme.darkMode,
      flowchart: natural,
      sequence: natural,
      state: natural,
      class: natural,
      er: natural,
    });
    if (!(await mermaid.parse(text, { suppressErrors: true }))) {
      showSource(canvas, text, "Mermaid could not parse this diagram; its source follows.");
      return;
    }
    rendered += 1;
    const { svg } = await mermaid.render(`diagram-${rendered}`, text);
    canvas.innerHTML = svg;
  } catch (error) {
    showSource(canvas, text, `${error.message} Its source follows.`);
  }
}

function showSource(canvas, text, message) {
  canvas.replaceChildren(element("p", { class: "diagram-error", text: message }), element("pre", { text }));
}
