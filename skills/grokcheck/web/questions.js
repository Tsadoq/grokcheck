import { renderCode } from "./codeview.js";
import { renderMarkdown } from "./markdown.js";

const OUTCOME_LABELS = {
  correct: "Correct",
  incorrect: "Incorrect",
  partial: "Partly correct",
  needs_review: "Needs review: your agent will check this answer",
  self_rated: "Self-rated",
};

export function moveStep(order, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= order.length) {
    return order;
  }
  const moved = [...order];
  const [step] = moved.splice(index, 1);
  moved.splice(target, 0, step);
  return moved;
}

export function element(tag, attributes = {}, children = []) {
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
  node.append(...children);
  return node;
}

export function codeView(code, options = {}) {
  const lineCount = code.text.replace(/\n$/, "").split("\n").length;
  const start = code.start_line ?? 1;
  const caption = code.file ? `${code.file}, lines ${start} to ${start + lineCount - 1}` : "";
  const view = element("figure", { class: "code-view", style: `--line-offset: ${start - 1}` }, [
    element("div", { html: renderCode(code.text, code.language, options) }),
  ]);
  if (caption) {
    view.prepend(element("figcaption", { text: caption }));
  }
  return view;
}

export function renderQuestion(question, { onChange = () => {}, beforeReveal = () => true } = {}) {
  const render = RENDERERS[question.type];
  if (!render) {
    throw new Error(`unknown question type ${question.type}`);
  }
  return render(question, { onChange, beforeReveal });
}

const RENDERERS = {
  single_choice: (question, { onChange }) => choiceGroup(question, "radio", onChange),
  multiple_choice: (question, { onChange }) => choiceGroup(question, "checkbox", onChange),
  open_answer: renderOpenAnswer,
  predict_output: renderPredictOutput,
  pick_line: renderPickLine,
  order_steps: renderOrderSteps,
  fill_blank: renderFillBlank,
};

function choiceGroup(question, kind, onChange) {
  const inputs = question.options.map((_, index) =>
    element("input", { type: kind, name: `choice-${question.id}`, value: index }),
  );
  const group = element(
    "fieldset",
    { class: "choices" },
    [element("legend", { text: kind === "radio" ? "Choose one" : "Choose every option that applies" })].concat(
      question.options.map((option, index) =>
        element("label", { class: "choice" }, [
          inputs[index],
          element("span", { class: "choice-text", html: renderMarkdown(option.text) }),
        ]),
      ),
    ),
  );
  group.addEventListener("change", onChange);
  const checked = () => inputs.filter((input) => input.checked).map((input) => Number(input.value));
  return {
    element: group,
    getResponse() {
      const indexes = checked();
      if (indexes.length === 0) {
        return null;
      }
      return kind === "radio" ? indexes[0] : indexes;
    },
  };
}

function renderOpenAnswer(question, { onChange, beforeReveal }) {
  const answer = element("textarea", { rows: 5, "aria-label": "Your answer" });
  const commit = element("button", { type: "button", text: "Commit answer", disabled: true });
  const rubricArea = element("div", { class: "rubric" });
  const container = element("div", { class: "open-answer" }, [answer, commit, rubricArea]);
  let ratings = null;

  answer.addEventListener("input", () => {
    commit.disabled = answer.value.trim() === "";
  });
  commit.addEventListener("click", () => {
    if (!beforeReveal()) {
      return;
    }
    answer.readOnly = true;
    commit.remove();
    const rubric = question.rubric ?? [];
    if (rubric.length === 0) {
      rubricArea.append(element("p", { class: "error", text: "This lesson carries no rubric to rate the answer against." }));
      return;
    }
    ratings = rubric.map(() => null);
    rubricArea.append(
      element("p", { text: "Rate your answer against each point: does it cover it?" }),
      ...rubric.map((item, index) => rubricItem(question.id, item, index, (met) => {
        ratings[index] = met;
        onChange();
      })),
    );
    onChange();
  });

  return {
    element: container,
    getResponse() {
      if (ratings === null || ratings.includes(null)) {
        return null;
      }
      return { text: answer.value, met: [...ratings] };
    },
  };
}

