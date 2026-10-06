import { highlight, splitHighlighted } from "./codeview.js";
import { renderMarkdown } from "./markdown.js";
import { element } from "./questions.js";

const KINDS = { " ": "context", "+": "add", "-": "del" };
const MARKERS = { " ": " ", "+": "+", "-": "−" };

/** A hunk whose notes mark their lines and whose new-side lines fetch their prepared ask on click. */
export function renderDiff(item, context) {
  const askBox = element("div", { class: "diff-ask", "aria-live": "polite" }, [
    element("span", { class: "status", text: "Click a line to see what a reviewer would ask about it." }),
  ]);
  const rows = diffRows(item);
  const lineRows = rows.filter((row) => row.dataset.line);
  const notes = item.notes.map((note) =>
    element("button", { type: "button", class: "diff-note", "aria-pressed": "false" }, [
      element("span", { class: "cite", text: `${note.cite.file}:${note.lines[0]}-${note.lines[1]}` }),
      element("div", { html: renderMarkdown(note.text) }),
    ]),
  );
  notes.forEach((button, index) =>
    button.addEventListener("click", () => {
      notes.forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
      lineRows.forEach((row) => row.setAttribute("aria-pressed", "false"));
      context.markLines?.(item.file, item.notes[index].lines);
    }),
  );

  const root = element("figure", { class: "code-view diff", "data-file": item.file, "data-start": item.new_start }, [
    element("figcaption", { text: item.file }),
    element("div", { class: "diff-split" }, [
      element("pre", { class: "code" }, [element("code", { class: "hljs" }, rows)]),
      notes.length ? element("div", { class: "diff-notes" }, notes) : "",
    ]),
    askBox,
  ]);

  const openLine = async (row) => {
    const line = Number(row.dataset.line);
    notes.forEach((note) => note.setAttribute("aria-pressed", "false"));
    lineRows.forEach((other) => other.setAttribute("aria-pressed", String(other === row)));
    context.markLines?.(item.file, [line, line]);
    let ask;
    try {
      ask = await context.askLine(item.id, line);
    } catch (error) {
      askBox.replaceChildren(element("span", { class: "status", text: `Could not load line ${line}: ${error.message}` }));
      return;
    }
    if (ask.question) {
      askBox.replaceChildren(
        element("span", { class: "eyebrow", text: `About line ${line}` }),
        element("b", { text: ask.question }),
        element("div", { html: renderMarkdown(ask.answer) }),
      );
      return;
    }
    const box = root.closest?.(".lesson-section")?.querySelector("aside.ask textarea");
    const hint = box ? " Type your own below." : "";
    askBox.replaceChildren(element("span", { class: "status", text: `No prepared question for line ${line}.${hint}` }));
    if (box) {
      box.value = `About line ${line}: `;
      box.focus();
    }
  };
  for (const row of lineRows) {
    row.addEventListener("click", () => openLine(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openLine(row);
      }
    });
  }
  return root;
}

function diffRows(item) {
  const html = splitHighlighted(highlight(item.lines.map((line) => line.text).join("\n"), item.file.split(".").pop()));
  let oldLine = item.old_start;
  let newLine = item.new_start;
  return item.lines.map(({ op }, index) => {
    const number = op === "-" ? oldLine : newLine;
    oldLine += op === "+" ? 0 : 1;
    newLine += op === "-" ? 0 : 1;
    const cells = [
      element("span", { class: "n", text: String(number) }),
      element("span", { class: "m", text: MARKERS[op] }),
      element("span", { class: "t", html: html[index] }),
    ];
    return op === "-"
      ? element("span", { class: "diff-line del" }, cells)
      : element(
          "span",
          { class: `diff-line code-line ${KINDS[op]}`, role: "button", tabindex: 0, "aria-pressed": "false", "data-line": number },
          cells,
        );
  });
}
