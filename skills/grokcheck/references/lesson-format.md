# Lesson format

A lesson is one JSON file. Its full shape is pinned by [`../schema/lesson.schema.json`](../schema/lesson.schema.json); `grokcheck validate` checks that shape plus the rules a schema cannot express (unique ids, indexes and lines in range, blank markers matching blanks, code files inside the project). [`example-lesson.json`](example-lesson.json) is a small complete lesson that passes `validate` and uses every question type.

Unknown fields are rejected everywhere. Every text field must hold at least one non-space character.

## Top level

| Field | Required | Meaning |
|-------|----------|---------|
| `schema_version` | yes | Always `1`. |
| `title` | yes | The lesson title shown in the browser. |
| `scope.summary` | yes | One sentence on what the lesson covers. |
| `scope.files` | yes | Project-relative paths of the files the lesson covers. |
| `sections` | yes | At least one explanation section, in reading order. |
| `final` | yes | The closed-book final quiz: at least 3 questions. |
| `seed` | no | Integer fixing the shuffled order of `order_steps`; defaults to a hash of the title. |

## Sections

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Identifier, unique among sections. |
| `title` | yes | Section heading. |
| `body` | yes | Markdown explanation. |
| `code` | no | A code block shown under the body. |
| `checkpoints` | yes | 1 or 2 questions the reader must answer before the next section unlocks. |

Markdown supports headings, paragraphs, bullet and numbered lists, fenced code blocks with a language, inline code, `**bold**`, `*italic*`, and `http(s)` links. It does not render tables, images or raw HTML.

Identifiers start with a letter or digit and hold only letters, digits, `-` and `_`. Question ids are unique across the whole lesson, checkpoints and final together.

## Code blocks

A `code` value takes one of two forms.

A reference to a file in the studied project, preferred because it cannot drift from the real code at authoring time:

```json
{"file": "src/cache.py", "lines": [17, 21]}
```

`lines` is an inclusive, 1-based `[start, end]` pair. The file must sit inside the project passed as `--project` and have at least `end` lines. `language` is optional and inferred from the file suffix. `serve` snapshots the resolved lines into the lesson directory, so later edits to the file do not change a running lesson.

Inline code, for snippets that do not exist in the project (a usage example, a modified version for a debugging question):

```json
{"language": "python", "text": "cache = LruCache(1)\nprint(len(cache))"}
```

## Questions

Every question has `id`, `type` and `prompt` (markdown). Every type also accepts `explanation`, markdown shown when the answer is revealed: at once for a checkpoint, after submit for a final question. Every type except `fill_blank` accepts an optional `code` block shown with the prompt.

Options, used by the choice types, are objects of `text` (markdown) and `why`. `why` is shown next to the option on reveal and must say what reasoning or misconception leads a reader to pick it. Option indexes in `correct` count from 0.

Before every reveal the reader rates their confidence as `sure`, `unsure` or `guess`.

### `single_choice`

| Field | Meaning |
|-------|---------|
| `options` | At least 2 options. |
| `correct` | Index of the one correct option. |

Graded `correct` or `incorrect`. Options are shown in authored order; a retake reshuffles them.

### `multiple_choice`

| Field | Meaning |
|-------|---------|
| `options` | At least 2 options. |
| `correct` | List of the correct indexes, at least one, no repeats. |

An exact match is `correct`. Otherwise the score is the overlap of chosen and correct sets over their union: above zero is `partial`, zero is `incorrect`.

### `open_answer`

| Field | Meaning |
|-------|---------|
| `model_answer` | The answer revealed after the reader commits. |
| `rubric` | 2 to 6 binary items; aim for 3 to 5. |

The reader writes an answer, then sees the model answer and ticks each rubric item met or unmet. The outcome is `self_rated` with the fraction of items ticked as the score; the agent re-grades it after submit.

### `predict_output`

`code` is required: the snippet whose output the reader predicts. Answered in one of two modes, never both:

| Mode | Fields | Grading |
|------|--------|---------|
| Choice | `options`, `correct` (one index) | As `single_choice`. |
| Free text | `accepted`: at least one accepted output | Normalised match (see `fill_blank`) is `correct`; anything else is `needs_review`, never `incorrect`. |

### `pick_line`

| Field | Meaning |
|-------|---------|
| `code` | Required: the snippet the reader clicks lines in. |
| `answer_lines` | The correct lines, at least one, no repeats. |

`answer_lines` count from 1 at the snippet's first line, not from the file's line numbers. With `{"file": "a.py", "lines": [17, 21]}`, file line 21 is answer line 5. Graded by set overlap, like `multiple_choice`.

### `order_steps`

| Field | Meaning |
|-------|---------|
| `steps` | At least 2 unique steps, written in the correct order. |

The reader sees the steps shuffled (by `seed`) and reorders them. In-order is `correct`; otherwise `partial`, scored by the longest run of steps left in the right relative order.

### `fill_blank`

| Field | Meaning |
|-------|---------|
| `text` | Code or prose holding one `[[blank:<id>]]` marker per blank. |
| `language` | Optional highlighting language for `text`. |
| `blanks` | One entry per marker: `id`, `accepted` (at least one answer), `case_sensitive` (default `true`), `quote_insensitive` (default `false`). |

Answers are normalised before matching: Unicode NFC, curly quotes straightened, whitespace next to a symbol dropped, other whitespace runs collapsed to one space. `quote_insensitive` also treats `"` and `'` as the same. All blanks matched is `correct`; any miss is `needs_review` with the fraction matched as a provisional score, because an unforeseen answer may still be right.

## Outcomes

`results.json` gives every answered question one outcome: `correct`, `incorrect`, `partial`, `needs_review` or `self_rated`. Its `summary` lists the final questions that are `needs_review`, `self_rated`, and `confident_wrong` (rated `sure` but not `correct`).
