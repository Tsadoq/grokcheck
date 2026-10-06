# Lesson format

A lesson is one JSON file. Its full shape is pinned by [`../schema/lesson.schema.json`](../schema/lesson.schema.json); `grokcheck validate` checks that shape plus the rules a schema cannot express (unique ids, indexes and lines in range, blank markers matching blanks, code files inside the project, claim symbols inside their cited lines). [`example-lesson.json`](example-lesson.json) is a small complete lesson that passes `validate` and uses every question type.

Unknown fields are rejected everywhere. Every text field must hold at least one non-space character.

## Top level

| Field | Required | Meaning |
|-------|----------|---------|
| `schema_version` | yes | Always `2`. Version 1 lessons are rejected; bump the number, since a v1 section's `code` still works as sugar (see below). |
| `title` | yes | The lesson title shown in the browser. |
| `scope.summary` | yes | One sentence on what the lesson covers. |
| `scope.files` | yes | Project-relative paths of the files the lesson covers. |
| `sections` | yes | At least one explanation section, in reading order. |
| `final` | yes | The closed-book final quiz: at least 3 questions. |
| `seed` | no | Integer fixing the shuffled order of `order_steps` and `parsons`; defaults to a hash of the title. |
| `plan` | no | Why the lesson is shaped as it is (see below). |
| `probe` | no | 0 to 2 closed questions asked before the first section (see below). |

## Plan

Every field is required.

| Field | Meaning |
|-------|---------|
| `subject` | One of `change`, `area`, `concept`, `library`, `options`, `decision`, `document`. A `document` lesson may not use the code-only types `trace`, `diff`, `mutation_quiz`, `fix_the_bug`, `spike` and `playground`. |
| `time_budget` | Minutes: `5`, `15` or `30`. A 5-minute lesson has exactly one section. |
| `content` | Which of `structure`, `behaviour`, `change`, `tests` the lesson covers, no repeats. |
| `media` | Element or question types the lesson uses. Each must appear in some section's elements or checkpoints. |
| `rejected` | Element or question types considered and left out. |
| `rationale` | At least one sentence, shown to the reader under "Why this lesson looks like this". |
| `default_depth` | `short` or `detail`: the starting view when no probe answer is wrong. |

## Probe

`probe` holds questions answered before the first section. A wrong answer starts the lesson in the detailed view. They must be a closed type (anything but `open_answer`), share the question id space, and never count toward the score; `results.json` lists them under `probe`.

## Sections

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Identifier, unique among sections. |
| `title` | yes | Section heading. |
| `body` | yes | Markdown lead explanation, shown first. |
| `elements` | no | Typed elements shown after the body, in the order listed. |
| `code` | no | Sugar for a `code` element placed before every entry in `elements`. |
| `checkpoints` | yes | 1 or 2 questions the reader must answer before the next section unlocks. |

Markdown supports headings, paragraphs, bullet and numbered lists, fenced code blocks with a language, inline code, `**bold**`, `*italic*`, and `http(s)` links. It does not render tables, images or raw HTML.

Identifiers start with a letter or digit and hold only letters, digits, `-` and `_`. Question ids are unique across the whole lesson: probe, checkpoints and final together.

## Elements

Every element has a `type` and an optional `depth`: `short` (the default) or `detail`. A `detail` element holds material a reader who already knows the basics can skip; the page hides it when the reader picks the short view.

| `type` | Fields | Shows |
|--------|--------|-------|
| `prose` | `markdown` (required) | More markdown, the same subset as `body`. |
| `code` | `code` (required, a code block), `caption` (optional markdown) | The code, with the caption under it. |

```json
"elements": [
  {"type": "code", "code": {"file": "src/cache.py", "lines": [11, 15]}, "caption": "A miss returns early."},
  {"type": "prose", "markdown": "`move_to_end` is O(1).", "depth": "detail"}
]
```

### `diff`

One hunk of a change. Never type it by hand: `grokcheck diff <path> --old <rev> --new <rev>` prints `{"ok": true, "elements": [...]}` with one ready `diff` element per hunk, numbered from git's hunk headers. Copy the ones you need and add `notes` and `asks`.

| Field | Required | Meaning |
|-------|----------|---------|
| `file`, `old_start`, `new_start`, `lines` | yes | Printed by `grokcheck diff`. Each line is `{"op": " " \| "+" \| "-", "text": ...}`; a start of `0` means that side is empty. |
| `notes` | no | `{"lines": [start, end], "cite": <lines backing>, "text": ...}`. `lines` are new-side line numbers inside the hunk; `cite` is a lines backing, checked the same way. |
| `asks` | no | `{"line": n, "question": ..., "answer": ...}` on one new-side line. Not graded; the page fetches `answer` only when the reader opens the ask. |

