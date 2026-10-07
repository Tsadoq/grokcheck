---
name: grokcheck
description: Use when a developer wants to understand something and prove to themselves that they do, typically code an agent or a colleague just produced. Takes files, a directory, a commit range, the last change, a library name, a choice between options, a decision already made, or a document, and turns it into an interactive browser lesson whose sections are gated by checkpoint questions, whose claims are checked against the code before serving, and which ends in a closed-book final quiz. Answers the reader's questions live, then re-grades free-text answers, reports where the reader's confidence was miscalibrated, and offers a retake and spaced re-tests of what they missed. Triggers on "explain this code and quiz me", "help me understand what you wrote", "teach me this library", "help me choose between X and Y", "grokcheck", "test my understanding".
argument-hint: "[files, dir, commit range, 'last change', library, 'X vs Y', question or document]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/grokcheck *), Bash(python3 ${CLAUDE_SKILL_DIR}/../grokcheck-media/grokcheck_media *)
compatibility: Requires Python 3.11+ and a local browser
---

# grokcheck

Turn a subject into a lesson the developer works through in the browser: short explanation sections, each gated by checkpoint questions, then a closed-book final quiz. You plan the lesson, subagents write its sections, a fresh-context subagent checks its claims, and the `grokcheck` CLI serves it, collects answers, and hands events back to you.

## Running commands

Issue every command as one plain Bash call of this exact form, with no `cd` prefix, no environment assignment, and no pipe:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck <command> ... --project <project root>
```

That shape is what the `allowed-tools` rule pre-approves. `<project root>` is the absolute path of the project being studied. Every command prints one JSON line. A failure prints `{"ok": false, "error": ...}` and exits with status 1. Give every subagent this section verbatim, with `${CLAUDE_SKILL_DIR}` already expanded.

## Videos

When the video tools are installed, every section opens with a one-minute narrated video. The media commands take the same plain form, from the sibling skill:

```
python3 ${CLAUDE_SKILL_DIR}/../grokcheck-media/grokcheck_media <command> ...
```

Before planning a lesson, run `doctor` with that form and act on its `status`:

| `status` | What to do |
|----------|------------|
| `ready` | Add videos as sections become final (below). |
| `missing` | Ask the user once whether to install the video tools: about 2.3 GB and a few minutes, kept in the plugin's data folder, installed in the background while you write the lesson. On yes, run `setup` as a background command (`run_in_background: true`); on no, run `setup --decline`, which records the answer. |
| `out_of_date` | The user said yes before and an update changed the pinned versions: run `setup` in the background without asking. |
| `declined` | Do not ask. Run `setup` only when the user asks for video. |

Once `doctor` says `ready`, start a section's chapter as soon as its elements and gates are final; that may run alongside grounding in step 4. When step 4 changes a claim a chapter cites, render that chapter again; the others stay. When setup is still running at step 5, serve without videos and tell the user the next lesson will have them.

1. Launch one subagent per section as it becomes final, sections that are final together in one message. Brief each with the section's id, its part file, the lines its claims cite, the section's gate and checkpoint questions with their correct answers, this section and `${CLAUDE_SKILL_DIR}/../grokcheck-media/references/video-script-rules.md`. The subagent writes a one-minute chapter script (`word_budget` 150, `concept_id` the lesson title in lowercase words joined by `-`, `chapter_id` the section id) from the section to `<project root>/.grokcheck/video/<section id>.json`, following the rules. The rule is one picture that changes: the chapter animates the section's diagram or trace as a `scene` (a section's Mermaid `flowchart` can be pasted in as is), building it up beat by beat, highlighting the path being narrated and moving a marker along it. Code appears only when one line must be read, at most one excerpt of 5 lines, in a band under the scene. The narration says what the code does in plain words and names at most two identifiers. The chapter teaches the first case the section shows and never narrates or shows the answer to a gate or checkpoint question. It runs `video chapter <that file> --renderer manim --out-dir <project root>/.grokcheck/video --project <project root>` with `--avoid "<answer>"` once per gate's correct answer, which refuses a script that gives one away. It reads the `contact_sheet` image and, with `video frames <path> --at <seconds>... --out-dir <dir>`, full-size frames of at least the busiest beats (the code beat, the beat with the most boxes, the last beat), then the `transcript_diff` and the `claims` (judging each claim from its evidence alone), fixes the script and runs again until all are clean. It reports `path`, `captions`, `duration`, the narration as one paragraph, and any claim it could not support.
2. Put a `video` element first in each section, in the short view (no `depth`): `id` `<section id>-video`, `src` and `captions` from the report, `duration`, and the narration as `transcript`. Drop a chapter whose claims it could not support rather than serve it.
3. Validate again, then serve.

## 1. Agree the scope

Pass the user's argument through as words:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck scope <words>... --project <project root>
```

