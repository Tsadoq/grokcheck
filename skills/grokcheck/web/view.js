import { renderCode } from "./codeview.js";
import { renderMarkdown } from "./markdown.js";
import { matches } from "./select.js";

const REVEAL_EVENT = "grokcheck:reveal";
const LINK_EVENT = "grokcheck:link";
const GATED_NOTE = "Predict before you look: answer the checkpoint below to see this step.";
const STACK_SLOTS = 16;

const kinds = new Map();
const datasets = new Map();
const handles = new Map();
const stores = new Map();

export function configureViews(lesson) {
  kinds.clear();
  datasets.clear();
  for (const kind of lesson.kinds ?? []) {
    kinds.set(kind.id, kind);
  }
  for (const dataset of lesson.datasets ?? []) {
    datasets.set(dataset.id, dataset);
  }
}

export function viewById(id) {
  return handles.get(id) ?? null;
}

export function createView(item, context = {}) {
  return mountView(item, context).element;
}

export function inScenario(items, fields, values) {
  return items.filter((item) => fields.every((field) => (item[field] ?? null) === values[field]));
}

export function inputValues(inputs) {
  return Object.fromEntries(inputs.map((input) => [input.field, input.default ?? input.values[0]]));
}

export function setInput(inputs, values, field, value) {
  const next = { ...values, [field]: value };
  for (const input of inputs) {
    if (input.follows === field && input.values.includes(value)) {
      next[input.field] = value;
    }
  }
  return next;
}

export function fillText(text, item) {
  return text.replace(/\{(\w+)\}/g, (whole, field) => (item && field in item ? String(item[field]) : whole));
}

export function paneValues(items) {
  return [...new Set(items.map((item) => item._pane).filter((pane) => pane !== undefined))];
}

export function laneOrder(items, encode) {
  const seen = (encode.lanes ?? []).map((lane) => lane.value);
  for (const item of items) {
    if (!seen.includes(item[encode.lane])) {
      seen.push(item[encode.lane]);
    }
  }
  return seen;
}

export function xOrder(items, field) {
  const xs = [...new Set(items.map((item) => item[field] ?? item.x))];
  return xs.every((x) => typeof x === "number") ? xs.sort((a, b) => a - b) : xs;
}

export function nextCell(cells, current, key) {
  const row = cells.filter((cell) => cell.r === current.r).sort((a, b) => a.c - b.c);
  const rows = [...new Set(cells.map((cell) => cell.r))].sort((a, b) => a - b);
  const nearest = (r) =>
    cells.filter((cell) => cell.r === r).reduce((best, cell) => (Math.abs(cell.c - current.c) < Math.abs(best.c - current.c) ? cell : best));
  const at = rows.indexOf(current.r);
  switch (key) {
    case "ArrowRight":
      return row.find((cell) => cell.c > current.c) ?? current;
    case "ArrowLeft":
      return row.findLast((cell) => cell.c < current.c) ?? current;
    case "ArrowDown":
      return at < rows.length - 1 ? nearest(rows[at + 1]) : current;
    case "ArrowUp":
      return at > 0 ? nearest(rows[at - 1]) : current;
    case "Home":
      return row[0];
    case "End":
      return row.at(-1);
    default:
      return null;
  }
}

function h(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "html") {
      node.innerHTML = value;
    } else if (name === "text") {
      node.textContent = value;
    } else if (value !== false && value !== undefined && value !== null) {
      node.setAttribute(name, value === true ? "" : String(value));
    }
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== ""));
  return node;
}

function show(value) {
  return value === null || value === undefined ? "null" : typeof value === "string" ? value : JSON.stringify(value);
}

function kindOf(item, encode) {
  const id = encode?.class ? item[encode.class] : undefined;
  const kind = kinds.get(id);
  return { id, label: kind?.label ?? (id === undefined ? "" : String(id)), tone: kind?.colour ?? "accent" };
}

function publicFields(item) {
  return Object.entries(item).filter(([field]) => !field.startsWith("_"));
}

function store(id) {
  if (!stores.has(id)) {
    stores.set(id, { fields: [], values: {}, listeners: new Set() });
  }
  return stores.get(id);
}