```json
{
  "type": "diff", "file": "src/cache.py", "old_start": 17, "new_start": 17,
  "lines": [
    {"op": " ", "text": "        self._items.move_to_end(key)"},
    {"op": "+", "text": "        if len(self._items) > self._capacity:"},
    {"op": "+", "text": "            self._items.popitem(last=False)"}
  ],
  "notes": [{"lines": [18, 19], "cite": {"file": "src/cache.py", "lines": [20, 21]}, "text": "Evicts after every write."}],
  "asks": [{"line": 19, "question": "Why `last=False`?", "answer": "It pops the oldest key."}]
}
```

### `vocab`

The names a reader must know, beside the code they appear in. Clicking a term marks its lines and shows its `detail`; the element counts as done once the reader has opened `min_opened` different terms.

| Field | Required | Meaning |
|-------|----------|---------|
| `terms` | yes | At least one term, see below. |
| `code` | no | A code block. Term `lines` count from 1 at its first line. |
| `min_opened` | no | Terms to open before the element is done, 1 to the number of terms. Default 3. |
| `owners` | no | A legend mapping each owner label to a colour token: `accent`, `good`, `bad`, `warn` or `muted`. It replaces the default owners, so a clinical lesson can tag terms `guideline`, `local protocol` and `common usage`. |

Each term:

| Field | Required | Meaning |
|-------|----------|---------|
| `term`, `definition` | yes | The name and a one-line definition. |
| `owner` | yes | Who defines the name: a label of `owners`, or without that legend `ours`, `library` or `stdlib`. |
| `lines` | no | Lines of `code` where the term appears. Without `code` there are none. |
| `detail` | no | Markdown shown when the term is opened; the definition is shown when it is empty. |
| `library_term` | no | The library's word for the same thing. Give it on one term and the element is a translation table: every term needs it, `null` where the library has no equivalent. |
| `false_friend` | no | `true` when the term sounds like the library's but means something else. |

```json
{
  "type": "vocab", "code": {"file": "src/cache.py", "lines": [11, 15]}, "min_opened": 2,
  "terms": [
    {"term": "key", "owner": "ours", "definition": "The name a value is stored under.", "lines": [1, 2], "library_term": null},
    {"term": "move_to_end", "owner": "stdlib", "definition": "Relinks a key as the newest.", "lines": [4], "library_term": "touch", "false_friend": true}
  ]
}
```

### `trace`

A stepper over a real run. Record the run first with `grokcheck trace record`, which writes `.grokcheck/traces/<trace_id>.json`; `validate` reads it from there. You write the narration and the state shown at each step; the line the run was on comes from the recording.

| Field | Required | Meaning |
|-------|----------|---------|
| `trace_id` | yes | The recording, unique among the section's traces. |
| `title` | yes | Shown above the stepper. |
| `panels` | yes | The state boxes: `{"id", "label", "kind"}`. `kind` is `status` (one line of text), `chips` (a list) or `nullable_chips` (a list, or `null` for "does not exist"). Ids follow the identifier rule and may not be an answer-key name such as `correct` or `why`. |
| `versions` | yes | One or more runs, such as before and after a fix: `{"label", "code", "steps"}`, plus an optional `trace_id` naming its own recording instead of the element's. |
| `gate` | no | The id of a `predict_state` checkpoint of this section on this trace. |

Each step is `{"narration": [...], "state": {...}}`, with an optional `at`. `narration` holds at least one sentence; `state` maps panel ids to the value shown. Authored step `i` shows recorded step `at`, which defaults to `i`, so a short story can pick steps out of a long run.

With a `gate`, the page gets only the line number of the predicted step and every step after it until the reader answers the gate; the answer's feedback carries them.

```json
{
  "type": "trace", "trace_id": "reconnect", "title": "A reader reconnects", "gate": "cp-predict",
  "panels": [{"id": "queue", "label": "Reader's queue", "kind": "nullable_chips"}],
  "versions": [{
    "label": "before", "code": {"file": "src/feed.py", "lines": [40, 50]},
    "steps": [
      {"narration": ["The browser is away; no queue exists."], "state": {"queue": null}},
      {"narration": ["follow() creates an empty queue."], "state": {"queue": []}, "at": 4}
    ]
  }]
}
```

### `spike`

A recorded experiment: its hypothesis, pinned versions and script, with the recorded run held back until the reader predicts it. Run the spike first with `grokcheck spike new` and `grokcheck spike run`, which write `.grokcheck/spikes/<spike_id>/spike.py` and `result.json`; `validate` reads the hypothesis, pins, `exclude-newer` timestamp, script and recorded output from there.

