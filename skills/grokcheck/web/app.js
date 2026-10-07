import { askQuestion, fetchLesson, followReplies, offline, sendAnswer, skippedOffline, submitQuiz } from "./api.js";
import { gatesSection, renderElements } from "./elements.js";
import { renderMarkdown } from "./markdown.js";
import { codeView, element, renderQuestion, renderReveal } from "./questions.js";
import { recall, remember } from "./storage.js";
import { configureViews } from "./view.js";

const DEPTHS = [
  ["short", "Short"],
  ["detail", "Detailed"],
];

const CONFIDENCE_LEVELS = [
  ["sure", "Sure"],
  ["unsure", "Unsure"],
  ["guess", "Guess"],
];

const stage = document.getElementById("stage");
const rail = document.getElementById("rail");
const railItems = [];
let railButtons = [];
const replySlots = new Map();
const repliesReceived = new Map();

function showReply(questionId, markdown) {
  repliesReceived.set(questionId, markdown);
  const slot = replySlots.get(questionId);
  if (slot) {
    slot.classList.remove("pending");
    slot.innerHTML = renderMarkdown(markdown);
  }
}

function onAsyncClick(button, status, failure, action, settle = () => {
  button.disabled = false;
}) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "";
    try {
      await action();
    } catch (error) {
      status.textContent = `${failure}: ${error.message}`;
    } finally {
      settle();
    }
  });
}

function threadView(thread) {
  const slot = element("div", { class: "reply pending", text: "Waiting for a reply from your agent..." });
  replySlots.set(thread.id, slot);
  const markdown = thread.reply || repliesReceived.get(thread.id);
  if (markdown) {
    showReply(thread.id, markdown);
  }
  return element("li", { class: "thread" }, [
    thread.selection ? element("blockquote", { text: thread.selection }) : "",
    element("p", { class: "asked", text: thread.text }),
    slot,
  ]);
}

function selectionWithin(container) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    return "";
  }
  const range = selection.getRangeAt(0);
  return container.contains(range.commonAncestorContainer) ? selection.toString().trim() : "";
}

function askPanel(section, sectionElement) {
  let selection = "";
  const quote = element("blockquote", { class: "captured", hidden: true });
  const clear = element("button", { type: "button", class: "link", text: "Clear selection", hidden: true });
  const text = element("textarea", { rows: 2, "aria-label": `Ask a question about ${section.title}` });
  const captureButton = element("button", { type: "button", text: "Ask about selection" });
  const askButton = element("button", { type: "button", text: "Ask" });
  const status = element("p", { class: "status", "aria-live": "polite" });
  const threads = element("ul", { class: "threads" });
  const socratic = element("input", { type: "checkbox", checked: recall("socratic") === "1" });
  socratic.addEventListener("change", () => remember("socratic", socratic.checked ? "1" : "0"));

  const setSelection = (value) => {
    selection = value;
    quote.textContent = value;
    quote.hidden = clear.hidden = value === "";
  };
  captureButton.addEventListener("mousedown", (event) => event.preventDefault());
  captureButton.addEventListener("click", () => {
    const captured = selectionWithin(sectionElement);
    status.textContent = captured ? "" : "Select some text in this section first.";
    if (captured) {
      setSelection(captured);
      text.focus();
    }
  });
  clear.addEventListener("click", () => setSelection(""));
  onAsyncClick(askButton, status, "Could not send the question", async () => {
    if (text.value.trim() === "") {
      status.textContent = "Type your question first.";
      return;
    }
    const mode = socratic.checked ? "socratic" : "answer";
    const thread = await askQuestion(section.id, text.value.trim(), selection, mode);
    threads.append(threadView(thread));
    text.value = "";
    setSelection("");
    status.textContent = "Sent. Your agent's reply will appear below.";
  });

  return element("aside", { class: "ask" }, [
    element("span", { class: "eyebrow", text: "Ask your agent" }),
    element("h3", { text: "Questions about this section" }),
    quote,
    clear,
    text,
    element("label", {}, [socratic, " Socratic: ask me questions instead"]),
    element("div", { class: "actions" }, [captureButton, askButton]),
    status,
    threads,
  ]);
}

