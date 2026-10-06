const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const HIGHLIGHT_TOKEN = /<span[^>]*>|<\/span>|\n/g;
const BLANK_MARKER = /\[\[blank:([^\]\s]+)\]\]/g;
const BLANK_PLACEHOLDER_BASE = 0xe000;

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

export function highlight(text, language) {
  const hljs = globalThis.hljs;
  if (!language || !hljs || !hljs.getLanguage(language)) {
    return escapeHtml(text);
  }
  return hljs.highlight(text, { language, ignoreIllegals: true }).value;
}

export function splitHighlighted(html) {
  const lines = [];
  const openTags = [];
  let line = "";
  let last = 0;
  for (const match of html.matchAll(HIGHLIGHT_TOKEN)) {
    line += html.slice(last, match.index);
    last = match.index + match[0].length;
    const token = match[0];
    if (token === "\n") {
      lines.push(line + "</span>".repeat(openTags.length));
      line = openTags.join("");
    } else {
      if (token === "</span>") {
        openTags.pop();
      } else {
        openTags.push(token);
      }
      line += token;
    }
  }
  lines.push(line + html.slice(last));
  return lines;
}

export function renderCode(text, language, { selectableLines = false, blanks = false } = {}) {
  const source = String(text).replace(/\r\n?/g, "\n").replace(/\n$/, "");
  const blankIds = [];
  // Each blank becomes one private-use character so highlight.js cannot split the marker across spans.
  const marked = blanks
    ? source.replace(BLANK_MARKER, (_, id) => {
        blankIds.push(id);
        return String.fromCharCode(BLANK_PLACEHOLDER_BASE + blankIds.length - 1);
      })
    : source;
  let html = highlight(marked, language);
  blankIds.forEach((id, index) => {
    html = html.replace(String.fromCharCode(BLANK_PLACEHOLDER_BASE + index), blankInput(id));
  });
  const lines = splitHighlighted(html).map((content, index) =>
    selectableLines
      ? `<span class="code-line" role="button" tabindex="0" aria-pressed="false" data-line="${index + 1}">${content}</span>`
      : `<span class="code-line">${content}</span>`,
  );
  return `<pre class="code"><code class="hljs">${lines.join("\n")}</code></pre>`;
}

function blankInput(id) {
  const safeId = escapeHtml(id);
  return `<input class="blank" type="text" data-blank="${safeId}" aria-label="Blank ${safeId}" autocomplete="off" spellcheck="false">`;
}