function provenance(item) {
  const dataset = datasets.get(item.data);
  if (!item.id || !dataset) {
    return null;
  }
  const head = dataset.head ? [" at ", h("code", { text: dataset.head.slice(0, 7) })] : [];
  const ran = dataset.cited_lines_run ? `, ${dataset.cited_lines_run} cited lines ran.` : ".";
  const parts = dataset.script
    ? ["Recorded from ", h("code", { text: dataset.script }), ...head, ran]
    : dataset.trace
      ? ["Recorded from the trace ", h("code", { text: dataset.trace }), ...head, ran]
      : [`Written by hand from cited lines${(dataset.cited ?? []).length ? `: ${dataset.cited.join(", ")}` : ""}, each row checked against the code.`];
  return h("p", { class: "provenance" }, parts);
}

function codePanel(code, current = null) {
  const spans = code.spans ?? [{ start_line: code.start_line ?? 1, text: code.text ?? "" }];
  const parts = [];
  spans.forEach((span, index) => {
    if (index > 0) {
      parts.push(h("span", { class: "code-gap", "aria-hidden": "true", text: "⋮" }));
    }
    const block = h("div", { style: `--line-offset: ${span.start_line - 1}`, html: renderCode(span.text, code.language) });
    if (current !== null) {
      block.querySelectorAll?.(".code-line").forEach((line, at) => line.classList.toggle("current", span.start_line + at === current));
    }
    parts.push(block);
  });
  return h("figure", { class: "code-view view-code" }, [code.file ? h("figcaption", { text: code.file }) : null, ...parts]);
}

function spanRange(code) {
  const spans = code.spans ?? [{ start_line: code.start_line ?? 1, text: code.text ?? "" }];
  const last = spans.at(-1);
  return [spans[0].start_line, last.start_line + last.text.replace(/\n$/, "").split("\n").length - 1];
}

