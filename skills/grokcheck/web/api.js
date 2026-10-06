import { emptyReveal, grade, summarise } from "./grading.js";

const TOKEN = new URLSearchParams(globalThis.location?.hash.slice(1)).get("t") ?? "";
const EMBEDDED = globalThis.document?.getElementById?.("grokcheck-lesson");
const SKIPPED_OFFLINE = new Set(["mutation_quiz", "fix_the_bug"]);
const REPLY_HOLD_SECONDS = 25;
const RETRY_DELAY_MS = 3000;

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const headers = { "X-Grokcheck-Token": TOKEN };
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, payload.error ?? `request failed with ${response.status}`);
  }
  return payload;
}

const live = {
  fetchLesson: () => request("GET", "/api/lesson"),
  sendAnswer: (questionId, response, confidence) =>
    request("POST", "/api/answer", { question_id: questionId, response, confidence }),
  runTests: (questionId, confidence) => request("POST", "/api/run", { question_id: questionId, confidence }),
  askQuestion: (sectionId, text, selection = "", mode = "answer") =>
    request("POST", "/api/question", { section_id: sectionId, text, selection, mode }),
  askLine: (elementId, line) => request("POST", "/api/ask-line", { element_id: elementId, line }),
  commitOptions: (elementId, text) => request("POST", "/api/commit", { element_id: elementId, text }),
  submitQuiz: () => request("POST", "/api/submit"),
  followReplies: pollReplies,
  mediaUrl: (elementId, file) =>
    `/api/media?element=${encodeURIComponent(elementId)}&file=${file}&t=${encodeURIComponent(TOKEN)}`,
};

async function pollReplies(onReply) {
  let after = 0;
  for (;;) {
    try {
      const { replies } = await request("GET", `/api/replies?after=${after}&timeout=${REPLY_HOLD_SECONDS}`);
      for (const reply of replies) {
        after = Math.max(after, reply.seq);
        onReply(reply.question_id, reply.markdown);
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    }
  }
}

/** A backend for an exported page: grades in the browser from the answer keys embedded beside the lesson. */
function staticBackend({ lesson, keys, gates = {}, options = {}, asks = {}, media = {} }) {
  const grades = new Map();
  const finals = new Set(lesson.final.map((question) => question.id));
  const needsGrokcheck = () => Promise.reject(new Error("this needs grokcheck running in the project"));
  const graded = (question) => {
    const found = grades.get(question.id);
    if (found) {
      return found;
    }
    const key = keys[question.id];
    return SKIPPED_OFFLINE.has(key.type)
      ? { question_id: key.id, outcome: "skipped", score: 0, response: null, confidence: null, reveal: emptyReveal(key.explanation ?? "", { tests: key.tests ?? [], log: key.log ?? "", mutation: key.mutation ?? null }) }
      : null;
  };
  const item = (question, result) => ({ ...result, type: question.type, prompt: question.prompt });
  return {
    fetchLesson: async () => lesson,
    async sendAnswer(questionId, response, confidence) {
      const key = keys[questionId];
      if (!key) {
        return { question_id: questionId, grade: null };
      }
      const result = grade(key, response, confidence);
      grades.set(questionId, result);
      if (finals.has(questionId)) {
        return { question_id: questionId, grade: null };
      }
      return { question_id: questionId, grade: { ...result, reveal: { ...result.reveal, element_payload: gates[questionId] ?? null } } };
    },
    runTests: needsGrokcheck,
    askQuestion: needsGrokcheck,
    askLine: async (elementId, line) => asks[elementId]?.[line] ?? {},
    async commitOptions(elementId, text) {
      if (!text.trim()) {
        throw new Error("list your options before committing");
      }
      return options[elementId];
    },
    async submitQuiz() {
      const final = lesson.final.map((question) => {
        const result = graded(question);
        if (!result) {
          throw new Error(`unanswered final question: ${question.id}`);
        }
        return item(question, result);
      });
      const checkpoints = lesson.sections
        .flatMap((section) => section.checkpoints)
        .filter((question) => grades.has(question.id))
        .map((question) => item(question, grades.get(question.id)));
      const scored = final.filter((result) => result.outcome !== "skipped");
      return { title: lesson.title, summary: summarise(scored), final, checkpoints, probe: [], questions: [], commits: [], assumptions: [] };
    },
    followReplies: () => {},
    mediaUrl: (elementId, file) => media[elementId]?.[file] ?? null,
  };
}

const backend = EMBEDDED ? staticBackend(JSON.parse(EMBEDDED.textContent)) : live;

/** True on an exported page, where nothing reaches the agent or the project. */
export const offline = Boolean(EMBEDDED);
export const skippedOffline = (question) => offline && SKIPPED_OFFLINE.has(question.type);
export const fetchLesson = backend.fetchLesson;
/** Feedback whose `grade.reveal.element_payload` carries the trace steps the answered gate withheld, or null. */
export const sendAnswer = backend.sendAnswer;
/** Feedback for the run of a `fix_the_bug` question's own tests, plus `run: {passed, log}`. */
export const runTests = backend.runTests;
export const askQuestion = backend.askQuestion;
/** The prepared `{question, answer}` on `line` of diff `elementId`, or `{}` when it has none. */
export const askLine = backend.askLine;
export const commitOptions = backend.commitOptions;
export const submitQuiz = backend.submitQuiz;
export const followReplies = backend.followReplies;
/** The URL of video `elementId`'s `file` ("video" or "captions"), or null when an export left it out. */
export const mediaUrl = backend.mediaUrl;
