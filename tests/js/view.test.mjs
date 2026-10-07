import assert from "node:assert/strict";
import { test } from "node:test";

import { byText, find } from "./dom.mjs";

const { configureViews, fillText, inScenario, mountView, nextCell, setInput, viewById, xOrder } = await import("../../skills/grokcheck/web/view.js");

configureViews({
  kinds: [
    { id: "prompt", label: "prompt", colour: "accent" },
    { id: "text", label: "text", colour: "ok" },
  ],
  datasets: [{ id: "reconnect", kind: "run", script: ".grokcheck/drivers/reconnect.py", head: "a1b2c3d4e5", cited_lines_run: 23 }],
});

const ENCODE = {
  lane: "lane",
  x: "seq",
  key: "event",
  label: "text",
  class: "kind",
  ref_lane: "server",
  lanes: [
    { value: "server", label: "transcript has" },
    { value: "client", label: "browser received" },
  ],
};

function chip(lane, seq, event, extra = {}) {
  const cell = `${lane}|${event}`;
  return { lane, seq, event, text: `t${event}`, kind: event === "1" ? "prompt" : "text", _cell: cell, _id: cell, _link: event, _lost: false, _dup: false, _burst: false, ...extra };
}

function scenario(drop, lid) {
  const server = ["1", "2", "3"].map((event, at) => chip("server", at + 1, event, { drop, lid }));
  const live = server.slice(0, drop).map((one) => chip("client", one.seq, one.event, { drop, lid }));
  const lost = server.slice(drop, lid).map((one) => chip("client", one.seq, one.event, { drop, lid, _lost: true }));
  const burst = server.slice(lid).map((one) => chip("client", 9, one.event, { drop, lid, _burst: server.length - lid > 1 }));
  return [...server, ...live, ...lost, ...burst];
}

function lanesView(extra = {}) {
  return {
    type: "view",
    id: `v-${Math.random().toString(36).slice(2, 8)}`,
    layout: "lanes",
    data: "reconnect",
    caption: "Which events arrive?",
    encode: ENCODE,
    inputs: [
      { field: "drop", label: "Drop", control: "range", default: 1, values: [1, 2], follows: null, hint: "" },
      { field: "lid", label: "Last id", control: "range", default: 1, values: [1, 2], follows: "drop", hint: "" },
    ],
    items: [...scenario(1, 1), ...scenario(1, 2), ...scenario(2, 1), ...scenario(2, 2)],
    marks: [{ where: { lane: "client", _lost: true }, text: "lost {event}", tone: "bad", place: "status" }],
    layers: [{ id: "wire", label: "Show the wire", fields: ["seq"], on: false }],
    notes: [{ where: { lane: "client", _lost: true }, text: "Never sent." }],
    tasks: [{ text: "Lose one.", when: { _lost: true } }],
    ...extra,
  };
}

const chips = (root) => find(root, (node) => node.classList?.contains("vchip"));
const status = (root) => root.querySelector(".view-status").textContent;
const range = (root, at) => root.querySelectorAll("input")[at];

test("pure helpers: scenario filter, follows, mark text, x order, keyboard moves", () => {
  const items = [{ a: 1, b: 2 }, { a: 1, b: 3 }];
  assert.deepEqual(inScenario(items, ["b"], { b: 3 }), [items[1]]);
  const inputs = [{ field: "drop", values: [1, 2] }, { field: "lid", follows: "drop", values: [1, 2, 3] }];
  assert.deepEqual(setInput(inputs, { drop: 1, lid: 1 }, "drop", 2), { drop: 2, lid: 2 });
  assert.equal(fillText("lost {event} at {nope}", { event: "3" }), "lost 3 at {nope}");
  assert.deepEqual(xOrder([{ x: 3 }, { x: 1 }, { x: 2 }], "x"), [1, 2, 3]);
  const cells = [{ r: 0, c: 0 }, { r: 0, c: 16 }, { r: 1, c: 0 }, { r: 1, c: 32 }];
  assert.equal(nextCell(cells, cells[0], "ArrowRight"), cells[1]);
  assert.equal(nextCell(cells, cells[1], "ArrowDown"), cells[2]);
  assert.equal(nextCell(cells, cells[2], "End"), cells[3]);
  assert.equal(nextCell(cells, cells[3], "Home"), cells[2]);
  assert.equal(nextCell(cells, cells[0], "ArrowLeft"), cells[0]);
  assert.equal(nextCell(cells, cells[0], "x"), null);
});

test("lanes draw the current scenario with provenance, lost chips and labels", () => {
  const { element } = mountView(lanesView(), {});
  assert.match(element.querySelector(".provenance").textContent, /reconnect\.py at a1b2c3d, 23 cited lines ran/);
  assert.equal(chips(element).length, 6);
  assert.deepEqual(byText(element, "transcript has").length, 1);
  assert.equal(status(element), "");
});