It prints `subject` (`change`, `area`, `concept`, `library`, `options` or `decision`), `files`, `commit`, `pins` (the project's pinned versions of the packages named) and a `reason` for the guess. No words means the last change: the uncommitted files, or the last commit when the tree is clean. A revision or range anywhere in the words lists that change's files, and a leading subject word, as in `decision a1b2c3d..HEAD`, sets the subject.

Pick a time budget of 5, 15 or 30 minutes: 15 by default, 5 when the user is about to act (push, approve, merge) and wants a quick check, 30 for a large area or when they ask for depth. Then state subject, files and time budget together in two or three lines, with the `reason` in a few words, and ask the user to confirm or correct any of them:

> Subject: a change (commit range `main~3..main`). Files: `src/cache.py`, `src/store.py`, `tests/test_cache.py`. Time: 15 minutes. Confirm, or correct any of these.

When the scope does not fit the budget, propose splitting it and teach the most important part first.

When the subject is a document (a paper, a guideline, a web page or a knowledge-base file), ingest it first, so the lesson cites a line-addressable copy:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck ingest <path or URL>... --project <project root>
```

It prints one JSON line per origin. A source record's `path` is the copy under `.grokcheck/sources/` that claims cite. A line with `"needs_transcription": true` is a PDF that `pdftotext` could not read: read the PDF with the Read tool in page ranges, write the transcription to `target` with a `<!-- page N -->` line at the start of each page, then run `ingest` on `target` so the manifest records its hash. `sources --project <project root>` lists what is already ingested. Author only from the copies, never from memory of the document.

## 2. Plan the lesson

Read the code in scope, plus the callers and tests that show how it is used. Pick the section plan from "Choosing the shape" in [references/authoring-guide.md](references/authoring-guide.md): the subject sets the section order, the content (structure, behaviour, change, tests) sets the media, and the time budget sets how many sections and questions. Follow the guide's media rules: every section shows its idea with a non-code element in the short view, and behaviour gets a view over a recorded dataset (preferred), a steps view or a trace stepper, with a predict gate. Time is never a reason to reject a medium, since traces and spikes are recorded in parallel in step 3.

Write the `plan` block: subject, time budget, content, the media you will use and the ones you rejected, a rationale the reader sees under "Why this lesson looks like this", and the default depth. Write one or two closed `probe` questions on the prior knowledge the lesson leans on most; a wrong probe answer starts the reader in the detailed view. When `<project root>/.grokcheck/lessons/*/results.json` shows the reader already answered a probe-worthy idea correctly, probe a different one. History only chooses which probes to ask; it never sets the depth or skips the probe.

When the lesson shows behaviour, name its example dataset in the plan: one driver script under `.grokcheck/drivers/<id>.py` that calls the real code, the files it cites, and the matrix of inputs the views will vary (for example connection drop point, Last-Event-ID, whether a turn is running). One running example is the default; a second recorded dataset needs a rationale sentence naming it. Only when no driver can run the code, plan an authored dataset and write the reason in the rationale; time is not a reason.

Choose every identifier now, before the fan-out: section ids, question id prefixes per section (`s1-`, `s2-`, ...), trace ids, dataset ids, spike ids and their output paths. Subagents then work without waiting on each other.

## 3. Author in parallel

Fan out in one message, one subagent per job, and wait for all of them:

- **One per section.** Brief it with the section's id, title and place in the plan, the files and line ranges it covers, the subject's rules from the authoring guide, the ids it owns, and the trace or spike ids it may cite. It reads [references/authoring-guide.md](references/authoring-guide.md) and [references/lesson-format.md](references/lesson-format.md), writes the section's body, elements, claims and checkpoints, and saves one section object to `<project root>/.grokcheck/parts/<section id>.json`. A diff element starts from `diff <path> --old <rev> --new <rev>`, which prints its hunks, so no line number is typed by hand. [references/example-lesson.json](references/example-lesson.json) is a complete valid lesson to copy the shape from.
- **One per dataset**, when the plan names one. It writes the driver (at most 60 lines; it calls `emit(field=value, ...)` once per row and reads this run's inputs from `INPUTS`; it prints nothing the recorder reads), then runs `record <driver> --id <dataset id> --cite <files>... --matrix <name>=<a>..<b> <name>=<x>,<y> --project <project root>`. Each matrix combination runs in a fresh process. When the code needs the project's own environment, add `--python <interpreter>`, for example `--python <project root>/.venv/bin/python`; the recorder is injected, so grokcheck need not be installed there. The record is refused when a run emitted rows without running a cited line inside a function, so a driver that only prints values fails. Then `data show <dataset id> --where '<selector json>' --project <project root>` to check the rows, and `data wrong <dataset id> --out <dataset id>-wrong --edit '<selector json>' <field>=<value> --project <project root>` for the final's wrong-data item. For an authored fallback, `data author <dataset id> --file <rows.json> --reason <text>`, every row with a `cite`. `record --from-trace <trace file> --id <dataset id>` turns a recorded trace into rows.
- **One per trace**, when the plan names `trace`: `trace record <script> --cite <files>... --out <project root>/.grokcheck/traces/<trace id>.json`, with a small driver script that exercises the path the lesson follows.
- **One per spike**, when the plan names spikes: `spike check <name>` for every package, `spike new <spike id> --hypothesis <text> --dep <name>==<version> --exclude-newer <timestamp> --file <script>` with the version from `scope`'s `pins`, then `spike run <spike id>`. [references/spike-template.py](references/spike-template.py) shows the script shape.
- **One per mutation**, when the plan names a mutation quiz or a fix-the-bug question: `mutate`, which plants the mutation in a scratch worktree and proves a test catches it.
- **One for diagrams**, when the plan names `diagram`: run the diagram check on each diagram and fix what it reports.

These commands write to their own paths under `.grokcheck/` and never to `draft.json`, so they are safe to run together. While the subagents work, write the `final` quiz from the plan. Then join once: collect the parts in plan order into `<project root>/.grokcheck/draft.json` with `plan`, `probe` and `final`, and revise the final where a section turned out differently from the plan.

When subagents are unavailable, do the same jobs yourself, one after another.

## 4. Validate and ground

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck validate <project root>/.grokcheck/draft.json --strict --project <project root>
```

On failure the output carries `problems`, each with a JSON `path` and a `message`. With `--strict` every lint warning is a problem too, the media rules included. Fix every one and validate again until it prints `{"ok": true}`.

Then check the claims against their evidence. Start one fresh-context subagent and give it only these instructions, never the draft, the code or this conversation:

> Run `python3 <skill dir>/grokcheck ground <project root>/.grokcheck/draft.json --project <project root>`. It prints a `manifest`: per claim still to judge a `claim_id`, the claim `text`, its `backing` and the `evidence` (the cited lines, a spike's output, or recorded rows with where they came from). A claim id `data:<dataset>.rows[<k>]` is a row typed by hand, judged on its cited lines. For each claim, judge only from its `evidence`: `supported` when the evidence shows the claim is true, `contradicted` when it shows it is false, `not_shown` when it shows neither or the evidence is `null`. Do not read any other file. Write a JSON list of `{"claim_id", "verdict", "note"}` to `<project root>/.grokcheck/verdicts.json`, with a one-sentence `note` for every verdict other than `supported`.

Apply its verdicts:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck ground <project root>/.grokcheck/draft.json --verdicts <project root>/.grokcheck/verdicts.json --project <project root>
```

It prints the counts and the `notes`, and stores with each verdict a hash of the claim's text, backing and evidence. Rewrite every `contradicted` claim so it says what the evidence shows, along with any prose, checkpoint or final question that rested on it; never delete a claim to get past the check. For a `not_shown` claim a question depends on, cite a better span or back it with a spike. A verdict holds only while its hash matches, so a rewritten claim, or one whose cited lines changed, counts as unchecked again.

Then start a new grounding subagent with the same instructions. `ground` lists only claims with no verdict, a `not_shown` verdict, or a hash that no longer matches, so the new subagent judges only what changed; `--all` lists every claim. Apply its verdicts and repeat. Stop when `ground` lists nothing, or only `not_shown` claims you have justified: no question depends on them, or you have read their evidence yourself and it holds. Then validate once more.

## 5. Serve

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck serve <project root>/.grokcheck/draft.json --project <project root>
```

It prints `{"lesson_id", "url", "lesson_dir"}` and returns while the server keeps running. It opens the browser when a display exists; always give the user the `url` as well, and tell them to ask questions in the page as they go. Add `--no-open` when the user asks you not to open a browser. Everything about the run lives in `lesson_dir`.

## 6. Wait for events

Run `wait` as a background command (`run_in_background: true`), so the user can keep talking to you while they read:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck wait <lesson_id> --project <project root>
```

It exits with one JSON event, and its exit wakes you. Handle the event, then start the next `wait` at once. Run only one `wait` per lesson at a time: they share a cursor, so two would receive the same event.

| Event | What to do |
|-------|------------|
| `question` | Answer it with `reply` (below), then wait again. |
| `submitted` | Go to step 7. |
| `timeout` | Nothing arrived within 20 minutes; wait again. |
| `closed` | The server has stopped (idle for two hours, or stopped). Tell the user; serve the draft again if they want to continue. |

A `question` event carries `question_id`, `section_id`, `selection` (the text the reader highlighted, empty when they used the Ask box), `text` and `mode`. Answer as a tutor, using that section of `lesson.json` and the code it covers as context: address the selection if there is one, stay within what the reader has seen, and never reveal a checkpoint or final answer, in either mode.

With `mode: "socratic"` the reader asked to be questioned instead of answered. Reply with one guiding question grounded in the cited lines and the section checkpoint's `reveal`, never with the answer. After three Socratic turns on the same thread, offer a direct answer.

Write the reply as markdown to a file in `lesson_dir` and send it:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck reply <lesson_id> <question_id> --file <lesson_dir>/reply-<question_id>.md --project <project root>
```

`--text "<markdown>"` works for a one-line reply. The reply appears under the reader's question in the page.

## 7. Debrief

The `submitted` event carries `results_path` and a `summary`. Read `results.json` at that path. Each entry in `final` and `checkpoints` has `question_id`, `type`, `prompt`, `outcome`, `score`, the reader's `response`, their `confidence` (`sure`, `unsure` or `guess`), and the `reveal` holding the answer key. `score` runs from 0 to 1: for `needs_review` it counts only the blanks that matched, for `self_rated` it is the fraction of rubric items the reader ticked. The outcomes and how each type is graded are in [references/lesson-format.md](references/lesson-format.md). `questions` holds the reader's mid-lesson questions and your replies.

1. **Re-grade every `needs_review` item.** Judge whether the reader's free text means the same as an accepted answer (`reveal.accepted` or `reveal.blanks`). An equivalent answer is correct; say so.
2. **Re-grade every `self_rated` item.** For each rubric item in `reveal.rubric`, decide met or unmet from the reader's `response.text`, using the met and unmet examples in `reveal.explanation`, and compare with the reader's own ticks in `response.met`, one boolean per rubric item in the same order.
3. **Report calibration gaps**: rubric items where your verdict and the reader's tick differ, and every item in `summary.confident_wrong` (rated `sure` but not correct). Confidence that does not match the result is the most useful thing the reader can learn here. Report the wrong-artefact item on its own line: readers judge wrong code at chance while staying confident.
4. **Explain the misses**, confident-but-wrong items first, then the other incorrect, partial and re-graded items. For each, name the misconception behind the answer and point at the code that settles it.
5. Give the corrected final score and one sentence on what the reader has solidly understood.

Keep the debrief in the chat and short: a table of question, reader's answer, verdict, then the explanations. grokcheck also saved a readable `lesson.md` in `lesson_dir`; mention it for later rereading.

Close with two offers. Submit schedules every missed or confident-wrong final question and every wrong predict gate for spaced re-tests on a ladder of 1, 3, 7, 21 and 60 days: a correct re-test moves the item one step up, a wrong one sends it back to 1 day, and a correct re-test on the last step retires it. `due --project <project root>` prints `{"due": [...], "stale": [...]}`; a stale item's cited lines changed in git since its lesson, so rewrite that question in a new lesson rather than serving it. `due --serve --project <project root>` serves the due final questions as one retake and prints a `lesson_id` and `url` like `serve`; return to step 6 with it. Due gates need their trace and are only listed. The misses can also be exported as cards: `export <lesson_id> --format anki --project <project root>`, or `export <lesson_id> --format obsidian --vault <folder> --project <project root>` for a new note in an Obsidian vault.

## 8. Offer a retake

Offer a closed-book retake of the missed questions, with options reshuffled:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck retake <lesson_id> --project <project root>
```

By default it includes `incorrect`, `partial`, `needs_review` and `confident_wrong` questions. When your re-grade found every `needs_review` answer correct, drop it with `--include incorrect partial confident_wrong`. An error saying no question matches means there is nothing to retake. A retake prints a new `lesson_id` and `url`; return to step 6 with the new id. When the user is done, stop the server with `python3 ${CLAUDE_SKILL_DIR}/grokcheck stop <lesson_id> --project <project root>`; it also exits on its own 10 minutes after submit.

When the user says the code or the discussion moved since a lesson, run `python3 ${CLAUDE_SKILL_DIR}/grokcheck refresh <project root>/.grokcheck/draft.json --project <project root>`. It reruns the lesson's spikes and checks its cited lines against HEAD, counting the lesson as written at the last commit before the file was saved (`since`). It prints `stale_claims` (claim paths whose cited lines or source copy changed), `changed_spikes` (each with the lesson paths that cite it and the old and new output) `moved_files` (scope files changed since `since`) and `stale_data` (each declared dataset whose cited files or driver changed, or the reason it no longer loads), and exits 0 even when something is stale. Rewrite only the named claims and their sections, re-record each changed spike with `spike run` and each stale dataset with `record`, then return to step 4 to validate and ground, and serve again.

## 9. Without background commands

When background commands are unavailable (another agent, or background tasks disabled), run `wait` in the foreground with a short ceiling so it finishes inside the command timeout, and loop:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck wait <lesson_id> --timeout 100 --project <project root>
```

On `timeout`, run it again; handle `question` and `submitted` as above. Tell the user that you are waiting on the page and that they can interrupt you to talk in the chat.