| Field | Required | Meaning |
|-------|----------|---------|
| `spike_id` | yes | The spike directory under `.grokcheck/spikes/`. |
| `gate` | yes | The id of a `predict_output` checkpoint of this section. |

The gate's correct option, or one of its `accepted` answers, must equal the recorded stdout after normalisation (see `fill_blank`), so the gate never marks the run's own output wrong. The page gets the recorded run only once the gate is answered.

```json
{"type": "spike", "spike_id": "sum", "gate": "cp-sum"}
```

### `playground`

A what-if view: the reader moves sliders and sees the recorded state for those input values on a timeline, one row per `rows` entry. Tasks turn green when the reader reaches a state that meets them and stay green; the element counts toward the section gate once every task is met, or at once if it has none.

| Field | Required | Meaning |
|-------|----------|---------|
| `inputs` | yes | At least one `{"id", "label", "min", "max"}` slider, with optional `step` (default 1) and starting `value` (default `min`, on the slider's grid). |
| `constraints` | no | `{"after", "gt"}` pairs of input ids: when `after` is not above `gt`, the page moves `after` to its next value above `gt`. |
| `presets` | no | `{"label", "values"}` buttons; `values` sets every input id to a value on its grid. |
| `rows` | yes | `{"id", "label"}` timeline rows in display order. |
| `variants` | yes | Row ids whose outcome is shown at the row's end and that tasks can test. |
| `ticks` | yes | Timeline columns; every row has one cell per tick. |
| `legend` | yes | `{"cell", "label", "colour"}`: what a cell class means; `colour` is one of `accent`, `good`, `bad`, `warn`, `muted`. |
| `states` | yes | One state per combination of slider values, keyed by the values joined with `,` in `inputs` order (`"2,7"`). A missing or extra key is an error. |
| `tasks` | no | `{"text", "when"}`; `when` lists `{"variant", "outcome", "min_lost"}` conditions that must all hold in one state. `min_lost` defaults to 0. |

A state is `{"cells", "outcomes", "explain"}`: `cells` maps every row id to `ticks` cell classes (`""` or a legend `cell`), `outcomes` maps every variant to `{"outcome", "lost"}` with `outcome` one of `complete`, `lost`, `hang` and `lost` defaulting to 0, and `explain` is markdown shown under the timeline.

```json
{
  "type": "playground",
  "inputs": [{"id": "drop", "label": "Connection drops at tick", "min": 1, "max": 5}, {"id": "back", "label": "Browser reconnects at tick", "min": 2, "max": 8}],
  "constraints": [{"after": "back", "gt": "drop"}],
  "presets": [{"label": "The bug report", "values": {"drop": 2, "back": 7}}],
  "rows": [{"id": "old", "label": "old code"}, {"id": "fixed", "label": "fixed code"}],
  "variants": ["old", "fixed"],
  "ticks": 6,
  "legend": [{"cell": "live", "label": "received live", "colour": "accent"}, {"cell": "lost", "label": "never delivered", "colour": "bad"}],
  "states": {"1,2": {"cells": {"old": ["live", "lost", "live", "live", "live", "live"], "fixed": ["live", "live", "live", "live", "live", "live"]}, "outcomes": {"old": {"outcome": "lost", "lost": 1}, "fixed": {"outcome": "complete"}}, "explain": "The old code never reads the log."}},
  "tasks": [{"text": "Make the old code hang.", "when": [{"variant": "old", "outcome": "hang"}]}]
}
```

The example shows one of its 35 states.

### `diagram`

A Mermaid diagram, drawn in the page's colours. The page loads Mermaid only when a lesson has a diagram.

| Field | Required | Meaning |
|-------|----------|---------|
| `mermaid` | yes | The Mermaid source. |
| `caption` | yes | Markdown under the diagram: what to read from it. |
| `legend` | no | Markdown saying what shapes or line styles mean. |
| `kind` | no | `flowchart` (the default), `sequence`, `state`, `class`, `er` or `before_after`. |

`validate` refuses a diagram with more than 12 nodes, a flowchart edge without a label (`A -->|reads| B`), and a diagram in a section without a checkpoint. `grokcheck diagram check <lesson> --project <root>` also warns on fewer than 7 nodes and on node ids shaped `module.symbol` that no file named after the module mentions, then renders each diagram with `mmdc` when it is installed and reports parse failures; without `mmdc` it prints `"skipped": "mmdc not installed"`.

```json
{"type": "diagram", "mermaid": "flowchart TD\n  put -->|then checks| over{over capacity?}\n  over -->|yes| pop[popitem]", "caption": "A write may evict."}
```

### `options`

An options table. With `reader_first` the page asks the reader to list their own options and criteria first; `criteria` and `options` stay on the server until the reader commits that list, and the committed text is kept in `results.json` under `commits`.

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | An identifier, unique among elements; the reader commits under it. |
| `question` | yes | Markdown: the choice being made. |
| `criteria` | yes | At least one criterion the options are compared on. |
| `options` | yes | At least 3 rows: `name`, `costs`, `assumes`, `standing` (required), `sketch` (a code block) and `constraints` (optional). |
| `reader_first` | no | `true` (the default) withholds the table until the reader commits. |

`standing` is `do_nothing`, `existing_dependency`, `build` or `other`; every table needs one row of each of the first three. A constraint is `{"text", "hard"}` plus, for a soft one, an optional `waivable_by`; a hard constraint cannot name `waivable_by`. Back every claim an option makes with an element claim.

```json
{"type": "options", "id": "opt-cache", "question": "How should reads keep recency?", "criteria": ["read cost"], "options": [
  {"name": "Leave reads alone", "costs": "Eviction ignores reads.", "assumes": "Writes track use.", "standing": "do_nothing"},
  {"name": "functools.lru_cache", "costs": "No eviction hook.", "assumes": "Memoisation is enough.", "standing": "existing_dependency", "constraints": [{"text": "Keys must be hashable.", "hard": true}]},
  {"name": "move_to_end on get", "costs": "One relink per hit.", "assumes": "Hits dominate.", "standing": "build", "constraints": [{"text": "Under 50 lines.", "hard": false, "waivable_by": "the maintainer"}]}
]}
```

### `assumptions`

The claims a choice rests on. The reader rates each with `sure`, `unsure` or `guess`; the page sends the ratings as the answer to `id`, a list with one level per item. Ratings are never graded and land in `results.json` under `assumptions`, next to `checked_by_spike`, so the debrief can compare them with the spike results.

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | An identifier, unique among question ids, since it is answered like one. |
| `items` | yes | At least one `{"claim", "backing"}`, with an optional `checked_by_spike` naming a spike with a recorded result and an optional `verified`. |

The grounding pass judges each item like a claim, addressed as `sections[i].elements[j].items[k]`, and a `contradicted` item is refused.

```json
{"type": "assumptions", "id": "assume-cache", "items": [{"claim": "A hit costs one relink.", "backing": {"spike_id": "read-cost"}, "checked_by_spike": "read-cost"}]}
```

### `video`

A narrated video, usually a concept video chapter or film from `grokcheck_media video`. The page shows it with native controls and a captions track, and never autoplays it. The server streams the two files through `GET /api/media?element=<id>` and reads no other path.

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | An identifier, unique among videos; the media URL names it. |
| `src` | yes | Absolute path to the MP4, inside the directory of the lesson file (`.grokcheck/` for `draft.json`) or `~/.cache/grokcheck/videos/`. |
| `captions` | yes | Absolute path to its WebVTT captions, under the same rule. |
| `duration` | yes | Length in seconds. |
| `transcript` | yes | The narration as text, shown under the video for a reader who cannot or will not watch. |

`validate` refuses a path outside those two places, a file that does not exist, and a video in a section without a checkpoint. Follow a video with checkpoints that ask about what it showed.

```json
{"type": "video", "id": "cancel-ch01", "src": "/home/me/.cache/grokcheck/videos/asyncio-cancellation/ch01/3f2a9c1e5b7d4a60/video.mp4", "captions": "/home/me/.cache/grokcheck/videos/asyncio-cancellation/ch01/3f2a9c1e5b7d4a60/captions.vtt", "duration": 118.4, "transcript": "A request starts a slow database query..."}
```

## Claims

Any element takes an optional `claims` array: the statements it makes, each with exactly one piece of evidence. The page lists them under the element, and the export lists them with their citation.

| Field | Required | Meaning |
|-------|----------|---------|
| `text` | yes | The statement, in markdown. |
| `backing` | yes | The evidence, one of the four forms below. |
| `verified` | no | The grounding pass's verdict: `unchecked` (the default) or `supported`. A `contradicted` claim is rejected, so a lesson cannot serve a statement its evidence refutes. |

`backing` holds exactly one of these key sets; the key names the kind.

| Kind | Fields | Checked by `validate` | Shown as |
|------|--------|-----------------------|----------|
| Lines | `file`, `lines` (inclusive 1-based `[start, end]`), `symbol` (optional), `quote` (optional) | The file sits inside the project, the span exists, `symbol` appears in it, and `quote` appears in it after whitespace runs are collapsed. A file under `.grokcheck/sources/` needs a `quote`. | A button that highlights the cited lines, then the quote. A citation into an ingested PDF shows `[name p.N]`, the page taken from the copy's page markers. |
| Source | `url`, `version` | Nothing; use it for library docs at a pinned version. | `url @ version` |
| Spike | `spike_id` | An identifier. | `spike <id>` |
| Unverified | `reason` | Nothing. | A warning badge with the reason. |

```json
"claims": [
  {"text": "A miss returns before the key moves.", "backing": {"file": "src/cache.py", "lines": [12, 14], "symbol": "move_to_end"}},
  {"text": "`move_to_end` is O(1).", "backing": {"url": "https://docs.python.org/3/library/collections.html", "version": "3.12"}},
  {"text": "Reads never block.", "backing": {"reason": "threading was not traced"}}
]
```

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

Every question has `id`, `type` and `prompt` (markdown). Every type also accepts `explanation`, markdown shown when the answer is revealed: at once for a checkpoint, after submit for a final question. Every type except `fill_blank`, `mutation_quiz`, `fix_the_bug` and `parsons` accepts an optional `code` block shown with the prompt.

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

### `predict_state`

A prediction made with a trace on screen, before the step it asks about is revealed. Allowed only as a checkpoint of the section that holds the trace, so it never counts toward the final score; its answer and confidence still reach the debrief.

| Field | Meaning |
|-------|---------|
| `trace_id` | The trace element it asks about. |
| `version`, `step` | 0-based indexes of the version and authored step being predicted; both must exist. |
| `options`, `correct` | As `single_choice`. |

Graded as `single_choice`. Name it in the trace's `gate` to hold back that step and every later one until it is answered.

### `mutation_quiz`

The reader sees a one-line change to the project and ticks the tests that fail on it. Record the answer key first with `grokcheck mutate <file> <line> [--replace <text>] --project <dir> --test <command...>` (`--test` takes the rest of the line, so it goes last). It checks out `HEAD` in a scratch worktree, runs the tests on the original (they must pass) and on the mutant (they must fail), and prints `failing_tests` and the run's `log`. Remove the worktree with `grokcheck mutate --remove <worktree>` when the quiz does not need it.

| Field | Meaning |
|-------|---------|
| `mutation` | `file` (project-relative), `line` (1-based, must exist) and `replacement`; `null` or absent deletes the line. |
| `tests` | Every test the reader may tick: `name` and `fails`. At least one fails; names are unique. |
| `log` | The tail of the mutant run's output, revealed with the answer. |

Ticking exactly the failing tests is `correct`; any other set is `incorrect`, with no partial credit.

### `fix_the_bug`

The reader repairs a mutant planted by `grokcheck mutate` in its scratch worktree; the server runs `test_command` there and grades the result. Keep the worktree the command printed; do not remove it until the lesson ends.

| Field | Meaning |
|-------|---------|
| `mutation` | As `mutation_quiz`, but `replacement` is required and is the original line, exactly as the project has it now. It is the answer key and stays hidden. |
| `worktree` | The absolute path `grokcheck mutate` printed; it must exist and lie outside the project. |
| `test_command` | The test command as a list of arguments, at least one. |

Passing tests is `correct`, failing tests `incorrect`.

### `change_impact`

The reader reads a proposed change and ticks the callers or tests it affects.

| Field | Meaning |
|-------|---------|
| `change` | The change, in markdown. |
| `candidates` | At least 2 options, each a caller or test; `why` says how the change reaches it, or why it does not. |
| `affected` | Indexes of the affected candidates, at least one, no repeats. |

Graded by set overlap, like `multiple_choice`.

### `parsons`

The reader assembles code from shuffled lines, leaving out the distractors, and indents each line placed.

| Field | Meaning |
|-------|---------|
| `lines` | At least 2 lines in the correct order, each `text` and `indent` (levels from 0). |
| `distractors` | At least one line that does not belong, as an option: `text` and `why`. |

Every line and distractor text must be distinct. The reader sees all texts shuffled (by `seed`) without indentation. Using any distractor is `incorrect`. Every line in order at its indent is `correct`; otherwise `partial`, scored by the longest run of lines in the right relative order, counting only lines at the right indent.

## Outcomes

`results.json` gives every answered question one outcome: `correct`, `incorrect`, `partial`, `needs_review` or `self_rated`. Its `summary` lists the final questions that are `needs_review`, `self_rated`, and `confident_wrong` (rated `sure` but not `correct`).