test("inputs, follows and tasks: moving lid loses an event and meets the task once", () => {
  let completed = 0;
  const { element } = mountView(lanesView(), { onComplete: () => completed++ });
  range(element, 1).value = "1";
  range(element, 1).dispatch("input");
  const lost = chips(element).filter((one) => one.classList.contains("lost"));
  assert.equal(lost.length, 1);
  assert.match(lost[0].getAttribute("aria-label"), /lost/);
  assert.equal(status(element), "lost 2");
  assert.equal(completed, 1);
  range(element, 0).value = "1";
  range(element, 0).dispatch("input");
  assert.equal(range(element, 1).value, "1");
  assert.equal(completed, 1);
});

test("layers add fields to chips, notes highlight matched items", () => {
  const { element } = mountView(lanesView(), {});
  range(element, 1).value = "1";
  range(element, 1).dispatch("input");
  byText(element, "Show the wire")[0].click();
  assert.ok(byText(element, "seq 2").length >= 1);
  element.querySelector(".view-notes").querySelector("button").click();
  assert.equal(chips(element).filter((one) => one.classList.contains("noted")).length, 1);
});

test("selecting a chip shows its payload and links items in other views of the section", () => {
  const first = mountView(lanesView(), { sectionId: "s1" });
  const second = mountView(lanesView(), { sectionId: "s1" });
  chips(first.element)[0].click();
  assert.match(first.element.querySelector(".view-payload").textContent, /event: 1/);
  assert.doesNotMatch(first.element.querySelector(".view-payload").textContent, /_cell/);
  const linked = chips(second.element).filter((one) => one.classList.contains("linked"));
  assert.deepEqual(linked.map((one) => one.dataset.id), ["server|1", "client|1"]);
});

test("keyboard: one tab stop, arrows move it", () => {
  const { element } = mountView(lanesView(), {});
  const all = chips(element);
  assert.equal(all.filter((one) => one.tabIndex === 0).length, 1);
  all[0].dispatch("keydown", { key: "ArrowRight" });
  assert.equal(document.activeElement.dataset.id, "server|2");
  document.activeElement.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(document.activeElement.dataset.id, "client|1");
});

test("masked lanes: ? cells toggle as answers, the reveal swaps in items and outlines the key", () => {
  const full = scenario(1, 2);
  const placeholders = full.filter((one) => one.lane === "server").map((one) => ({ _cell: `client|${one.event}`, _id: `client|${one.event}`, _masked: true, lane: "client", seq: one.seq }));
  const view = { ...lanesView({ inputs: [], tasks: [], gate: "q1", mask: { lane: "client" } }), items: [...full.filter((one) => one.lane === "server"), ...placeholders] };
  const { element, handle } = mountView(view, {});
  assert.equal(viewById(view.id), handle);
  let changes = 0;
  const session = handle.answer({ candidates: placeholders.map((one) => one._cell) }, () => changes++);
  const masked = chips(element).filter((one) => one.classList.contains("masked"));
  assert.equal(masked.length, 3);
  masked[1].click();
  masked[2].click();
  assert.deepEqual(session.response(), ["client|2", "client|3"]);
  assert.equal(changes, 2);
  document.dispatchEvent(new CustomEvent("grokcheck:reveal", { detail: { gate: "q1", view: view.id, items: full } }));
  assert.equal(chips(element).filter((one) => one.classList.contains("masked")).length, 0);
  handle.showAnswer({ answer_cells: ["client|2"] }, ["client|2", "client|3"]);
  const marked = (name) => chips(element).filter((one) => one.classList.contains(name)).map((one) => one.dataset.id);
  assert.deepEqual(marked("answer"), ["client|2"]);
  assert.deepEqual(marked("wrong"), ["client|3"]);
});

test("table: masked fill cells become choice lists and the reveal shows right values", () => {
  const items = [
    { case: "past end", status: 409, _cell: "past end", _id: "past end", _link: "past end" },
    { case: "at end", _cell: "at end", _id: "at end", _link: "at end", _masked: true },
  ];
  const view = {
    type: "view", id: "v-table", layout: "table", caption: "Answers", gate: "q2", fill: ["status"], choices: { status: [200, 204, 409] },
    encode: { columns: [{ field: "case", label: "Case" }, { field: "status", label: "Status" }], key: "case" },
    notes: [{ where: { case: "past end" }, text: "Beyond." }], items,
  };
  const { element, handle } = mountView(view, {});
  assert.equal(element.querySelector("td.why").innerHTML.includes("Beyond."), true);
  const session = handle.answer({ blanks: [{ cell: "at end", field: "status" }] }, () => {});
  assert.equal(session.response(), null);
  const picker = element.querySelector("select");
  picker.value = "0";
  picker.dispatch("change");
  assert.deepEqual(session.response(), { "at end": { status: 200 } });
  handle.showAnswer({ cells: { "at end": { status: 204 } } }, session.response());
  assert.equal(element.querySelector(".fill-correct").textContent.trim(), "204");
});

