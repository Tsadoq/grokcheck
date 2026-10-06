const CURLY_QUOTES = { "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"' };
const SPACE = "\\t-\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const SPACE_AROUND_SYMBOL = new RegExp(`[${SPACE}]*([^\\p{L}\\p{N}_${SPACE}])[${SPACE}]*`, "gu");
const WHITESPACE_RUN = new RegExp(`[${SPACE}]+`, "gu");
const EDGE_SPACE = new RegExp(`^[${SPACE}]+|[${SPACE}]+$`, "gu");
const LINE_BREAK = /\r\n|[\n\v\f\r\x1c-\x1e\x85\u2028\u2029]/;

export class ResponseError extends Error {}

/** A port of grading.py's `grade`: same outcome, score and reveal for a dumped question. */
export function grade(question, response, confidence = null) {
  const [outcome, score, reveal] = gradeQuestion(question, response);
  return { question_id: question.id, outcome, score, response, confidence, reveal: emptyReveal(question.explanation ?? "", reveal) };
}

/** A port of grading.py's `summarise`. */
export function summarise(grades) {
  const ids = (keep) => grades.filter(keep).map((g) => g.question_id);
  return {
    final_score: grades.length ? grades.reduce((total, g) => total + g.score, 0) / grades.length : 0,
    confident_wrong: ids((g) => g.confidence === "sure" && g.outcome !== "correct"),
    needs_review: ids((g) => g.outcome === "needs_review"),
    self_rated: ids((g) => g.outcome === "self_rated"),
  };
}

export function normalise(text, { caseSensitive = true, quoteInsensitive = false } = {}) {
  let out = text.normalize("NFC").replace(/[‘-‟]/g, (char) => CURLY_QUOTES[char] ?? char);
  out = out.replace(SPACE_AROUND_SYMBOL, "$1").replace(WHITESPACE_RUN, " ").replace(EDGE_SPACE, "");
  if (quoteInsensitive) {
    out = out.replaceAll('"', "'");
  }
  return caseSensitive ? out : out.toLowerCase().toUpperCase().toLowerCase();
}

export function emptyReveal(explanation, fields) {
  return {
    explanation,
    why: [],
    correct: [],
    answer_lines: [],
    steps: [],
    accepted: [],
    blanks: [],
    model_answer: "",
    rubric: [],
    tests: [],
    log: "",
    mutation: null,
    lines: [],
    distractors: [],
    element_payload: null,
    ...fields,
  };
}

function gradeQuestion(question, response) {
  const why = (options) => options.map((option) => option.why);
  switch (question.type) {
    case "single_choice":
    case "predict_state":
      return choice(question, response);
    case "predict_output":
      if (Number.isInteger(question.correct)) {
        return choice(question, response);
      }
      return [...freeText(text(response), question.accepted), { accepted: question.accepted }];
    case "multiple_choice":
    case "change_impact": {
      const options = question.options ?? question.candidates;
      const correct = question.correct ?? question.affected;
      const chosen = new Set(items(response).map((item) => index(item, options.length)));
      return [...overlap(chosen, new Set(correct)), { why: why(options), correct }];
    }
    case "pick_line": {
      const count = lineCount(question.code.text);
      const picked = new Set(items(response).map((item) => index(item, count, 1)));
      return [...overlap(picked, new Set(question.answer_lines)), { answer_lines: question.answer_lines }];
    }
    case "order_steps":
      return [...order(response, question.steps), { steps: question.steps }];
    case "fill_blank":
      return [...fill(response, question.blanks), { blanks: question.blanks }];
    case "open_answer":
      return ["self_rated", selfRating(response, question.rubric.length), { model_answer: question.model_answer, rubric: question.rubric }];
    case "mutation_quiz": {
      const failing = new Set(question.tests.filter((test) => test.fails).map((test) => test.name));
      const ticked = ticks(response);
      return [...exact(ticked.size === failing.size && [...ticked].every((name) => failing.has(name))), { tests: question.tests, log: question.log }];
    }
    case "fix_the_bug":
      return [...exact(passed(response)), { mutation: question.mutation }];
    case "parsons":
      return [...parsons(response, question.lines, question.distractors), { lines: question.lines, distractors: question.distractors }];
    default:
      throw new ResponseError(`unknown question type ${question.type}`);
  }
}

function lineCount(source) {
  const lines = source === "" ? [] : source.split(LINE_BREAK);
  return lines.at(-1) === "" ? lines.length - 1 : lines.length;
}

function choice(question, response) {
  const result = exact(index(response, question.options.length) === question.correct);
  return [...result, { why: question.options.map((option) => option.why), correct: [question.correct] }];
}

function freeText(answer, accepted) {
  return matches(answer, accepted) ? ["correct", 1] : ["needs_review", 0];
}

function exact(isCorrect) {
  return isCorrect ? ["correct", 1] : ["incorrect", 0];
}

function overlap(chosen, correct) {
  const union = new Set([...chosen, ...correct]);
  const shared = [...chosen].filter((item) => correct.has(item)).length;
  if (shared === chosen.size && shared === correct.size) {
    return ["correct", 1];
  }
  const score = union.size ? shared / union.size : 0;
  return [score ? "partial" : "incorrect", score];
}

function order(response, steps) {
  const all = items(response);
  const shown = all.filter((step) => typeof step === "string");
  if (!sameMultiset(shown, steps) || shown.length !== all.length) {
    throw new ResponseError("must list every step exactly once");
  }
  const positions = shown.map((step) => steps.indexOf(step));
  if (positions.every((at, i) => i === 0 || positions[i - 1] <= at)) {
    return ["correct", 1];
  }
  return ["partial", longestIncreasing(positions) / steps.length];
}

function sameMultiset(left, right) {
  const a = [...left].sort();
  const b = [...right].sort();
  return a.length === b.length && a.every((item, i) => item === b[i]);
}

function parsons(response, lines, distractors) {
  const placed = items(response).map(placedLine);
  const texts = lines.map((line) => line.text);
  const decoys = new Set(distractors.map((d) => d.text));
  const shown = placed.map(([t]) => t);
  if (new Set(shown).size !== shown.length || !shown.every((t) => texts.includes(t) || decoys.has(t))) {
    throw new ResponseError("must list known lines, each at most once");
  }
  if (shown.some((t) => decoys.has(t))) {
    return ["incorrect", 0];
  }
  if (placed.length === lines.length && placed.every(([t, indent], i) => t === lines[i].text && indent === lines[i].indent)) {
    return ["correct", 1];
  }
  const positions = placed.filter(([t, indent]) => indent === lines[texts.indexOf(t)].indent).map(([t]) => texts.indexOf(t));
  const score = longestIncreasing(positions) / lines.length;
  return [score ? "partial" : "incorrect", score];
}

function placedLine(item) {
  const isObject = item !== null && typeof item === "object" && !Array.isArray(item);
  const t = isObject ? item.text : undefined;
  const indent = isObject ? item.indent : undefined;
  if (typeof t !== "string" || !Number.isInteger(indent)) {
    throw new ResponseError("each placed line must carry 'text' and an integer 'indent'");
  }
  return [t, indent];
}

function longestIncreasing(values) {
  const tails = [];
  for (const value of values) {
    let low = 0;
    let high = tails.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (tails[mid] < value) {
        low = mid + 1;
      } else {
        high = mid;
      }
    }
    tails[low] = value;
  }
  return tails.length;
}

