# Lesson format

A lesson is one JSON file. Its full shape is pinned by [`../schema/lesson.schema.json`](../schema/lesson.schema.json); `grokcheck validate` checks that shape plus the rules a schema cannot express (unique ids, indexes and lines in range, blank markers matching blanks, code files inside the project, claim symbols inside their cited lines). [`example-lesson.json`](example-lesson.json) is a small complete lesson that passes `validate` and uses every question type except the two that need a view, `select_items` and `fill_table`.

Unknown fields are rejected everywhere. Every text field must hold at least one non-space character.

## Top level

| Field | Required | Meaning |
|-------|----------|---------|
| `schema_version` | yes | `3`. Version `2` lessons still load, but `kinds`, `datasets`, the `view` element, `select_items`, `fill_table` and `predict_state` on a view need `3`. Version 1 lessons are rejected. |
| `title` | yes | The lesson title shown in the browser. |
| `scope.summary` | yes | One sentence on what the lesson covers. |
| `scope.files` | yes | Project-relative paths of the files the lesson covers. |
| `sections` | yes | At least one explanation section, in reading order. |
| `final` | yes | The closed-book final quiz: at least 3 questions. |
| `seed` | no | Integer fixing the shuffled order of `order_steps` and `parsons`; defaults to a hash of the title. |
| `plan` | no | Why the lesson is shaped as it is (see below). |
| `probe` | no | 0 to 2 closed questions asked before the first section (see below). |
| `kinds` | no | The classes of view item, each `{"id", "label", "colour"}`. See [Kinds and datasets](#kinds-and-datasets). |
| `datasets` | no | The recorded datasets the lesson's views draw, each `{"id", "held_out"?}`. |

## Kinds and datasets

A view draws its items from a dataset: rows recorded by `grokcheck record` from a driver script that runs the real code, stored in `.grokcheck/data/<id>.json`. Every dataset a view names must be declared under `datasets`; `validate` loads each one and refuses a file whose rows did not come from the cited code running.

```json
"kinds": [{"id": "prompt", "label": "prompt", "colour": "accent"},
          {"id": "thinking", "label": "thinking", "colour": "replay"}],
"datasets": [{"id": "reconnect", "held_out": {"version": "new", "drop": 3, "lid": 4}},
             {"id": "reconnect-wrong"},
             {"id": "terms"}]
```

| Field | Meaning |
|-------|---------|
| `kinds[].id` | A value of some view's `encode.class` field. Every value that field takes must be a declared kind. |
| `kinds[].label` | Printed on every chip of that kind, so colour is never the only signal. |
| `kinds[].colour` | One of `accent`, `ok`, `warn`, `bad`, `replay`, `num`, `fn`, `muted`. |
| `datasets[].id` | The file `.grokcheck/data/<id>.json`. |
| `datasets[].held_out` | A [selector](#selectors) over the dataset's fields. Section views never show these rows; final and probe questions may. |

A dataset's `source.kind` is `run` (recorded from a driver), `trace` (flattened from a `trace record` file), `authored` (typed rows, each citing project lines) or `wrong` (a copy of another dataset with one or more edited values). Sections may not draw a `wrong` dataset.

The page shows where each view's rows came from, for example "Recorded from `.grokcheck/drivers/reconnect.py` at `a1b2c3d`, 23 cited lines ran."

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

Every element has a `type` and an optional `depth`: `short` (the default) or `detail`. A `detail` element holds material a reader who already knows the basics can skip; the page hides it when the reader picks the short view. The lint wants at least one element other than `prose` and `code` in every section's short view, and never a `diagram` in `detail`; see the media rules in [authoring-guide.md](authoring-guide.md).

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

### `view`

A picture drawn from dataset rows, in one of five layouts. The page never computes anything from the rows: `validate` derives the items (rows plus reserved fields such as `_lost`), and the page draws them and evaluates selectors over them.

```json
{"type": "view", "id": "v-drop", "layout": "lanes", "data": "reconnect",
 "caption": "Which events reach the browser?",
 "where": {"version": "new", "drop": 2, "lid": 2, "running": 0},
 "encode": {"lane": "lane", "x": "seq", "key": "event", "label": "text", "class": "kind",
            "ref_lane": "server",
            "lanes": [{"value": "server", "label": "transcript has"},
                      {"value": "client", "label": "browser received"}]},
 "marks": [{"where": {"lane": "server", "seq": 2}, "text": "drops after frame {seq}", "tone": "bad"}],
 "layers": [{"id": "wire", "label": "Show the wire frames", "fields": ["seq", "kind"]}],
 "notes": [{"where": {"lane": "client", "kind": "thinking"}, "text": "The thinking event arrives as frame 4."}]}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Unique among element and question ids. The page uses it as the DOM id, so `#v-drop` scrolls to it. |
| `layout` | yes | `lanes`, `table`, `steps`, `blocks` or `decision`. |
| `caption` | yes | The question the picture answers. |
| `data` | yes, except for an inline steps view | A declared dataset. |
| `where` | no | A [selector](#selectors) over dataset fields, applied before items are built. Default `{}`. |
| `encode` | per layout | Which dataset field plays which part; every named field must exist in the dataset. |
| `split` | no | A field. One pane per value, in first-appearance order, drawn at the same positions, so two versions sit side by side. |
| `inputs` | no | Controls that choose a scenario, or `{"from": "<view id>"}` to share another view's controls in the same section. |
| `presets` | no | Named scenarios: `{"label", "values": {field: value}}`. |
| `tasks` | no | `{"text", "when": <selector>}`. A task is met when any item of the current scenario matches `when`; the view is done once every task was met. Tasks are not graded. |
| `missing` | when some input combination has no rows | The text the status line shows for such a combination. |
| `marks` | no | `{"where", "text", "tone"?, "place"?}`. `place` is `column` (above the matched item's column, lanes only) or `status`. `text` may hold `{field}`, filled from the first matched item. `tone`: `ok`, `warn`, `bad`, `accent`, `muted`. |
| `layers` | no | `{"id", "label", "fields", "where"?, "on"?}`: a toggle that shows extra fields under the matched items. |
| `notes` | no | `{"where", "text"}`: statements about the matched items. Each is a claim backed by the rows it matches; in a table they fill a "Why" column. |
| `mask`, `fill`, `gate` | no | See [Gates and masks](#gates-and-masks). |

#### Layouts

| Layout | `encode` (required in bold) | Items | Reader answers by |
|--------|-----------------------------|-------|-------------------|
| `lanes` | **`lane`**, **`x`**, **`key`**, **`label`**, `class`, `ref_lane`, `lanes`, `link` | Chips per lane along `x`. With `ref_lane`, a key the reference lane has and another lane lacks becomes a lost item. | `select_items` on masked `?` cells, or tasks |
| `table` | **`columns`** (`[{"field", "label"}]`), **`key`**, `class`, `link` | One row per item; two rows with the same key are an error. | `fill_table` on `fill` cells, or `select_items` on rows |
| `steps` | inline: none; data: **`file`**, **`line`**, **`key`**, `show`, `link` | One step per authored step or per item. | `predict_state` with `view` and `step` |
| `blocks` | **`group`**, **`key`**, **`label`**, `class`, `group_label`, `link` | Chips in one box per group value. | Never gated; its section needs a checkpoint |
| `decision` | none; uses `nodes` | One item per node per scenario. | `select_items` on nodes, or tasks |

`lanes` orders lanes by `encode.lanes`, else by first appearance. `link` names the field whose value links items across the section's views (default `key`): selecting an item highlights every item, step and node with the same link.

An inline `steps` view is a code walk with no dataset:

```json
{"type": "view", "id": "v-walk", "layout": "steps", "caption": "read(), a few lines at a time",
 "steps": [{"code": {"file": "src/stream.py", "lines": [[30, 32], [35, 35]]},
            "note": "A cursor past the end raises Beyond.", "link": ["beyond"]}]}
```

`lines` is one `[start, end]` span or a list of spans, all from one file. A step shows at most 6 lines in total, and its spans sit at most 40 lines apart. Each `note` is a claim backed by the step's lines. A data `steps` view makes one step per item and shows the item's `line` with two lines either side, plus its `show` fields as state chips.

A `decision` view draws the branches a function takes, computed from the lines each recorded run executed:

```json
{"type": "view", "id": "v-read", "layout": "decision", "data": "reconnect",
 "inputs": {"from": "v-scrub"}, "caption": "Which answer does read() pick?",
 "nodes": [
   {"id": "beyond", "label": "cursor > end?", "kind": "check",
    "code": {"file": "src/stream.py", "lines": [32, 32]}, "yes": {"lines": [33, 33]},
    "note": "Checked first, running or not."},
   {"id": "409", "label": "409 Conflict", "kind": "outcome", "tone": "bad",
    "code": {"file": "src/stream.py", "lines": [33, 33]}, "note": "Beyond maps to 409.",
    "example": {"version": "new", "drop": 2, "lid": 4, "running": 1}}],
 "tasks": [{"text": "Make read() answer 409.", "when": {"node": "409", "_taken": true}}]}
```

A node is taken in a scenario when any line of its `code` ran in that run. A check with `yes` answers `"yes"` when a `yes` line ran, else `"no"`. Node files must be cited by the dataset. `example` names input values that have a recorded run; clicking the node moves the shared inputs there. Each node `note` is a claim backed by its lines.

#### Items

Items are the selected rows plus reserved fields, which selectors outside `where` may use:

| Field | Meaning |
|-------|---------|
| `_cell` | Pane, lane and key joined with `\|`, leaving out the parts a layout lacks. The unit a reader picks. |
| `_id` | `_cell`, plus `#n` for the n-th repeat. |
| `_link` | The `link` field's value; a node's id on a decision view. |
| `_pane` | The `split` value. |
| `_lost` | lanes: the reference lane has this key and this lane does not. |
| `_dup` | lanes: this cell occurred earlier in the lane. |
| `_burst` | lanes: another item of the lane shares this column. |
| `_differs` | With `split`: no other pane has the same item. |
| `_taken`, `node`, `kind`, `answer` | decision items. |
| `_edited` | The row was edited in a `wrong` copy. Never sent to the page. |

#### Inputs

```json
"inputs": [{"field": "drop", "label": "Connection drops after frame", "control": "range", "default": 2},
           {"field": "lid", "label": "Last-Event-ID sent back", "control": "range", "default": 2,
            "follows": "drop", "hint": "An EventSource sends the last id it saw."},
           {"field": "running", "label": "Turn 2 still running", "control": "toggle", "default": 0}]
```

An input's values are the distinct values of its field after `where` and held-out rows are applied. `range` needs numbers, `toggle` exactly two values, `select` any. `default` must be one of the values. With `follows`, the input takes the named input's new value whenever it changes, if that value exists. A view with inputs holds the items of every scenario and the page filters them.

#### Gates and masks

A view without a `gate` shows everything. The first view over each dataset should be that whole first case; the lint warns when it is gated.

`gate` names a checkpoint of the same section that names this view: `select_items`, `fill_table`, or `predict_state` with `view`. A gated view has no inputs, and its `where` selects exactly one scenario (one combination of the dataset's recorded inputs, the `split` field aside). Until the gate is answered the page gets:

| Layout | Withheld | Shown instead |
|--------|----------|---------------|
| `lanes` | Every item of each lane `mask` touches; `mask` must cover whole lanes and the view needs `ref_lane` | One `?` cell per reference-lane key, at that key's column |
| `table` | The `fill` fields of items `mask` matches (default all) | Empty cells, filled through `fill_table` |
| `decision` | Which nodes were taken and each check's answer (`mask` not allowed) | Untaken-looking nodes |
| `steps` | The note and state chips from the asked `step` on | The code lines |

With `split`, `mask` may cover one pane, so the reader sees the old pane and predicts the new one. The answer feedback carries the full items and the view unmasks.

A view must be asked about: it needs a `gate`, a same-section checkpoint whose `view` is its id, or `tasks`. A `blocks` view only needs some checkpoint in its section.

Limits: at most 5000 items per view, at most 512 KB per view as JSON, gated payload included, and 4 MB over all views of a lesson.

## Selectors

`where`, `held_out`, `mask`, `answer`, `marks[].where`, `notes[].where`, `layers[].where`, `tasks[].when` and a data backing's `rows` share one JSON grammar:

| Form | Holds when |
|------|------------|
| `{}` | Always. |
| `{"field": value}` | The field equals the value. |
| `{"field": [a, b]}` | The field equals one of them. |
| `{"field": {"gte": 3, "lt": 9}}` | The field is a number within every bound (`gt`, `gte`, `lt`, `lte`). |
| `{"and": [s, ...]}`, `{"or": [s, ...]}`, `{"not": s}` | Every one, at least one, or not the selector. |

Several keys in one object must all hold. A missing field reads as `null`. Equality is strict about types: `true` never equals `1`, while `1` equals `1.0`. `where`, `held_out` and data backings may name dataset fields only; the rest may also name the reserved item fields of their layout.

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
| Data | `data`, `rows` (a selector) | The dataset is declared and `rows` matches at least one of its rows. | `recorded data <id>` |

A view's notes, its steps' notes and its decision nodes' notes are claims too, addressed as `sections[i].elements[j].notes[k]`, `.steps[k]` and `.nodes[k]`. Recorded rows are evidence and are never judged; an authored dataset's rows are claims checked against their `cite`.

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

A prediction made with a trace or a steps view on screen, before the step it asks about is revealed. Allowed only as a checkpoint of the section that holds the trace or view, so it never counts toward the final score; its answer and confidence still reach the debrief.

| Field | Meaning |
|-------|---------|
| `trace_id`, `version` | The trace element it asks about, and the 0-based version. |
| `view` | Instead of `trace_id` and `version`: a `steps` view of the same section. |
| `step` | 0-based index of the step being predicted; it must exist. |
| `options`, `correct` | As `single_choice`. |

Graded as `single_choice`. Name it in the trace's or view's `gate` to hold back that step and every later one until it is answered.

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

### `select_items`

The reader clicks items on a view: the `?` cells of a masked lane, table rows, or decision nodes.

```json
{"type": "select_items", "id": "cp-lost", "view": "v-drop2",
 "prompt": "The browser reconnects with Last-Event-ID 4. Click the frames it never gets.",
 "answer": {"_pane": "new", "lane": "client", "_lost": true},
 "explanation": "Frames 3 and 4 sit between the drop and the cursor."}
```

| Field | Meaning |
|-------|---------|
| `view` | A checkpoint names a view of its section by id; that view has no inputs. A final or probe question holds an inline view object instead. |
| `answer` | A selector over the view's full items. The cells it matches are the answer. |

The candidates are the `?` cells of a masked view, else every item's cell. `validate` refuses an answer that matches nothing, matches every candidate, or matches a cell that is not a candidate. Scored like `multiple_choice`: the exact set is `correct`, otherwise `partial` by overlap.

### `fill_table`

The reader fills the masked cells of a `table` view, choosing each value from a list of the values that field takes anywhere in the dataset.

```json
{"type": "fill_table", "id": "cp-kinds", "view": "v-rules",
 "prompt": "Fill in the kind of frames 3 to 5.",
 "explanation": "The second turn is a prompt, a thinking event and an answer."}
```

The view must be a `table` with `fill`, and in a section this question is its `gate`. The score is the share of blanks filled with the recorded value: `correct` at 1, `partial` above 0, else `incorrect`.

### Questions on an inline view

In `final` and `probe`, `select_items` and `fill_table` carry their view inline: the view object without `id`, `gate`, `inputs`, `notes`, `claims` and `depth`. Its `where` selects one scenario. With `mask` it stays masked for good, since final questions never reveal before submit. Inline views may draw held-out rows and `wrong` datasets, which sections may not. A transfer item is a final question whose inline view shows only held-out rows; a wrong-data item is a final `select_items` over a `wrong` dataset, usually with `"answer": {"_edited": true}` and the prompt "One item is wrong. Which?".

## Outcomes

`results.json` gives every answered question one outcome: `correct`, `incorrect`, `partial`, `needs_review` or `self_rated`. Its `summary` lists the final questions that are `needs_review`, `self_rated`, and `confident_wrong` (rated `sure` but not `correct`).

Each item under `checkpoints`, `final` and `probe` also names its `element`: `view:<layout>` for a question on a view, `trace` for a `predict_state` on a trace, the type of the element the question gates, or `null`. `by_element` gives, per element name, the checkpoints `answered`, how many were `correct`, and their `mean_score`.