function rubricItem(questionId, item, index, onRate) {
  const name = `rubric-${questionId}-${index}`;
  const choice = (label, met) => {
    const input = element("input", { type: "radio", name, value: label });
    input.addEventListener("change", () => onRate(met));
    return element("label", {}, [input, ` ${label}`]);
  };
  return element("fieldset", { class: "rubric-item" }, [
    element("legend", { html: renderMarkdown(item) }),
    choice("Met", true),
    choice("Not met", false),
  ]);
}

function renderPredictOutput(question, { onChange }) {
  if (question.options?.length > 0) {
    return choiceGroup(question, "radio", onChange);
  }
  const output = element("textarea", { class: "mono", rows: 3, "aria-label": "Predicted output", spellcheck: "false" });
  output.addEventListener("input", onChange);
  return {
    element: element("label", { class: "predict" }, ["What does it print?", output]),
    getResponse: () => (output.value.trim() === "" ? null : output.value),
  };
}

function renderPickLine(question, { onChange }) {
  const view = codeView(question.code, { selectableLines: true });
  const lines = [...view.querySelectorAll(".code-line")];
  const toggle = (line) => {
    if (view.closest("fieldset[disabled]")) {
      return;
    }
    line.setAttribute("aria-pressed", String(line.getAttribute("aria-pressed") !== "true"));
    onChange();
  };
  view.addEventListener("click", (event) => {
    const line = event.target.closest(".code-line");
    if (line) {
      toggle(line);
    }
  });
  view.addEventListener("keydown", (event) => {
    const line = event.target.closest(".code-line");
    if (line && (event.key === " " || event.key === "Enter")) {
      event.preventDefault();
      toggle(line);
    }
  });
  return {
    element: element("div", { class: "pick-line" }, [element("p", { class: "hint", text: "Select every line that answers the question." }), view]),
    getResponse() {
      const picked = lines.filter((line) => line.getAttribute("aria-pressed") === "true").map((line) => Number(line.dataset.line));
      return picked.length === 0 ? null : picked;
    },
  };
}

function renderOrderSteps(question, { onChange }) {
  let order = [...question.steps];
  const list = element("ol", { class: "steps" });
  const announcer = element("p", { class: "visually-hidden", "aria-live": "polite" });

  const move = (index, delta, direction) => {
    const next = moveStep(order, index, delta);
    if (next === order) {
      return;
    }
    order = next;
    draw();
    announcer.textContent = `"${order[index + delta]}" is now step ${index + delta + 1} of ${order.length}.`;
    const buttons = list.children[index + delta].querySelectorAll("button");
    const [up, down] = buttons;
    const preferred = direction === "up" ? up : down;
    (preferred.disabled ? (preferred === up ? down : up) : preferred).focus();
    onChange();
  };

  const draw = () => {
    list.replaceChildren(
      ...order.map((step, index) =>
        element("li", { class: "step" }, [
          element("span", { class: "step-text", html: renderMarkdown(step) }),
          stepButton("Up", `Move "${step}" up`, index === 0, () => move(index, -1, "up")),
          stepButton("Down", `Move "${step}" down`, index === order.length - 1, () => move(index, 1, "down")),
        ]),
      ),
    );
  };
  draw();

  return {
    element: element("div", { class: "order-steps" }, [
      element("p", { class: "hint", text: "Put the steps in order with the Up and Down buttons." }),
      list,
      announcer,
    ]),
    getResponse: () => [...order],
  };
}

function stepButton(label, description, disabled, onClick) {
  const button = element("button", { type: "button", "aria-label": description, disabled, text: label });
  button.addEventListener("click", onClick);
  return button;
}

