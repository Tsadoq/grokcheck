import { renderMarkdown } from "./markdown.js";
import { element } from "./questions.js";

/** The state `values` lands on once constraints are applied, and which tasks it meets. */
export function evaluate(item, values) {
  const settled = { ...values };
  for (const { after, gt } of item.constraints ?? []) {
    if (settled[after] <= settled[gt]) {
      const input = item.inputs.find((candidate) => candidate.id === after);
      const above = input.min + (Math.floor((settled[gt] - input.min) / input.step) + 1) * input.step;
      settled[after] = Math.min(Math.max(above, input.min), input.max);
    }
  }
  const key = item.inputs.map((input) => settled[input.id]).join(",");
  const state = item.states.find((candidate) => candidate.key === key);
  const met = item.tasks.map((task) =>
    task.when.every((condition) => {
      const outcome = state.outcomes[item.variants.indexOf(condition.variant)];
      return outcome.outcome === condition.outcome && outcome.lost >= condition.min_lost;
    }),
  );
  return { values: settled, state, met };
}

const OUTCOME_TEXT = { complete: "complete", hang: "hangs" };

export function createPlayground(item, context = {}) {
  const legend = new Map(item.legend.map((entry) => [entry.cell, entry]));
  const outputs = item.inputs.map(() => element("output"));
  const sliders = item.inputs.map((input) => {
    const slider = element("input", { type: "range", min: input.min, max: input.max, step: input.step });
    slider.value = String(input.value);
    slider.addEventListener("input", () => draw(readSliders()));
    return slider;
  });
  const readSliders = () => Object.fromEntries(item.inputs.map((input, at) => [input.id, Number(sliders[at].value)]));
  const presets = item.presets.map((preset) => {
    const button = element("button", { type: "button", class: "chip", text: preset.label });
    button.addEventListener("click", () => draw({ ...readSliders(), ...preset.values }));
    return button;
  });
  const timeline = element("div", {
    class: "playground-timeline",
    style: `grid-template-columns: minmax(6em, auto) repeat(${item.ticks}, minmax(2em, 1fr)) minmax(6em, auto)`,
  });
  const explain = element("div", { class: "playground-explain", "aria-live": "polite" });
  const met = item.tasks.map(() => false);
  const taskRows = item.tasks.map((task) => element("li", { class: "playground-task", text: task.text }));
  let completed = false;

  function draw(values) {
    const result = evaluate(item, values);
    item.inputs.forEach((input, at) => {
      sliders[at].value = String(result.values[input.id]);
      outputs[at].textContent = String(result.values[input.id]);
    });
    timeline.replaceChildren(...timelineCells(item, result.state, legend));
    explain.innerHTML = renderMarkdown(result.state.explain);
    result.met.forEach((now, at) => {
      met[at] ||= now;
      taskRows[at].setAttribute("data-met", String(met[at]));
    });
    if (!completed && met.every(Boolean)) {
      completed = true;
      context.onComplete?.();
    }
  }

  const view = element("div", { class: "playground-element" }, [
    element(
      "div",
      { class: "playground-sliders" },
      item.inputs.map((input, at) => element("label", {}, [`${input.label} `, outputs[at], sliders[at]])),
    ),
    presets.length ? element("div", { class: "playground-presets" }, [element("span", { text: "Try:" }), ...presets]) : "",
    element("div", { class: "playground-scroll" }, [timeline]),
    element(
      "div",
      { class: "playground-legend" },
      item.legend.map((entry) => element("span", {}, [swatch(entry), entry.label])),
    ),
    explain,
    item.tasks.length ? element("ol", { class: "playground-tasks" }, taskRows) : "",
  ]);
  draw(readSliders());
  return view;
}

function timelineCells(item, state, legend) {
  const ticks = Array.from({ length: item.ticks }, (_, tick) => element("span", { class: "tick", text: `t${tick}` }));
  const cells = [element("span"), ...ticks, element("span")];
  item.rows.forEach((row, at) => {
    cells.push(element("span", { class: "row-label", text: row.label }));
    for (const cell of state.cells[at]) {
      const entry = legend.get(cell);
      cells.push(entry ? swatch(entry, "cell") : element("span", { class: "cell" }));
    }
    const variant = item.variants.indexOf(row.id);
    cells.push(variant === -1 ? element("span") : outcomeTag(state.outcomes[variant]));
  });
  return cells;
}

function outcomeTag({ outcome, lost }) {
  const tone = { complete: "pass", lost: "warn", hang: "fail" }[outcome];
  return element("span", { class: `badge ${tone}`, text: OUTCOME_TEXT[outcome] ?? `lost ${lost}` });
}

function swatch(entry, className = "swatch") {
  return element("span", {
    class: className,
    title: entry.label,
    style: `--cell: var(--${entry.colour})`,
  });
}
