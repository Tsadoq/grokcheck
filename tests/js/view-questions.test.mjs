import assert from "node:assert/strict";
import { test } from "node:test";

import { byText, find } from "./dom.mjs";

const { mountView } = await import("../../skills/grokcheck/web/view.js");
const { renderQuestion, renderReveal } = await import("../../skills/grokcheck/web/questions.js");
const { emptyReveal } = await import("../../skills/grokcheck/web/grading.js");

const ENCODE = { lane: "lane", x: "seq", key: "event", label: "event", ref_lane: "server" };
const server = ["1", "2"].map((event, at) => ({ lane: "server", seq: at + 1, event, _cell: `server|${event}`, _id: `server|${event}`, _link: event }));
const masked = server.map((one) => ({ _cell: `client|${one.event}`, _id: `client|${one.event}`, _masked: true, lane: "client", seq: one.seq }));
const frame = { type: "view", layout: "lanes", caption: "Which arrive?", encode: ENCODE, mask: { lane: "client" }, items: [...server, ...masked] };
const chips = (root) => find(root, (node) => node.classList?.contains("vchip") && node.classList.contains("masked"));

test("select_items on a section view: Answer on the view, live count, sorted cells", () => {
  mountView({ ...frame, id: "v-q", gate: "q1" }, {});
  let changes = 0;
  const rendered = renderQuestion({ id: "q1", type: "select_items", view: "v-q", candidates: masked.map((one) => one._cell) }, { onChange: () => changes++ });
  assert.equal(byText(rendered.element, "Answer on the view").length, 1);
  assert.equal(rendered.getResponse(), null);
  const count = rendered.element.querySelector(".view-count");
  assert.equal(count.textContent, "0 selected");
});

test("select_items with a frame renders the view in the card and reveals onto it", () => {
  const question = { id: "f1", type: "select_items", frame, candidates: masked.map((one) => one._cell) };
  const rendered = renderQuestion(question, {});
  const cells = chips(rendered.element);
  assert.equal(cells.length, 2);
  cells[1].click();
  cells[0].click();
  assert.deepEqual(rendered.getResponse(), ["client|1", "client|2"]);
  assert.equal(rendered.element.querySelector(".view-count").textContent, "2 selected");
  const reveal = renderReveal(question, { outcome: "partial", score: 0.5, response: ["client|1", "client|2"], reveal: emptyReveal("", { answer_cells: ["client|2"] }) });
  assert.ok(byText(reveal, "client|2").length >= 1);
});

test("fill_table counts filled blanks and returns cell values", () => {
  const view = {
    type: "view", id: "v-fill", layout: "table", caption: "Fill", fill: ["status"], choices: { status: [200, 409] }, gate: "q2",
    encode: { columns: [{ field: "case", label: "Case" }, { field: "status", label: "Status" }], key: "case" },
    items: [{ case: "a", _cell: "a", _id: "a", _link: "a", _masked: true }],
  };
  const { element } = mountView(view, {});
  const rendered = renderQuestion({ id: "q2", type: "fill_table", view: "v-fill", blanks: [{ cell: "a", field: "status" }] }, {});
  assert.equal(rendered.element.querySelector(".view-count").textContent, "0 of 1 filled");
  const picker = element.querySelector("select");
  picker.value = "1";
  picker.dispatch("change");
  assert.deepEqual(rendered.getResponse(), { a: { status: 409 } });
  assert.equal(rendered.element.querySelector(".view-count").textContent, "1 of 1 filled");
});

test("predict_state on a steps view offers a jump to its step", () => {
  mountView({ type: "view", id: "v-walk", layout: "steps", caption: "Walk", steps: [
    { code: { file: "a.py", language: "python", spans: [{ start_line: 1, text: "a" }] }, note: "One." },
    { code: { file: "a.py", language: "python", spans: [{ start_line: 2, text: "b" }] }, note: "Two." },
  ] }, {});
  const rendered = renderQuestion({ id: "q3", type: "predict_state", view: "v-walk", step: 1, options: [{ text: "x" }, { text: "y" }] }, {});
  assert.equal(byText(rendered.element, "Show step 2").length, 1);
});
