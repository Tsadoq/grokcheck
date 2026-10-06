const TOKEN = new URLSearchParams(globalThis.location?.hash.slice(1)).get("t") ?? "";
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

export function fetchLesson() {
  return request("GET", "/api/lesson");
}

/** Feedback whose `grade.reveal.element_payload` carries the trace steps the answered gate withheld, or null. */
export function sendAnswer(questionId, response, confidence) {
  return request("POST", "/api/answer", { question_id: questionId, response, confidence });
}

/** Feedback for the run of a `fix_the_bug` question's own tests, plus `run: {passed, log}`. */
export function runTests(questionId, confidence) {
  return request("POST", "/api/run", { question_id: questionId, confidence });
}

export function askQuestion(sectionId, text, selection = "", mode = "answer") {
  return request("POST", "/api/question", { section_id: sectionId, text, selection, mode });
}

/** The prepared `{question, answer}` on `line` of diff `elementId`, or `{}` when it has none. */
export function askLine(elementId, line) {
  return request("POST", "/api/ask-line", { element_id: elementId, line });
}

export function commitOptions(elementId, text) {
  return request("POST", "/api/commit", { element_id: elementId, text });
}

export function submitQuiz() {
  return request("POST", "/api/submit");
}

export async function followReplies(onReply) {
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
