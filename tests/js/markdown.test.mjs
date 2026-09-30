import assert from "node:assert/strict";
import { test } from "node:test";

import { renderMarkdown } from "../../skills/grokcheck/web/markdown.js";

test("renderMarkdown escapes raw HTML instead of passing it through", () => {
  const html = renderMarkdown('Hi <img src=x onerror="alert(1)"> and `<b>`');

  assert.ok(!html.includes("<img"), `raw tag survived: ${html}`);
  assert.ok(html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"), html);
  assert.ok(html.includes("<code>&lt;b&gt;</code>"), html);
});

test("renderMarkdown links only http and https URLs", () => {
  const html = renderMarkdown("[docs](https://example.com/a_b_c?x=1&y=2) [bad](javascript:alert(1))");

  assert.ok(
    html.includes('<a href="https://example.com/a_b_c?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">docs</a>'),
    html,
  );
  assert.ok(!html.includes('href="javascript'), `unsafe link rendered: ${html}`);
  assert.ok(html.includes("[bad](javascript:alert(1))"), html);
});

test("renderMarkdown renders headings, emphasis, lists and fenced code", () => {
  const html = renderMarkdown(
    "## Title\n\nSome **bold** and *soft* text\ncontinues here.\n\n- one\n- two\n\n1. first\n2. second\n\n```python\nx = '<y>'\n```",
  );

  assert.equal(
    html,
    [
      "<h2>Title</h2>",
      "<p>Some <strong>bold</strong> and <em>soft</em> text continues here.</p>",
      "<ul><li>one</li><li>two</li></ul>",
      "<ol><li>first</li><li>second</li></ol>",
      '<pre class="code"><code class="hljs"><span class="code-line">x = &#39;&lt;y&gt;&#39;</span></code></pre>',
    ].join("\n"),
  );
});
