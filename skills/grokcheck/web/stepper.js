import { REVEAL_EVENT, codeView, element } from "./questions.js";
import { recall, remember } from "./storage.js";

const SILENT_MS_PER_SENTENCE = 2500;
const SPOKEN_MS_PER_CHAR = 100;
const SPOKEN_GRACE_MS = 2000;
const GATED_NARRATION = "Predict before you look: answer the checkpoint below to reveal this step.";

/** The step of `versionLabel` at `index`, clamped into the version; a withheld step is `gated`. */
export function stepState(trace, versionLabel, index) {
  const { steps } = versionOf(trace, versionLabel);
  const at = Math.min(Math.max(index, 0), steps.length - 1);
  const step = steps[at];
  return {
    index: at,
    count: steps.length,
    curLine: step.cur_line,
    state: step.state ?? {},
    narration: step.narration ?? [],
    gated: isWithheld(step),
  };
}

function isWithheld(step) {
  return !("narration" in step);
}

function versionOf(trace, label) {
  return trace.versions.find((version) => version.label === label) ?? trace.versions[0];
}

export function createStepper(trace) {
  const hashKey = `step-${trace.trace_id}`;
  let version = trace.versions[0].label;
  let index = Math.min(stepFromHash(hashKey), firstGated(trace.versions[0].steps));
  let watching = 0;
  let playing = false;

  const code = element("div", { class: "stepper-code" });
  const values = new Map(trace.panels.map((panel) => [panel.id, element("div", { class: `panel-value ${panel.kind}` })]));
  const narration = element("div", { class: "narration", "aria-live": "polite" });
  const progress = element("span", { class: "progress" });
  const back = element("button", { type: "button", text: "Back" });
  const watch = element("button", { type: "button", text: "Watch" });
  const next = element("button", { type: "button", class: "primary", text: "Next step" });
  const narrate = element("input", { type: "checkbox", checked: recall("narrate") !== "false" });
  const versionButtons = trace.versions.map((candidate) =>
    element("button", { type: "button", "data-version": candidate.label, text: candidate.label }),
  );

  const draw = () => {
    const step = stepState(trace, version, index);
    index = step.index;
    const shown = versionOf(trace, version).code;
    const view = codeView(shown);
    view.querySelectorAll(".code-line")[step.curLine - (shown.start_line ?? 1)]?.classList.add("current");
    code.replaceChildren(view);
    for (const panel of trace.panels) {
      values.get(panel.id).replaceChildren(...panelValue(panel.kind, step.state[panel.id], step.gated));
    }
    const sentences = step.gated ? [GATED_NARRATION] : step.narration;
    narration.replaceChildren(...sentences.map((sentence) => element("p", { text: sentence })));
    progress.textContent = `Step ${index + 1} of ${step.count}`;
    back.disabled = index === 0;
    next.disabled = step.gated || index === step.count - 1;
    for (const button of versionButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.version === version));
    }
    writeStepToHash(hashKey, index + 1);
  };

  const stop = () => {
    watching += 1;
    playing = false;
    window.speechSynthesis?.cancel();
    watch.textContent = "Watch";
  };

  const move = (delta) => {
    stop();
    if (delta > 0 && stepState(trace, version, index).gated) {
      return;
    }
    index += delta;
    draw();
  };

  const play = async () => {
    const run = ++watching;
    playing = true;
    watch.textContent = "Pause";
    for (;;) {
      const step = stepState(trace, version, index);
      if (step.gated) {
        stop();
        focusGate(trace.gate);
        return;
      }
      await speak(step.narration, narrate.checked);
      if (run !== watching) {
        return;
      }
      if (index === step.count - 1) {
        stop();
        return;
      }
      index += 1;
      draw();
    }
  };

  back.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  watch.addEventListener("click", () => (playing ? stop() : play()));
  narrate.addEventListener("change", () => remember("narrate", String(narrate.checked)));
  for (const button of versionButtons) {
    button.addEventListener("click", () => {
      stop();
      version = button.dataset.version;
      index = 0;
      draw();
    });
  }
  const onReveal = ({ detail }) => {
    if (detail.gate === trace.gate) {
      document.removeEventListener(REVEAL_EVENT, onReveal);
      trace.versions[detail.version].steps = detail.steps;
      draw();
    }
  };
  if (trace.gate) {
    document.addEventListener(REVEAL_EVENT, onReveal);
  }

  const root = element("div", { class: "stepper", role: "group", tabindex: 0, "aria-label": `${trace.title}: use the arrow keys to step` }, [
    element("h3", { text: trace.title }),
    versionButtons.length > 1 ? element("div", { class: "versions", role: "group", "aria-label": "Code version" }, versionButtons) : "",
    element("div", { class: "stepper-body" }, [
      code,
      element(
        "div",
        { class: "panels" },
        trace.panels.map((panel) => element("div", { class: "panel" }, [element("span", { class: "panel-label", text: panel.label }), values.get(panel.id)])),
      ),
    ]),
    narration,
    element("div", { class: "actions" }, [back, watch, element("label", {}, [narrate, " narrate"]), next, progress]),
  ]);
  root.addEventListener("keydown", (event) => {
    if (event.target.tagName === "INPUT" || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) {
      return;
    }
    event.preventDefault();
    move(event.key === "ArrowRight" ? 1 : -1);
  });
  draw();
  return root;
}

function panelValue(kind, value, gated) {
  if (gated) {
    return [element("span", { class: "empty", text: "?" })];
  }
  if (kind === "status") {
    return [String(value ?? "")];
  }
  if (value === null || value === undefined) {
    return [element("span", { class: "empty", text: "does not exist" })];
  }
  if (value.length === 0) {
    return [element("span", { class: "empty", text: "empty" })];
  }
  return value.map((chip) => element("span", { class: "chip", text: String(chip) }));
}

function speak(sentences, aloud) {
  const synth = window.speechSynthesis;
  if (!aloud || !synth) {
    return new Promise((resolve) => setTimeout(resolve, sentences.length * SILENT_MS_PER_SENTENCE));
  }
  const text = sentences.join(" ");
  return new Promise((resolve) => {
    const fallback = setTimeout(resolve, text.length * SPOKEN_MS_PER_CHAR + SPOKEN_GRACE_MS);
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onend = utterance.onerror = () => {
      clearTimeout(fallback);
      resolve();
    };
    synth.speak(utterance);
  });
}

function focusGate(gate) {
  const card = document.querySelector(`[data-question="${CSS.escape(gate)}"]`);
  if (card) {
    card.tabIndex = -1;
    card.focus();
  }
}

function firstGated(steps) {
  const at = steps.findIndex(isWithheld);
  return at === -1 ? steps.length - 1 : at;
}

function stepFromHash(key) {
  const step = Number(new URLSearchParams(location.hash.slice(1)).get(key));
  return Number.isInteger(step) && step > 0 ? step - 1 : 0;
}

function writeStepToHash(key, step) {
  const params = new URLSearchParams(location.hash.slice(1));
  params.set(key, step);
  try {
    history.replaceState(null, "", `#${params}`);
  } catch {
    return;
  }
}
