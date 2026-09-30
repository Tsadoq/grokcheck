---
name: grokcheck
description: Use when a developer wants to understand code they did not write, typically code an agent just produced, and prove to themselves that they do. Explains chosen files, a directory, a commit range or the last change as an interactive browser lesson, gates each section behind checkpoint questions, answers the reader's questions live, then runs a closed-book final quiz. Afterwards it re-grades free-text answers, reports where the reader's confidence was miscalibrated, and offers a retake of what they missed. Triggers on "explain this code and quiz me", "help me understand what you wrote", "grokcheck", "test my understanding".
argument-hint: "[files, dir, commit range, or 'last change']"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/grokcheck *)
compatibility: Requires Python 3.11+ and a local browser
---

# grokcheck

Turn a piece of code into a lesson the developer works through in the browser: short explanation sections, each gated by checkpoint questions, then a closed-book final quiz. You write the lesson content; the `grokcheck` CLI serves it, collects answers, and hands events back to you.

## Running commands

Issue every command as one plain Bash call of this exact form, with no `cd` prefix, no environment assignment, and no pipe:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck <command> ... --project <project root>
```

That shape is what the `allowed-tools` rule pre-approves. `<project root>` is the absolute path of the project being studied. Every command prints one JSON line. A failure prints `{"ok": false, "error": ...}` and exits with status 1.

## 1. Agree the scope

Resolve the argument into a concrete file list:

- files or a directory: use them as given;
- a commit range: the files changed in `git diff --name-only <range>`;
- "last change" or no argument: the uncommitted changes, or the last commit when the tree is clean.

Tell the user which files and which behaviour the lesson will cover, in two or three lines, and ask them to confirm or narrow it. A lesson should take 15 to 30 minutes; when the scope is bigger, propose splitting it and teach the most important part first.

## 2. Write the lesson

Read the code in scope, plus the callers and tests that show how it is used. Then write the lesson to `<project root>/.grokcheck/draft.json`, following [references/authoring-guide.md](references/authoring-guide.md) for what to teach and ask, and [references/lesson-format.md](references/lesson-format.md) for the fields. [references/example-lesson.json](references/example-lesson.json) is a complete valid lesson to copy the shape from.

## 3. Validate

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck validate <project root>/.grokcheck/draft.json --project <project root>
```

On failure the output carries `problems`, each with a JSON `path` and a `message`. Fix every one and validate again until it prints `{"ok": true}`.

## 4. Serve

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck serve <project root>/.grokcheck/draft.json --project <project root>
```

It prints `{"lesson_id", "url", "lesson_dir"}` and returns while the server keeps running. It opens the browser when a display exists; always give the user the `url` as well, and tell them to ask questions in the page as they go. Add `--no-open` when the user asks you not to open a browser. Everything about the run lives in `lesson_dir`.

## 5. Wait for events

Run `wait` as a background command (`run_in_background: true`), so the user can keep talking to you while they read:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck wait <lesson_id> --project <project root>
```

It exits with one JSON event, and its exit wakes you. Handle the event, then start the next `wait` at once. Run only one `wait` per lesson at a time: they share a cursor, so two would receive the same event.

| Event | What to do |
|-------|------------|
| `question` | Answer it with `reply` (below), then wait again. |
| `submitted` | Go to step 6. |
| `timeout` | Nothing arrived within 20 minutes; wait again. |
| `closed` | The server has stopped (idle for two hours, or stopped). Tell the user; serve the draft again if they want to continue. |

A `question` event carries `question_id`, `section_id`, `selection` (the text the reader highlighted, empty when they used the Ask box) and `text`. Answer as a tutor, using that section of `lesson.json` and the code it covers as context: address the selection if there is one, stay within what the reader has seen, and do not reveal checkpoint or final answers. Write the reply as markdown to a file in `lesson_dir` and send it:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck reply <lesson_id> <question_id> --file <lesson_dir>/reply-<question_id>.md --project <project root>
```

`--text "<markdown>"` works for a one-line reply. The reply appears under the reader's question in the page.

## 6. Debrief

The `submitted` event carries `results_path` and a `summary`. Read `results.json` at that path. Each entry in `final` and `checkpoints` has `question_id`, `type`, `prompt`, `outcome`, `score`, the reader's `response`, their `confidence` (`sure`, `unsure` or `guess`), and the `reveal` holding the answer key. `score` runs from 0 to 1: for `needs_review` it counts only the blanks that matched, for `self_rated` it is the fraction of rubric items the reader ticked. The outcomes and how each type is graded are in [references/lesson-format.md](references/lesson-format.md). `questions` holds the reader's mid-lesson questions and your replies.

1. **Re-grade every `needs_review` item.** Judge whether the reader's free text means the same as an accepted answer (`reveal.accepted` or `reveal.blanks`). An equivalent answer is correct; say so.
2. **Re-grade every `self_rated` item.** For each rubric item in `reveal.rubric`, decide met or unmet from the reader's `response.text`, using the met and unmet examples in `reveal.explanation`, and compare with the reader's own ticks in `response.met`, one boolean per rubric item in the same order.
3. **Report calibration gaps**: rubric items where your verdict and the reader's tick differ, and every item in `summary.confident_wrong` (rated `sure` but not correct). Confidence that does not match the result is the most useful thing the reader can learn here.
4. **Explain the misses**, confident-but-wrong items first, then the other incorrect, partial and re-graded items. For each, name the misconception behind the answer and point at the code that settles it.
5. Give the corrected final score and one sentence on what the reader has solidly understood.

Keep the debrief in the chat and short: a table of question, reader's answer, verdict, then the explanations. grokcheck also saved a readable `lesson.md` in `lesson_dir`; mention it for later rereading.

## 7. Offer a retake

Offer a closed-book retake of the missed questions, with options reshuffled:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck retake <lesson_id> --project <project root>
```

By default it includes `incorrect`, `partial`, `needs_review` and `confident_wrong` questions. When your re-grade found every `needs_review` answer correct, drop it with `--include incorrect partial confident_wrong`. An error saying no question matches means there is nothing to retake. A retake prints a new `lesson_id` and `url`; return to step 5 with the new id. When the user is done, stop the server with `python3 ${CLAUDE_SKILL_DIR}/grokcheck stop <lesson_id> --project <project root>`; it also exits on its own 10 minutes after submit.

## 8. Without background commands

When background commands are unavailable (another agent, or background tasks disabled), run `wait` in the foreground with a short ceiling so it finishes inside the command timeout, and loop:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck wait <lesson_id> --timeout 100 --project <project root>
```

On `timeout`, run it again; handle `question` and `submitted` as above. Tell the user that you are waiting on the page and that they can interrupt you to talk in the chat.
