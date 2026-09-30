const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t") ?? "";
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

export function sendAnswer(questionId, response, confidence) {
  return request("POST", "/api/answer", { question_id: questionId, response, confidence });
}

export function askQuestion(sectionId, text, selection = "") {
  return request("POST", "/api/question", { section_id: sectionId, text, selection });
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