function fill(response, blanks) {
  const answers = blankAnswers(response, blanks);
  const matched = blanks.filter((blank) =>
    matches(answers[blank.id], blank.accepted, { caseSensitive: blank.case_sensitive, quoteInsensitive: blank.quote_insensitive }),
  ).length;
  return matched === blanks.length ? ["correct", 1] : ["needs_review", matched / blanks.length];
}

function blankAnswers(response, blanks) {
  if (typeof response === "string" && blanks.length === 1) {
    return { [blanks[0].id]: response };
  }
  if (response === null || typeof response !== "object" || Array.isArray(response)) {
    throw new ResponseError("must map each blank id to its answer");
  }
  const answers = {};
  for (const blank of blanks) {
    const answer = Object.hasOwn(response, blank.id) ? response[blank.id] : undefined;
    if (typeof answer !== "string") {
      throw new ResponseError(`blank '${blank.id}' must have a text answer`);
    }
    answers[blank.id] = answer;
  }
  return answers;
}

function matches(answer, accepted, options = {}) {
  const given = normalise(answer, options);
  return accepted.some((candidate) => normalise(candidate, options) === given);
}

function selfRating(response, rubricSize) {
  const met = response !== null && typeof response === "object" && !Array.isArray(response) ? response.met : undefined;
  if (!Array.isArray(met) || met.length !== rubricSize || !met.every((item) => typeof item === "boolean")) {
    throw new ResponseError(`must carry 'met' with one boolean per rubric item (${rubricSize})`);
  }
  return met.filter(Boolean).length / rubricSize;
}

function ticks(response) {
  const ticked = items(response);
  if (!ticked.every((item) => typeof item === "string")) {
    throw new ResponseError("must list test names");
  }
  return new Set(ticked);
}

function passed(response) {
  const value = response !== null && typeof response === "object" && !Array.isArray(response) ? response.passed : undefined;
  if (typeof value !== "boolean") {
    throw new ResponseError("must carry 'passed' as true or false");
  }
  return value;
}

function index(value, count, first = 0) {
  if (!Number.isInteger(value) || value < first || value >= first + count) {
    throw new ResponseError(`${JSON.stringify(value)} is not between ${first} and ${first + count - 1}`);
  }
  return value;
}

function items(response) {
  if (!Array.isArray(response)) {
    throw new ResponseError("must be a list");
  }
  return response;
}

function text(response) {
  if (typeof response !== "string") {
    throw new ResponseError("must be text");
  }
  return response;
}