function confidencePicker(questionId, onChange) {
  const inputs = CONFIDENCE_LEVELS.map(([value]) =>
    element("input", { type: "radio", name: `confidence-${questionId}`, value }),
  );
  const picker = element(
    "fieldset",
    { class: "confidence" },
    [element("legend", { text: "How sure are you?" })].concat(
      CONFIDENCE_LEVELS.map(([, label], index) => element("label", {}, [inputs[index], ` ${label}`])),
    ),
  );
  picker.addEventListener("change", onChange);
  return {
    element: picker,
    value: () => inputs.find((input) => input.checked)?.value ?? null,
    lock: () => {
      picker.disabled = true;
    },
  };
}

function skippedCard(question, label, onAnswered) {
  queueMicrotask(() => onAnswered({ question_id: question.id, grade: null }));
  return element("article", { class: "question", "data-question": question.id }, [
    label ? element("span", { class: "eyebrow", text: label }) : "",
    element("div", { class: "prompt", html: renderMarkdown(question.prompt) }),
    element("p", {
      class: "note",
      text: "This question runs the project's tests, so it needs grokcheck. It is skipped here and does not count toward your score.",
    }),
  ]);
}

function questionCard(question, { final, label, onAnswered }) {
  if (skippedOffline(question)) {
    return skippedCard(question, label, onAnswered);
  }
  const status = element("p", { class: "status", "aria-live": "polite" });
  const button = element("button", { type: "button", class: "primary", text: final ? "Save answer" : "Check answer", disabled: true });
  const update = () => {
    button.disabled = answer.getResponse() === null || confidence.value() === null;
  };
  const confidence = confidencePicker(question.id, update);
  const answer = renderQuestion(question, {
    onChange: update,
    beforeReveal: () => {
      if (confidence.value() === null) {
        status.textContent = "Choose how sure you are before committing your answer.";
        return false;
      }
      status.textContent = "";
      confidence.lock();
      return true;
    },
  });
  const controls = element("fieldset", { class: "answer" }, [answer.element]);
  const feedback = element("div", { class: "feedback" });
  const card = element("article", { class: "question", "data-question": question.id }, [
    label ? element("span", { class: "eyebrow", text: label }) : "",
    element("div", { class: "prompt", html: renderMarkdown(question.prompt) }),
    question.code && question.type !== "pick_line" ? codeView(question.code) : "",
    controls,
    confidence.element,
    element("div", { class: "actions" }, [button]),
    status,
    feedback,
  ]);
  let saved = false;

  onAsyncClick(
    button,
    status,
    "Could not save the answer",
    async () => {
      const result = await sendAnswer(question.id, answer.getResponse(), confidence.value());
      if (final) {
        status.textContent = "Saved. You can change it until you submit.";
      } else {
        controls.disabled = true;
        confidence.lock();
        button.remove();
        feedback.replaceChildren(renderReveal(question, result.grade));
      }
      if (!saved) {
        saved = true;
        onAnswered(result);
      }
    },
    update,
  );
  return card;
}

function sectionView(section, index, count, onComplete) {
  const view = element("section", { class: "lesson-section", id: section.id, "data-section": section.id }, [
    element("div", { class: "head" }, [
      element("p", { class: "eyebrow", text: `Chapter ${index + 1} of ${count}` }),
      element("h2", { text: section.title }),
    ]),
  ]);
  const content = element("div", { class: "section-body", html: renderMarkdown(section.body) });
  let remaining = section.checkpoints.length + section.elements.filter(gatesSection).length;
  const passGate = () => {
    remaining -= 1;
    if (remaining === 0) {
      onComplete();
    }
  };
  view.append(
    content,
    renderElements(section, {
      sectionId: section.id,
      onComplete: (item) => {
        if (gatesSection(item)) {
          passGate();
        }
      },
      markLines: (file, lines) => markLines(view, file, lines),
    }),
  );
  view.append(
    element("h3", { text: "Checkpoint" }),
    ...section.checkpoints.map((question) => questionCard(question, { final: false, label: "Checkpoint", onAnswered: passGate })),
    offline ? "" : askPanel(section, view),
  );
  return view;
}

function markLines(view, file, [start, end]) {
  for (const figure of view.querySelectorAll("figure.code-view[data-file]")) {
    const first = Number(figure.dataset.start);
    figure.querySelectorAll(".code-line").forEach((line, at) => {
      line.classList.toggle("marked", figure.dataset.file === file && first + at >= start && first + at <= end);
    });
  }
}