export function mountView(item, context = {}) {
  const encode = item.encode ?? {};
  const own = Array.isArray(item.inputs) ? item.inputs : [];
  const source = item.inputs?.from ?? (own.length ? item.id : null);
  const shared = source ? store(source) : null;
  if (own.length) {
    shared.fields = own.map((input) => input.field);
    shared.values = inputValues(own);
    shared.inputs = own;
  }
  const state = {
    items: item.items ?? [],
    steps: item.steps ?? [],
    selected: null,
    linked: new Set(),
    noteFocus: null,
    layers: new Set((item.layers ?? []).filter((layer) => layer.on).map((layer) => layer.id)),
    pane: null,
    answering: null,
    answer: null,
    step: 0,
    revealed: false,
    focusId: null,
    scenarioKey: null,
  };
  const met = (item.tasks ?? []).map(() => false);
  let done = false;

  const values = () => shared?.values ?? {};
  const fields = () => shared?.fields ?? [];
  const current = () => inScenario(state.items, fields(), values());

  const status = h("div", { class: "view-status", role: "status", "aria-live": "polite" });
  const legend = h("div", { class: "view-legend" });
  const body = h("div", { class: "view-body" });
  const detail = h("div", { class: "view-detail", "aria-live": "polite" });
  const notes = h("ul", { class: "view-notes" });
  const tasks = h("ol", { class: "playground-tasks view-tasks" });
  const root = h("div", { class: `view layout-${item.layout}`, id: item.id, "data-view": item.id ?? "", "data-layout": item.layout }, [
    item.caption ? h("div", { class: "view-caption", html: renderMarkdown(item.caption) }) : null,
    provenance(item),
  ]);

  const controls = own.length ? inputControls(own, shared, () => notify()) : null;
  const notify = () => shared.listeners.forEach((listener) => listener());
  if (controls) {
    root.append(controls.element);
  }
  if (item.presets?.length) {
    root.append(
      h("div", { class: "playground-presets view-presets" }, [
        h("span", { text: "Try:" }),
        ...item.presets.map((preset) => {
          const button = h("button", { type: "button", class: "chip", text: preset.label });
          button.addEventListener("click", () => {
            shared.values = { ...shared.values, ...preset.values };
            notify();
          });
          return button;
        }),
      ]),
    );
  }
  const toggles = [];
  if (item.layers?.length) {
    toggles.push(
      ...item.layers.map((layer) => {
        const button = h("button", { type: "button", class: "view-toggle", "aria-pressed": String(state.layers.has(layer.id)), text: layer.label });
        button.addEventListener("click", () => {
          state.layers.has(layer.id) ? state.layers.delete(layer.id) : state.layers.add(layer.id);
          button.setAttribute("aria-pressed", String(state.layers.has(layer.id)));
          draw();
        });
        return button;
      }),
    );
  }
  const panes = item.split ? paneValues(state.items) : [];
  if (panes.length > 1) {
    const choices = [[null, "Side by side"], ...panes.map((pane) => [pane, String(pane)])];
    const buttons = choices.map(([pane, label]) => {
      const button = h("button", { type: "button", "aria-pressed": String(pane === state.pane), text: label });
      button.addEventListener("click", () => {
        state.pane = pane;
        buttons.forEach((other, at) => other.setAttribute("aria-pressed", String(choices[at][0] === pane)));
        draw();
      });
      return button;
    });
    toggles.unshift(h("div", { class: "toggle", role: "group", "aria-label": `Show ${item.split}` }, buttons));
  }
  if (toggles.length) {
    root.append(h("div", { class: "view-toggles" }, toggles));
  }
  root.append(status, legend, body, detail, notes, tasks);

  const select = (target) => {
    if (state.answering?.candidates && state.answering.candidates.has(target._cell)) {
      const picked = state.answering.picked;
      picked.has(target._cell) ? picked.delete(target._cell) : picked.add(target._cell);
      state.focusId = target._id;
      draw();
      state.answering.onChange();
      return;
    }
    state.selected = target._id;
    state.focusId = target._id;
    state.linked = new Set(target._link === undefined ? [] : [target._link]);
    draw();
    if (context.sectionId) {
      document.dispatchEvent(new CustomEvent(LINK_EVENT, { detail: { section: context.sectionId, from: item.id, links: [...state.linked] } }));
    }
  };

  const onLink = ({ detail: link }) => {
    if (link.section === context.sectionId && link.from !== item.id) {
      state.linked = new Set(link.links);
      state.selected = null;
      draw();
    }
  };
  if (context.sectionId) {
    document.addEventListener(LINK_EVENT, onLink);
  }
  if (item.gate) {
    const onReveal = ({ detail: payload }) => {
      if (payload.gate === item.gate) {
        document.removeEventListener(REVEAL_EVENT, onReveal);
        state.revealed = true;
        state.items = payload.items ?? state.items;
        state.steps = payload.steps ?? state.steps;
        draw();
      }
    };
    document.addEventListener(REVEAL_EVENT, onReveal);
  }

  const chipState = (target) => {
    const picked = state.answering?.picked ?? state.answer?.picked;
    const pressed = picked ? picked.has(target._cell) : state.selected === target._id;
    const answer = state.answer?.cells instanceof Set ? state.answer : null;
    const classes = [
      target._lost ? "lost" : "",
      target._dup ? "dup" : "",
      target._burst ? "burst" : "",
      target._masked ? "masked" : "",
      target._differs && !state.items.some((one) => one._masked) ? "differs" : "",
      state.linked.has(target._link) ? "linked" : "",
      state.noteFocus !== null && matches(item.notes[state.noteFocus].where, target) ? "noted" : "",
      answer?.cells.has(target._cell) ? "answer" : "",
      answer && answer.picked.has(target._cell) && !answer.cells.has(target._cell) ? "wrong" : "",
      state.answering?.candidates?.has(target._cell) ? "candidate" : "",
    ];
    return { pressed, classes: classes.filter(Boolean).join(" ") };
  };

  const layerLines = (target) =>
    (item.layers ?? [])
      .filter((layer) => state.layers.has(layer.id) && !target._masked && matches(layer.where ?? {}, target))
      .flatMap((layer) => layer.fields.map((field) => `${field} ${show(target[field])}`));

  const describe = (target, laneLabel) => {
    if (target._masked) {
      return `unknown, ${laneLabel}, hidden until you answer`;
    }
    const kind = kindOf(target, encode);
    const repeat = target._dup ? `, repeat ×${repeatCount(target)}` : "";
    return [kind.label, show(target[encode.label ?? encode.key]), laneLabel, target._lost ? "lost" : "", target._burst ? "stacked" : ""]
      .filter(Boolean)
      .join(", ") + repeat;
  };

  const chip = (target, laneLabel, position) => {
    const kind = kindOf(target, encode);
    const { pressed, classes } = chipState(target);
    const flag = target._lost ? "lost" : target._dup ? `×${repeatCount(target)}` : "";
    const button = h(
      "button",
      {
        type: "button",
        class: `vchip tone-${kind.tone} ${classes}`,
        "data-id": target._id,
        "data-r": position.r,
        "data-c": position.c,
        tabindex: -1,
        "aria-pressed": String(pressed),
        "aria-label": describe(target, laneLabel),
      },
      target._masked
        ? [h("span", { class: "vk", text: "?" })]
        : [
            h("span", { class: "vk", text: show(target[encode.key]) }),
            encode.label && encode.label !== encode.key ? h("span", { class: "vl", text: show(target[encode.label]) }) : null,
            kind.label ? h("span", { class: "vc", text: kind.label }) : null,
            flag ? h("span", { class: "vf", text: flag }) : null,
            ...layerLines(target).map((line) => h("span", { class: "vlayer", text: line })),
          ],
    );
    button.addEventListener("click", () => select(target));
    return button;
  };

  const LAYOUTS = {
    lanes: (items, pane, offset) => drawLanes(items, pane, offset),
    table: (items, pane, offset) => drawTable(items, offset),
    blocks: (items, pane, offset) => drawBlocks(items, offset),
    decision: (items, pane, offset) => drawDecision(items, offset),
    steps: () => drawSteps(),
  };

  function drawLanes(items, pane, offset) {
    const all = current();
    const xs = xOrder(all, encode.x);
    const lanes = laneOrder(all, encode);
    const labelOf = (value) => encode.lanes?.find((lane) => lane.value === value)?.label ?? String(value);
    const xOf = (target) => target[encode.x] ?? target.x;
    const laneOf = (target) => target[encode.lane] ?? target.lane;
    const columnMarks = new Map();
    for (const mark of (item.marks ?? []).filter((one) => one.place === "column")) {
      const hit = items.find((target) => !target._masked && matches(mark.where, target));
      if (hit) {
        columnMarks.set(xOf(hit), [...(columnMarks.get(xOf(hit)) ?? []), mark]);
      }
    }
    const refXs = new Set(items.filter((target) => laneOf(target) === encode.ref_lane).map(xOf));
    const grid = h("div", { class: "lanes-grid", style: `grid-template-columns: var(--lane-label) repeat(${xs.length}, max-content)` });
    grid.append(h("div", { class: "lane-label" }));
    xs.forEach((x) => {
      const marks = columnMarks.get(x) ?? [];
      grid.append(
        h("div", { class: `phase${marks.length ? "" : " empty"}`, "data-x": show(x) }, marks.map((mark) => h("span", { class: `tone-${mark.tone ?? "muted"}`, text: fillText(mark.text, items.find((target) => matches(mark.where, target))) }))),
      );
    });
    lanes.forEach((lane, laneIndex) => {
      grid.append(h("div", { class: "lane-label", text: labelOf(lane) }));
      xs.forEach((x, xIndex) => {
        const here = items.filter((target) => laneOf(target) === lane && xOf(target) === x);
        const gap = encode.ref_lane !== undefined && !refXs.has(x);
        grid.append(
          h(
            "div",
            {
              class: `stack${gap ? " col-gap" : ""}`,
              "data-x": show(x),
              "data-burst": here.some((target) => target._burst) ? "true" : false,
              "data-lost": here.some((target) => target._lost) ? "true" : false,
            },
            here.map((target, k) => chip(target, labelOf(lane), { r: offset + laneIndex, c: xIndex * STACK_SLOTS + k })),
          ),
        );
      });
    });
    return h("div", { class: "view-scroll", tabindex: 0, role: "region", "aria-label": `${stripMarkdown(item.caption)}: scrolls sideways` }, [grid]);
  }

  function drawTable(items, offset) {
    const columns = encode.columns ?? [];
    const withNotes = (item.notes ?? []).length > 0;
    const fill = new Set(item.fill ?? []);
    const answering = state.answering?.blanks ? state.answering : null;
    const cellValue = (target, field) => {
      const key = `${target._cell}`;
      const masked = target._masked && fill.has(field) && !(field in target && target[field] !== null);
      if (answering && masked && answering.blanks.some((blank) => blank.cell === key && blank.field === field)) {
        return fillPicker(target, field);
      }
      const given = state.answer?.response?.[key]?.[field];
      const right = state.answer?.cells?.[key]?.[field];
      if (state.answer && right !== undefined) {
        const ok = given !== undefined && JSON.stringify(given) === JSON.stringify(right);
        return h("span", { class: ok ? "fill-right" : "fill-wrong" }, [
          given === undefined ? h("span", { class: "fill-given", text: "blank" }) : h(ok ? "span" : "s", { class: "fill-given", text: show(given) }),
          ok ? null : h("span", { class: "fill-correct", text: ` ${show(right)}` }),
        ]);
      }
      return masked ? h("span", { class: "fill-masked", text: "?" }) : h("span", { text: show(target[field]) });
    };
    const rows = items.map((target, index) => {
      const { pressed, classes } = chipState(target);
      const kind = kindOf(target, encode);
      const key = h(
        "button",
        { type: "button", class: `row-key tone-${kind.tone}`, "data-id": target._id, "data-r": offset + index, "data-c": 0, tabindex: -1, "aria-pressed": String(pressed), "aria-label": `${describe(target, "row")}` },
        [h("span", { text: show(target[encode.key]) }), kind.label ? h("span", { class: "vc", text: kind.label }) : null, ...layerLines(target).map((line) => h("span", { class: "vlayer", text: line }))],
      );
      key.addEventListener("click", () => select(target));
      const note = withNotes ? (item.notes.find((one) => matches(one.where, target))?.text ?? "") : null;
      return h("tr", { class: classes }, [
        h("th", { scope: "row" }, [key]),
        ...columns.filter((column) => column.field !== encode.key).map((column) => h("td", {}, [cellValue(target, column.field)])),
        withNotes ? h("td", { class: "why", html: renderMarkdown(note) }) : null,
      ]);
    });
    const head = [encode.key, ...columns.filter((column) => column.field !== encode.key).map((column) => column.field)];
    const label = (field) => columns.find((column) => column.field === field)?.label ?? field;
    return h("div", { class: "view-scroll", tabindex: 0, role: "region", "aria-label": `${stripMarkdown(item.caption)}: scrolls sideways` }, [
      h("table", { class: "view-table" }, [
        h("thead", {}, [h("tr", {}, [...head.map((field) => h("th", { scope: "col", text: label(field) })), withNotes ? h("th", { scope: "col", text: "Why" }) : null])]),
        h("tbody", {}, rows),
      ]),
    ]);
  }

  function fillPicker(target, field) {
    const options = item.choices?.[field] ?? [];
    const picker = h("select", { "aria-label": `${field} for ${show(target[encode.key])}` }, [
      h("option", { value: "", text: "?" }),
      ...options.map((option, index) => h("option", { value: String(index), text: show(option) })),
    ]);
    const chosen = state.answering.fills[target._cell]?.[field];
    if (chosen !== undefined) {
      picker.value = String(options.findIndex((option) => JSON.stringify(option) === JSON.stringify(chosen)));
    }
    picker.addEventListener("change", () => {
      const fills = state.answering.fills;
      fills[target._cell] = { ...fills[target._cell] };
      if (picker.value === "") {
        delete fills[target._cell][field];
      } else {
        fills[target._cell][field] = options[Number(picker.value)];
      }
      state.answering.onChange();
    });
    return picker;
  }

  function drawBlocks(items, offset) {
    const groups = [...new Set(items.map((target) => target[encode.group]))];
    return h(
      "div",
      { class: "view-blocks" },
      groups.map((group, groupIndex) => {
        const members = items.filter((target) => target[encode.group] === group);
        const title = encode.group_label ? members[0][encode.group_label] : group;
        return h("section", { class: "vblock" }, [
          h("span", { class: "panel-label", text: show(title) }),
          h("div", { class: "vblock-items" }, members.map((target, at) => chip(target, show(title), { r: offset + groupIndex, c: at }))),
        ]);
      }),
    );
  }

  function drawDecision(items, offset) {
    const nodes = item.nodes ?? [];
    const byNode = new Map(items.map((target) => [target.node ?? target._link, target]));
    const focused = nodes.find((node) => node.id === state.selected);
    const strip = h(
      "div",
      { class: "nodes" },
      nodes.map((node, index) => {
        const target = byNode.get(node.id) ?? { _id: node.id, _cell: node.id, _link: node.id };
        const answer = target._masked ? "?" : target._taken ? (node.kind === "outcome" ? "taken" : (target.answer ?? "reached")) : "";
        const { classes } = chipState(target);
        const button = h(
          "button",
          {
            type: "button",
            class: `node node-${node.kind ?? "check"} tone-${node.tone ?? "accent"} ${target._taken ? (node.kind === "outcome" ? "chosen" : "on-path") : ""} ${classes}`,
            "data-id": node.id,
            "data-r": offset,
            "data-c": index,
            tabindex: -1,
            "aria-pressed": String(state.answering?.picked ? state.answering.picked.has(target._cell) : state.selected === node.id),
            "aria-label": `${node.label}${answer ? `: ${answer}` : ""}`,
          },
          [h("span", { text: node.label }), h("span", { class: "ans", text: answer })],
        );
        button.addEventListener("click", () => {
          if (node.example && shared && !state.answering) {
            shared.values = { ...shared.values, ...node.example };
            notify();
          }
          select({ ...target, _id: node.id });
          if (node.code?.file && context.markLines) {
            context.markLines(node.code.file, spanRange(node.code));
          }
        });
        return button;
      }),
    );
    const note = focused
      ? h("div", { class: "node-note" }, [
          h("div", { html: renderMarkdown(focused.note ?? "") }),
          focused.code?.text || focused.code?.spans ? codePanel(focused.code) : null,
        ])
      : h("p", { class: "node-note hint", text: "Click a box to see the line it stands for." });
    return h("div", { class: "strip" }, [strip, note]);
  }

  function drawSteps() {
    const count = state.steps.length;
    if (count === 0) {
      return h("p", { class: "hint", text: item.missing || "No steps." });
    }
    state.step = Math.min(state.step, count - 1);
    const step = state.steps[state.step];
    const withheld = Boolean(item.gate) && !state.revealed && (item.data ? Boolean(state.items[state.step]?._masked) : !step.note);
    const dots = h(
      "div",
      { class: "step-dots", role: "group", "aria-label": "Steps" },
      state.steps.map((other, at) => {
        const button = h("button", {
          type: "button",
          class: (other.link ?? []).some((link) => state.linked.has(link)) && at !== state.step ? "linked" : "",
          "aria-current": at === state.step ? "step" : false,
          "aria-label": `Step ${at + 1}`,
          text: String(at + 1),
        });
        button.addEventListener("click", () => goStep(at));
        return button;
      }),
    );
    const back = h("button", { type: "button", text: "Back", disabled: state.step === 0 });
    const next = h("button", { type: "button", class: "primary", text: "Next", disabled: state.step === count - 1 });
    back.addEventListener("click", () => goStep(state.step - 1));
    next.addEventListener("click", () => goStep(state.step + 1));
    const shown = withheld ? [] : Object.entries(step.show ?? {}).map(([field, value]) => h("span", { class: "chip", text: `${field} ${show(value)}` }));
    return h("div", { class: "view-steps" }, [
      dots,
      codePanel(step.code, step.line ?? null),
      h("div", { class: "walk-note", "aria-live": "polite" }, [
        withheld ? h("p", { text: GATED_NOTE }) : step.note ? h("div", { html: renderMarkdown(step.note) }) : null,
        shown.length ? h("div", { class: "panel-value" }, shown) : null,
      ]),
      h("div", { class: "actions" }, [back, next, h("span", { class: "progress", text: `Step ${state.step + 1} of ${count}` })]),
    ]);
  }

  function goStep(at) {
    state.step = Math.max(0, Math.min(at, state.steps.length - 1));
    const step = state.steps[state.step];
    state.linked = new Set(step?.link ?? []);
    draw();
    if (context.sectionId) {
      document.dispatchEvent(new CustomEvent(LINK_EVENT, { detail: { section: context.sectionId, from: item.id, links: [...state.linked] } }));
    }
    if (step?.code?.file && context.markLines) {
      context.markLines(step.code.file, spanRange(step.code));
    }
  }

  function drawStatus(items) {
    const marks = (item.marks ?? []).filter((mark) => mark.place === "status");
    const hits = marks.map((mark) => [mark, items.find((target) => !target._masked && matches(mark.where, target))]).filter(([, hit]) => hit);
    if (items.length === 0 && item.layout !== "steps") {
      status.className = "view-status tone-warn";
      status.replaceChildren(h("p", { text: item.missing || "No recorded run for these inputs." }));
      return;
    }
    status.className = `view-status${hits.length ? ` tone-${hits[0][0].tone ?? "accent"}` : " empty"}`;
    status.replaceChildren(...hits.map(([mark, hit]) => h("span", { text: fillText(mark.text, hit) })));
  }

  function drawLegend(items) {
    const present = [...new Set(items.filter((target) => !target._masked).map((target) => kindOf(target, encode).id))].filter((id) => id !== undefined);
    const entries = present.map((id) => [`tone-${kinds.get(id)?.colour ?? "accent"}`, kinds.get(id)?.label ?? String(id)]);
    const flags = [
      ["_lost", "lost", "lost: never arrived"],
      ["_dup", "dup", "×2: arrived again"],
      ["_burst", "burst", "stacked: arrived together"],
      ["_masked", "masked", "?: hidden until you answer"],
      ["_differs", "differs", "differs between panes"],
    ].filter(([field]) => field !== "_differs" || !items.some((target) => target._masked));
    for (const [field, cls, text] of flags) {
      if (items.some((target) => target[field])) {
        entries.push([cls, text]);
      }
    }
    legend.hidden = entries.length === 0 || ["steps", "decision"].includes(item.layout);
    legend.replaceChildren(...entries.map(([cls, text]) => h("span", {}, [h("i", { class: `swatch ${cls}` }), text])));
  }

  function drawDetail(items) {
    const target = items.find((one) => one._id === state.selected);
    if (!target || item.layout === "decision" || item.layout === "steps") {
      detail.replaceChildren();
      return;
    }
    if (target._masked) {
      detail.replaceChildren(h("p", { class: "hint", text: "Hidden until you answer the checkpoint." }));
      return;
    }
    const kind = kindOf(target, encode);
    const head = [kind.label, encode.lane ? target[encode.lane] : "", show(target[encode.key])].filter(Boolean).join(" · ");
    detail.replaceChildren(
      h("span", { class: "panel-label", text: head }),
      h("pre", { class: "view-payload" }, [h("code", { text: publicFields(target).map(([field, value]) => `${field}: ${show(value)}`).join("\n") })]),
    );
  }

  function drawNotes() {
    if (item.layout === "table" || !(item.notes ?? []).length) {
      notes.hidden = true;
      return;
    }
    notes.replaceChildren(
      ...item.notes.map((note, index) => {
        const button = h("button", { type: "button", class: "link", "aria-pressed": String(state.noteFocus === index), html: renderMarkdown(note.text) });
        button.addEventListener("click", () => {
          state.noteFocus = state.noteFocus === index ? null : index;
          draw();
        });
        return h("li", { "data-verified": note.verified ?? false }, [button]);
      }),
    );
  }

  function drawTasks(items) {
    if (!(item.tasks ?? []).length) {
      tasks.hidden = true;
      return;
    }
    item.tasks.forEach((task, index) => {
      met[index] ||= items.some((target) => matches(task.when, target));
    });
    tasks.replaceChildren(...item.tasks.map((task, index) => h("li", { class: "playground-task", "data-met": String(met[index]), text: task.text })));
    if (!done && met.every(Boolean)) {
      done = true;
      context.onComplete?.();
    }
  }

  function draw() {
    const hadFocus = typeof document.activeElement !== "undefined" && body.contains?.(document.activeElement);
    const items = current();
    const key = JSON.stringify([values(), state.pane, state.revealed, state.items.length]);
    const moved = key !== state.scenarioKey;
    state.scenarioKey = key;
    const kept = [...body.querySelectorAll(".view-scroll")].map((scroller) => scroller.scrollLeft);
    controls?.sync();
    drawStatus(items);
    drawLegend(items);
    const shown = panes.length > 1 ? (state.pane === null ? panes : [state.pane]) : [undefined];
    const render = LAYOUTS[item.layout] ?? LAYOUTS.table;
    let offset = 0;
    const parts = shown.map((pane) => {
      const paneItems = pane === undefined ? items : items.filter((target) => target._pane === pane);
      const part = render(paneItems, pane, offset);
      offset += 1000;
      return pane === undefined ? part : h("div", { class: "view-pane" }, [h("span", { class: "panel-label", text: `${item.split}: ${show(pane)}` }), part]);
    });
    body.replaceChildren(panes.length > 1 && shown.length > 1 ? h("div", { class: "view-panes" }, parts) : parts[0]);
    rove(hadFocus);
    drawDetail(items);
    drawNotes();
    drawTasks(items);
    globalThis.requestAnimationFrame?.(() =>
      body.querySelectorAll(".view-scroll").forEach((scroller, at) => {
        const column = scroller.querySelector("[data-burst]") ?? scroller.querySelector("[data-lost]");
        scroller.scrollLeft = moved ? (column ? Math.max(0, column.offsetLeft - scroller.clientWidth * 0.45) : 0) : (kept[at] ?? 0);
      }),
    );
  }

  function cells() {
    return [...body.querySelectorAll("[data-r]")];
  }

  function rove(refocus) {
    const all = cells();
    const active = all.find((cell) => cell.dataset.id === state.focusId) ?? all[0];
    for (const cell of all) {
      cell.tabIndex = cell === active ? 0 : -1;
    }
    if (refocus && active) {
      active.focus({ preventScroll: false });
    }
  }

  body.addEventListener("keydown", (event) => {
    if (item.layout === "steps") {
      return;
    }
    const cell = event.target.closest?.("[data-r]");
    if (!cell) {
      return;
    }
    const all = cells();
    const spots = all.map((one) => ({ r: Number(one.dataset.r), c: Number(one.dataset.c), node: one }));
    const here = spots.find((spot) => spot.node === cell);
    const target = nextCell(spots, here, event.key);
    if (!target) {
      return;
    }
    event.preventDefault();
    state.focusId = target.node.dataset.id;
    rove(false);
    target.node.focus();
  });
  root.addEventListener("keydown", (event) => {
    if (item.layout !== "steps" || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) {
      return;
    }
    const count = state.steps.length;
    const delta = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (delta && state.step + delta >= 0 && state.step + delta < count) {
      event.preventDefault();
      goStep(state.step + delta);
    }
  });
  if (item.layout === "steps") {
    root.tabIndex = 0;
    root.setAttribute("aria-label", `${stripMarkdown(item.caption)}: use the arrow keys to step`);
  }

  shared?.listeners.add(draw);

  const handle = {
    element: root,
    answer(question, onChange = () => {}) {
      state.answering = question.blanks
        ? { blanks: question.blanks, fills: {}, onChange }
        : { candidates: new Set(question.candidates ?? []), picked: new Set(), onChange };
      draw();
      return {
        response: () => {
          if (state.answering?.fills) {
            const filled = Object.entries(state.answering.fills).filter(([, given]) => Object.keys(given).length);
            return filled.length ? Object.fromEntries(filled) : null;
          }
          return state.answering ? [...state.answering.picked].sort() : [];
        },
        stop: () => {
          if (state.answering?.picked) {
            state.answer = { cells: new Set(), picked: state.answering.picked };
          }
          state.answering = null;
          draw();
        },
      };
    },
    showAnswer(reveal, response) {
      state.answering = null;
      state.answer = reveal.answer_cells?.length
        ? { cells: new Set(reveal.answer_cells), picked: new Set(Array.isArray(response) ? response : []) }
        : { cells: reveal.cells ?? {}, response: response ?? {} };
      draw();
    },
    goStep,
    focus() {
      root.scrollIntoView?.({ behavior: "smooth", block: "center" });
      (cells().find((cell) => cell.tabIndex === 0) ?? root).focus?.({ preventScroll: true });
    },
    state,
  };
  if (item.id) {
    handles.set(item.id, handle);
  }
  draw();
  return { element: root, handle };
}

