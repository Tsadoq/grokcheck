import { askQuestion, fetchLesson, followReplies, sendAnswer, submitQuiz } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { codeView, element, renderQuestion, renderReveal } from "./questions.js";

const CONFIDENCE_LEVELS = [
  ["sure", "Sure"],
  ["unsure", "Unsure"],
  ["guess", "Guess"],
];

const stage = document.getElementById("stage");
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
    const thread = await askQuestion(section.id, text.value.trim(), selection);
    threads.append(threadView(thread));
    text.value = "";
    setSelection("");
    status.textContent = "Sent. Your agent's reply will appear below.";
  });

  return element("aside", { class: "ask" }, [
    element("h3", { text: "Questions about this section" }),
    quote,
    clear,
    text,
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

function questionCard(question, { final, onAnswered }) {
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
        onAnswered();
      }
    },
    update,
  );
  return card;
}

function sectionView(section, index, count, onComplete) {
  const view = element("section", { class: "lesson-section", "data-section": section.id }, [
    element("p", { class: "progress", text: `Section ${index + 1} of ${count}` }),
    element("h2", { text: section.title }),
  ]);
  const content = element("div", { class: "section-body", html: renderMarkdown(section.body) });
  view.append(content);
  if (section.code) {
    view.append(codeView(section.code));
  }
  let remaining = section.checkpoints.length;
  view.append(
    element("h3", { text: "Checkpoint" }),
    ...section.checkpoints.map((question) =>
      questionCard(question, {
        final: false,
        onAnswered: () => {
          remaining -= 1;
          if (remaining === 0) {
            onComplete();
          }
        },
      }),
    ),
    askPanel(section, view),
  );
  return view;
}

function showSection(lesson, index) {
  if (index >= lesson.sections.length) {
    offerFinal(lesson);
    return;
  }
  const view = sectionView(lesson.sections[index], index, lesson.sections.length, () => showSection(lesson, index + 1));
  stage.append(view);
  if (index > 0) {
    view.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function offerFinal(lesson) {
  const start = element("button", { type: "button", class: "primary", text: "Start final quiz" });
  const intro = element("section", { class: "final-intro" }, [
    element("h2", { text: "Final quiz" }),
    element("p", {
      text: "The final quiz is closed book: the explanation disappears and asking questions is switched off until you submit. You see how you did after submitting.",
    }),
    start,
  ]);
  start.addEventListener("click", () => startFinal(lesson));
  stage.append(intro);
  intro.scrollIntoView({ behavior: "smooth", block: "start" });
}

function startFinal(lesson) {
  stage.replaceChildren();
  let unanswered = lesson.final.length;
  const submit = element("button", { type: "button", class: "primary", text: "Submit final quiz", disabled: true });
  const status = element("p", { class: "status", "aria-live": "polite" });
  const quiz = element("section", { class: "final-quiz" }, [
    element("h2", { text: "Final quiz" }),
    ...lesson.final.map((question) =>
      questionCard(question, {
        final: true,
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
    element("h2", { text: "Results" }),
    element("p", { class: "score", text: `Final quiz score: ${Math.round(summary.final_score * 100)}%` }),
    summary.confident_wrong.length > 0
      ? element("p", { text: `You were sure but not right on ${summary.confident_wrong.length} question(s); your agent will start there.` })
      : "",
    summary.needs_review.length + summary.self_rated.length > 0
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
  window.scrollTo({ top: 0 });
}

function showHeader(lesson) {
  document.title = `${lesson.title} - grokcheck`;
  document.getElementById("title").textContent = lesson.title;
  const scope = document.getElementById("scope");
  scope.innerHTML = renderMarkdown(lesson.scope.summary);
  if (lesson.scope.files.length > 0) {
    scope.append(element("ul", { class: "files" }, lesson.scope.files.map((file) => element("li", {}, [element("code", { text: file })]))));
  }
}

async function main() {
  try {
    const lesson = await fetchLesson();
    showHeader(lesson);
    followReplies(showReply);
    showSection(lesson, 0);
  } catch (error) {
    stage.replaceChildren(element("p", { class: "error", text: `Could not load the lesson: ${error.message}` }));
  }
}

main();
