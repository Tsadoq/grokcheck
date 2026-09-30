import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
globalThis.hljs = require("../../skills/grokcheck/web/vendor/highlight/highlight.min.js");

const { renderCode, splitHighlighted } = await import("../../skills/grokcheck/web/codeview.js");

test("splitHighlighted keeps a multi-line string token highlighted on every line", () => {
  const snippet = 'def f(x):\n    """multi\n    line doc"""\n    return x';
  const html = globalThis.hljs.highlight(snippet, { language: "python" }).value;

  const lines = splitHighlighted(html);

  assert.equal(lines.length, 4, `expected 4 lines, got ${JSON.stringify(lines)}`);
  for (const index of [1, 2]) {
    const line = lines[index];
    assert.ok(
      line.trimStart().startsWith('<span class="hljs-string">') && line.endsWith("</span>"),
      `line ${index + 1} lost its string highlight: ${line}`,
    );
  }
});

test("renderCode turns blank markers into labelled inputs, even inside a highlighted string", () => {
  const html = renderCode('key = "[[blank:name]]"', "python", { blanks: true });

  assert.ok(!html.includes("[[blank:"), `marker survived: ${html}`);
  assert.ok(
    html.includes('<input class="blank" type="text" data-blank="name" aria-label="Blank name"'),
    `no labelled input for blank "name": ${html}`,
  );
});

test("renderCode numbers selectable lines from 1 as pressable buttons", () => {
  const html = renderCode("a = 1\nb = 2\n", "python", { selectableLines: true });

  const lineNumbers = [...html.matchAll(/role="button" tabindex="0" aria-pressed="false" data-line="(\d+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(lineNumbers, ["1", "2"], html);
});
