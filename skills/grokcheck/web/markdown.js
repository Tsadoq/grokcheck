import { escapeHtml, renderCode } from "./codeview.js";

const FENCE_OPEN = /^(`{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET_ITEM = /^\s*[-*+]\s+(.*)$/;
const ORDERED_ITEM = /^\s*\d+[.)]\s+(.*)$/;
const CONTINUATION = /^\s+\S/;
const INLINE_TOKEN = /`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^\s()<>"']+)\)/g;
const EMPHASIS_RULES = [
  [/\*\*(\S(?:.*?\S)?)\*\*/g, "<strong>$1</strong>"],
  [/__(\S(?:.*?\S)?)__/g, "<strong>$1</strong>"],
  [/\*(\S(?:[^*]*?\S)?)\*/g, "<em>$1</em>"],
  [/(^|[^\w])_(\S(?:[^_]*?\S)?)_(?!\w)/g, "$1<em>$2</em>"],
];

export function renderMarkdown(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const fence = line.match(FENCE_OPEN);
    const heading = line.match(HEADING);
    if (fence) {
      const closing = new RegExp(`^${fence[1]}\`*\\s*$`);
      const body = [];
      index += 1;
      while (index < lines.length && !closing.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(renderCode(body.join("\n"), fence[2]));
    } else if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
    } else if (BULLET_ITEM.test(line) || ORDERED_ITEM.test(line)) {
      const pattern = BULLET_ITEM.test(line) ? BULLET_ITEM : ORDERED_ITEM;
      const items = [];
      while (index < lines.length) {
        const item = lines[index].match(pattern);
        if (item) {
          items.push(item[1]);
        } else if (CONTINUATION.test(lines[index])) {
          items[items.length - 1] += ` ${lines[index].trim()}`;
        } else {
          break;
        }
        index += 1;
      }
      const tag = pattern === BULLET_ITEM ? "ul" : "ol";
      blocks.push(`<${tag}>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`);
    } else if (line.trim() === "") {
      index += 1;
    } else {
      const paragraph = [];
      while (index < lines.length && startsParagraphLine(lines[index])) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      blocks.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
    }
  }
  return blocks.join("\n");
}

function startsParagraphLine(line) {
  return (
    line.trim() !== "" &&
    !FENCE_OPEN.test(line) &&
    !HEADING.test(line) &&
    !BULLET_ITEM.test(line) &&
    !ORDERED_ITEM.test(line)
  );
}

function renderInline(text) {
  let html = "";
  let last = 0;
  for (const match of text.matchAll(INLINE_TOKEN)) {
    html += renderEmphasis(escapeHtml(text.slice(last, match.index)));
    const [, code, label, url] = match;
    html +=
      code !== undefined
        ? `<code>${escapeHtml(code)}</code>`
        : `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${renderEmphasis(escapeHtml(label))}</a>`;
    last = match.index + match[0].length;
  }
  return html + renderEmphasis(escapeHtml(text.slice(last)));
}

function renderEmphasis(escaped) {
  return EMPHASIS_RULES.reduce((html, [pattern, replacement]) => html.replace(pattern, replacement), escaped);
}