function renderFillBlank(question, { onChange }) {
  const container = element("div", {
    class: "fill-blank",
    html: renderCode(question.text, question.language, { blanks: true }),
  });
  const inputs = [...container.querySelectorAll("input.blank")];
  container.addEventListener("input", onChange);
  return {
    element: container,
    getResponse() {
      if (inputs.some((input) => input.value.trim() === "")) {
        return null;
      }
      return Object.fromEntries(inputs.map((input) => [input.dataset.blank, input.value]));
    },
  };
}

export function renderReveal(question, grade) {
  const { reveal, outcome, score, response } = grade;
  const parts = [
    element("p", { class: `outcome outcome-${outcome}` }, [
      element("strong", { text: OUTCOME_LABELS[outcome] ?? outcome }),
      outcome === "correct" || outcome === "incorrect" ? "" : ` (score ${Math.round(score * 100)}%)`,
    ]),
  ];
  if (reveal.why.length > 0) {
    parts.push(revealedOptions(question.options ?? [], reveal, response));
  }
  if (reveal.answer_lines.length > 0) {
    parts.push(revealedLines(question.code, reveal.answer_lines));
  }
  if (reveal.steps.length > 0) {
    parts.push(
      element("p", { text: "The correct order:" }),
      element("ol", {}, reveal.steps.map((step) => element("li", { html: renderMarkdown(step) }))),
    );
  }
  if (reveal.accepted.length > 0) {
    parts.push(labelledList("Accepted answers:", reveal.accepted));
  }
  if (reveal.blanks.length > 0) {
    parts.push(
      labelledList(
        "Accepted blanks:",
        reveal.blanks.map((blank) => `${blank.id}: ${blank.accepted.join(" or ")}`),
      ),
    );
  }
  if (reveal.model_answer) {
    parts.push(element("p", { text: "A model answer:" }), element("div", { html: renderMarkdown(reveal.model_answer) }));
  }
  if (reveal.rubric.length > 0) {
    const met = response?.met ?? [];
    parts.push(
      element(
        "ul",
        { class: "rubric-result" },
        reveal.rubric.map((item, index) =>
          element("li", { class: met[index] ? "met" : "unmet" }, [
            element("span", { text: met[index] ? "Met: " : "Not met: " }),
            element("span", { html: renderMarkdown(item) }),
          ]),
        ),
      ),
    );
  }
  if (reveal.explanation) {
    parts.push(element("div", { class: "explanation", html: renderMarkdown(reveal.explanation) }));
  }
  return element("div", { class: "reveal" }, parts);
}

function revealedOptions(options, reveal, response) {
  const chosen = new Set([response].flat());
  return element(
    "ul",
    { class: "revealed-options" },
    reveal.why.map((why, index) => {
      const marks = [reveal.correct.includes(index) ? "correct answer" : "", chosen.has(index) ? "your choice" : ""];
      return element("li", { class: reveal.correct.includes(index) ? "is-correct" : "" }, [
        element("div", { html: renderMarkdown(options[index]?.text ?? `Option ${index + 1}`) }),
        element("small", { text: marks.filter(Boolean).join(", ") }),
        element("div", { class: "why", html: renderMarkdown(why) }),
      ]);
    }),
  );
}

function revealedLines(code, answerLines) {
  if (!code) {
    return element("p", { text: `Answer lines: ${answerLines.join(", ")}` });
  }
  const view = codeView(code);
  view.querySelectorAll(".code-line").forEach((line, index) => {
    line.classList.toggle("answer-line", answerLines.includes(index + 1));
  });
  return element("div", {}, [element("p", { text: `Answer lines: ${answerLines.join(", ")}` }), view]);
}

function labelledList(label, items) {
  return element("div", {}, [
    element("p", { text: label }),
    element("ul", {}, items.map((item) => element("li", {}, [element("code", { text: item })]))),
  ]);
}