function showSection(lesson, index) {
  if (index >= lesson.sections.length) {
    offerFinal(lesson);
    return;
  }
  const view = sectionView(lesson.sections[index], index, lesson.sections.length, () => {
    railItems[index].done = true;
    showSection(lesson, index + 1);
  });
  stage.append(view);
  renderRail();
  if (index > 0) {
    view.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function probeStage(lesson, onDone) {
  let remaining = lesson.probe.length;
  let anyWrong = false;
  stage.append(
    element("section", { class: "probe" }, [
      element("div", { class: "head" }, [
        element("p", { class: "eyebrow", text: "Probe · not scored" }),
        element("h2", { text: "Before you start" }),
        element("p", { class: "note", text: "These questions are not scored; they decide how much detail the lesson starts with." }),
      ]),
      ...lesson.probe.map((question) =>
        questionCard(question, {
          final: false,
          label: "Probe",
          onAnswered: (result) => {
            anyWrong ||= result.grade !== null && result.grade.outcome !== "correct";
            remaining -= 1;
            if (remaining === 0) {
              onDone(anyWrong);
            }
          },
        }),
      ),
    ]),
  );
}

function depthToggle(initial) {
  const buttons = DEPTHS.map(([value, label]) => element("button", { type: "button", "data-depth-choice": value, text: label }));
  const show = (depth) => {
    document.body.dataset.depthView = depth;
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(button.dataset.depthChoice === depth));
    }
  };
  for (const button of buttons) {
    button.addEventListener("click", () => {
      show(button.dataset.depthChoice);
      remember("depth", button.dataset.depthChoice);
    });
  }
  show(initial);
  return {
    element: element("div", { class: "depth" }, [
      element("span", { text: "Explanations" }),
      element("div", { class: "toggle", role: "group", "aria-label": "Lesson depth" }, buttons),
    ]),
    show,
  };
}

function planDisclosure(plan) {
  return element("details", { class: "plan" }, [
    element("summary", { text: "Why this lesson looks like this" }),
    element("ul", {}, plan.rationale.map((sentence) => element("li", { text: sentence }))),
  ]);
}

function offerFinal(lesson) {
  const start = element("button", { type: "button", class: "primary", text: "Start final quiz" });
  const intro = element("section", { class: "final-intro" }, [
    element("div", { class: "head" }, [element("p", { class: "eyebrow", text: "Closed book" }), element("h2", { text: "Final quiz" })]),
    element("p", {
      text: "The final quiz is closed book: the explanation disappears and asking questions is switched off until you submit. You see how you did after submitting.",
    }),
    start,
  ]);
  start.addEventListener("click", () => startFinal(lesson));
  stage.append(intro);
  renderRail();
  intro.scrollIntoView({ behavior: "smooth", block: "start" });
}

function startFinal(lesson) {
  stage.replaceChildren();
  let unanswered = lesson.final.length;
  const submit = element("button", { type: "button", class: "primary", text: "Submit final quiz", disabled: true });
  const status = element("p", { class: "status", "aria-live": "polite" });
  const quiz = element("section", { class: "final-quiz" }, [
    element("div", { class: "head" }, [element("p", { class: "eyebrow", text: "Closed book" }), element("h2", { text: "Final quiz" })]),
    ...lesson.final.map((question, index) =>
      questionCard(question, {
        final: true,
        label: `Question ${index + 1} of ${lesson.final.length}`,
        onAnswered: () => {
          unanswered -= 1;
          submit.disabled = unanswered > 0;
        },
      }),
    ),
    element("div", { class: "actions" }, [submit]),
    status,
  ]);
  onAsyncClick(submit, status, "Could not submit", async () => {
    showResults(lesson, await submitQuiz());
  });
  stage.append(quiz);
  renderRail();
  window.scrollTo({ top: 0 });
}

function gradedItem(question, item) {
  return element("article", { class: "question result" }, [
    element("div", { class: "prompt", html: renderMarkdown(item.prompt) }),
    element("p", { class: "confidence-given", text: item.confidence ? `You said: ${item.confidence}` : "" }),
    renderReveal(question ?? {}, item),
  ]);
}