function inputControls(inputs, shared, onChange) {
  const syncs = [];
  const set = (field, value) => {
    shared.values = setInput(inputs, shared.values, field, value);
    onChange();
  };
  const element = h(
    "div",
    { class: "view-controls" },
    inputs.map((input) => {
      if (input.control === "toggle") {
        const button = h("button", { type: "button", class: "view-toggle", "aria-pressed": "false", text: input.label });
        button.addEventListener("click", () => set(input.field, shared.values[input.field] === input.values[0] ? input.values[1] : input.values[0]));
        syncs.push(() => button.setAttribute("aria-pressed", String(shared.values[input.field] !== input.values[0])));
        return h("div", { class: "view-control" }, [button, input.hint ? h("span", { class: "hint", text: input.hint }) : null]);
      }
      const id = `in-${Math.random().toString(36).slice(2, 9)}`;
      const output = h("output", { for: id });
      const control =
        input.control === "select"
          ? h("select", { id }, input.values.map((value, index) => h("option", { value: String(index), text: show(value) })))
          : h("input", { id, type: "range", min: 0, max: input.values.length - 1, step: 1 });
      control.addEventListener(input.control === "select" ? "change" : "input", () => set(input.field, input.values[Number(control.value)]));
      syncs.push(() => {
        const value = shared.values[input.field];
        control.value = String(input.values.findIndex((one) => one === value));
        control.setAttribute("aria-valuetext", show(value));
        output.textContent = show(value);
      });
      return h("div", { class: "view-control" }, [
        h("label", { for: id }, [h("span", { text: input.label }), " ", output]),
        control,
        input.hint ? h("span", { class: "hint", text: input.hint }) : null,
      ]);
    }),
  );
  return { element, sync: () => syncs.forEach((sync) => sync()) };
}

function repeatCount(target) {
  const match = /#(\d+)$/.exec(target._id ?? "");
  return match ? Number(match[1]) : 2;
}

function stripMarkdown(text = "") {
  return text.replace(/[`*_]/g, "");
}