test("blocks group chips by group with the group label", () => {
  const view = {
    type: "view", id: "v-blocks", layout: "blocks", caption: "Names", encode: { group: "g", key: "term", label: "term", class: "kind" },
    items: [
      { g: "server", term: "cursor", kind: "prompt", _cell: "server|cursor", _id: "server|cursor", _link: "cursor" },
      { g: "browser", term: "EventSource", kind: "text", _cell: "browser|EventSource", _id: "browser|EventSource", _link: "EventSource" },
    ],
  };
  const { element } = mountView(view, {});
  assert.equal(element.querySelectorAll(".vblock").length, 2);
  assert.equal(byText(element, "browser").length, 1);
});

test("decision lights the taken path from shared inputs and meets its task", () => {
  const owner = lanesView();
  mountView(owner, {});
  const items = [1, 2].flatMap((lid) => [
    { drop: 1, lid, node: "check", kind: "check", _cell: "check", _id: "check", _link: "check", _taken: true, answer: lid === 2 ? "yes" : "no" },
    { drop: 1, lid, node: "lost", kind: "outcome", _cell: "lost", _id: "lost", _link: "lost", _taken: lid === 2 },
  ]);
  const nodes = [
    { id: "check", label: "lid > drop?", kind: "check", code: { file: "a.py", language: "python", start_line: 3, text: "if x:" }, note: "First." },
    { id: "lost", label: "lost", kind: "outcome", tone: "bad", code: { file: "a.py", language: "python", start_line: 4, text: "lose()" }, note: "Lost.", example: { lid: 2 } },
  ];
  let completed = 0;
  const marked = [];
  const { element } = mountView(
    { type: "view", id: "v-dec", layout: "decision", caption: "Path", inputs: { from: owner.id }, nodes, items, tasks: [{ text: "Lose.", when: { node: "lost", _taken: true } }] },
    { onComplete: () => completed++, markLines: (file, lines) => marked.push([file, lines]) },
  );
  const node = (id) => element.querySelectorAll("button").find((one) => one.dataset.id === id);
  assert.match(node("check").getAttribute("aria-label"), /no$/);
  assert.equal(node("lost").classList.contains("chosen"), false);
  node("lost").click();
  assert.equal(node("lost").classList.contains("chosen"), true);
  assert.deepEqual(marked, [["a.py", [4, 4]]]);
  assert.equal(completed, 1);
});

test("steps: gated steps hide their note until the reveal, arrows step", () => {
  const steps = [
    { code: { file: "a.py", language: "python", spans: [{ start_line: 1, text: "a\nb" }, { start_line: 9, text: "c" }] }, note: "First.", link: ["x"] },
    { code: { file: "a.py", language: "python", spans: [{ start_line: 3, text: "d" }] }, note: "", link: ["y"] },
  ];
  const view = { type: "view", id: "v-steps", layout: "steps", caption: "Walk", gate: "q3", steps };
  const { element, handle } = mountView(view, {});
  assert.equal(element.querySelectorAll(".code-gap").length, 1);
  element.dispatch("keydown", { key: "ArrowRight" });
  assert.match(element.querySelector(".walk-note").textContent, /Predict before you look/);
  document.dispatchEvent(new CustomEvent("grokcheck:reveal", { detail: { gate: "q3", view: "v-steps", items: [], steps: [steps[0], { ...steps[1], note: "Second." }] } }));
  assert.match(element.querySelector(".walk-note").children[0].innerHTML, /Second\./);
  handle.goStep(0);
  assert.match(element.querySelector(".progress").textContent, /Step 1 of 2/);
});

test("split: one pane per value with a pane toggle", () => {
  const items = ["old", "new"].flatMap((pane) =>
    scenario(1, 1).map((one) => ({ ...one, _pane: pane, _cell: `${pane}|${one._cell}`, _id: `${pane}|${one._id}`, _differs: pane === "old" && one.lane === "client" })),
  );
  const { element } = mountView({ ...lanesView({ inputs: [], tasks: [] }), split: "version", items }, {});
  assert.equal(element.querySelectorAll(".view-pane").length, 2);
  byText(element, "old")[0].click();
  assert.equal(element.querySelectorAll(".view-pane").length, 1);
  assert.equal(byText(element, "version: old").length, 1);
  assert.ok(chips(element).some((one) => one.classList.contains("differs")));
});