function showResults(lesson, results) {
  const questions = new Map(
    lesson.sections.flatMap((section) => section.checkpoints).concat(lesson.final).map((question) => [question.id, question]),
  );
  const { summary } = results;
  const view = element("section", { class: "results" }, [
    element("div", { class: "head" }, [element("p", { class: "eyebrow", text: "Debrief" }), element("h2", { text: "Results" })]),
    element("p", { class: "score", text: `Final quiz score: ${Math.round(summary.final_score * 100)}%` }),
    summary.confident_wrong.length > 0
      ? element("p", {
          text: `You were sure but not right on ${summary.confident_wrong.length} question(s)${offline ? "." : "; your agent will start there."}`,
        })
      : "",
    !offline && summary.needs_review.length + summary.self_rated.length > 0
      ? element("p", { text: "Your agent will re-grade your free-text answers in the chat." })
      : "",
    element("h3", { text: "Final quiz" }),
    ...results.final.map((item) => gradedItem(questions.get(item.question_id), item)),
  ]);
  if (results.checkpoints.length > 0) {
    view.append(
      element("h3", { text: "Checkpoints" }),
      ...results.checkpoints.map((item) => gradedItem(questions.get(item.question_id), item)),
    );
  }
  if (results.questions.length > 0) {
    view.append(element("h3", { text: "Your questions" }), element("ul", { class: "threads" }, results.questions.map(threadView)));
  }
  stage.replaceChildren(view);
  railItems.at(-1).done = true;
  renderRail();
  window.scrollTo({ top: 0 });
}

function showHeader(lesson) {
  document.title = `${lesson.title} - grokcheck`;
  document.getElementById("title").textContent = lesson.title;
  const files = lesson.scope.files;
  document.getElementById("eyebrow").textContent = [
    "grokcheck",
    lesson.plan ? `${lesson.plan.subject} lesson` : "",
    files.length === 1 ? files[0] : files.length > 1 ? `${files.length} files` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const scope = document.getElementById("scope");
  scope.innerHTML = renderMarkdown(lesson.scope.summary);
  if (lesson.scope.files.length > 0) {
    scope.append(element("ul", { class: "files" }, lesson.scope.files.map((file) => element("li", {}, [element("code", { text: file })]))));
  }
  if (lesson.plan) {
    document.getElementById("intro").prepend(planDisclosure(lesson.plan));
  }
}

function railTarget(index) {
  const sections = stage.querySelectorAll(".lesson-section");
  return index < railItems.length - 1 ? sections[index] : stage.querySelector(".final-intro, .final-quiz, .results");
}

function renderRail() {
  railButtons = railItems.map((item, index) => {
    const open = Boolean(railTarget(index));
    const button = element(
      "button",
      { type: "button", class: item.done ? "done" : open ? "" : "locked", disabled: !open },
      [
        element("span", { class: "dot", "aria-hidden": "true", text: item.done ? "\u2713" : item.number }),
        element("span", { class: "rail-label", text: item.label }),
        item.done ? element("span", { class: "visually-hidden", text: ", done" }) : "",
        open ? "" : element("span", { class: "lock", text: "locked" }),
      ],
    );
    button.addEventListener("click", () => {
      const target = railTarget(index);
      const heading = target?.querySelector("h2");
      if (!heading) {
        return;
      }
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    });
    return button;
  });
  rail.replaceChildren(
    ...railButtons,
    element("p", { class: "rail-foot", text: "The next chapter unlocks when you answer this one's checkpoints." }),
  );
  markCurrent();
}

function markCurrent() {
  let current = -1;
  railButtons.forEach((button, index) => {
    const target = railTarget(index);
    if (target && (current === -1 || target.getBoundingClientRect().top < window.innerHeight / 3)) {
      current = index;
    }
  });
  railButtons.forEach((button, index) => {
    if (index === current) {
      button.setAttribute("aria-current", "step");
    } else {
      button.removeAttribute("aria-current");
    }
  });
}

function startLesson(lesson) {
  railItems.push(
    ...lesson.sections.map((section, index) => ({ label: section.title, number: String(index + 1), done: false })),
    { label: "Final quiz", number: "Q", done: false },
  );
  renderRail();
  window.addEventListener("scroll", markCurrent, { passive: true });
  const planned = lesson.plan?.default_depth ?? "detail";
  const remembered = recall("depth");
  const toggle = depthToggle(DEPTHS.some(([value]) => value === remembered) ? remembered : planned);
  document.getElementById("controls").append(toggle.element);
  if (lesson.probe.length === 0) {
    showSection(lesson, 0);
    return;
  }
  probeStage(lesson, (anyWrong) => {
    toggle.show(anyWrong ? "detail" : planned);
    showSection(lesson, 0);
  });
}

async function main() {
  try {
    const lesson = await fetchLesson();
    showHeader(lesson);
    configureViews(lesson);
    followReplies(showReply);
    startLesson(lesson);
  } catch (error) {
    stage.replaceChildren(element("p", { class: "error", text: `Could not load the lesson: ${error.message}` }));
  }
}

main();
