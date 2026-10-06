# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-06

### Added

- Subjects: `grokcheck scope` guesses whether a lesson is about a change, an area, a concept, a library, options, a decision or a document, and the authoring guide gives each its own section plan.
- Lesson plan and probe: a `plan` block with the media used and rejected, one or two probe questions that set the starting depth, a short and detailed view, and a "Why this lesson looks like this" disclosure.
- Claims and grounding: elements carry claims backed by cited lines, a spike or a source, and `grokcheck ground` lets a fresh-context agent judge each claim against its evidence before the lesson is served.
- Sources: `grokcheck ingest` copies papers, guidelines and web pages into `.grokcheck/sources/` as line-addressable text that claims can quote.
- Elements: annotated diffs from `grokcheck diff`, vocabulary tables, trace steppers over runs recorded with `grokcheck trace record`, spikes, playgrounds, Mermaid diagrams (vendored, loaded only when used), options and assumptions tables, and video.
- Gated reveal: a trace step or a spike result stays on the server until the reader answers the checkpoint that gates it.
- Spikes: `grokcheck spike check`, `spike new`, `spike run` and `spike rerun` run one-question experiments against pinned versions in a throwaway uv environment, or in a container for packages the project does not already lock.
- Question types: `predict_state`, `mutation_quiz` and `fix_the_bug` (answer keys recorded by `grokcheck mutate` in a scratch worktree), `change_impact` and `parsons`.
- Lint: `validate` warns about item-writing flaws, loose Simple Technical English prose and intro narration; `--strict` turns the warnings into failures.
- Socratic mode for the live Q&A.
- Spaced re-tests: `grokcheck due` lists and serves missed questions on a 1, 3, 7, 21 and 60 day ladder and flags those whose cited code changed.
- Exports: `grokcheck export --format anki` and `--format obsidian`.
- `grokcheck refresh` reports the claims and spikes of a lesson that no longer hold.
- `grokcheck-media`, an optional second skill in the same plugin: `doctor`, a reveal.js deck export, a narrated stepper MP4 and narrated concept videos in cached, checked chapters.
- A 15-minute tour lesson about grokcheck, made with grokcheck from the README, `SKILL.md` and the research notes, in `docs/lessons/grokcheck-tour.json` and linked from the README, with a narrated video of it made by `grokcheck-media` (a GIF of the first chapter and the captions in `docs/`).

### Changed

- Lesson schema version 2: a section holds an ordered list of elements instead of one code block.
- `SKILL.md` covers every subject, a time budget, parallel authoring by subagents and grounding before serve.
- CI type-checks the media skill, runs the example lesson through `validate --strict`, and lints only `skills` and `tests`.

## [0.1.0] - 2026-09-30

### Added

- Lesson format with a validator that reports every problem in one pass, and a published JSON Schema kept in sync with it.
- Seven question types: single choice, multiple choice, open answer, predict the output, pick the line, order the steps and fill the blank.
- Grading with sure, unsure and guess confidence ratings, instant feedback at checkpoints and delayed feedback in the closed-book final quiz.
- Localhost lesson server guarded by a per-lesson token, with live in-page questions answered by the agent.
- `grokcheck` CLI: `validate`, `serve`, `wait`, `reply`, `stop` and `retake`.
- Browser lesson page with checkpoint-gated sections, ask-about-selection and syntax highlighting.
- Markdown export of each finished lesson, stored next to the code under `.grokcheck/lessons/`.
- `SKILL.md`, an authoring guide, a lesson format reference and an example lesson.

[0.2.0]: https://github.com/Tsadoq/grokcheck/releases/tag/v0.2.0
[0.1.0]: https://github.com/Tsadoq/grokcheck/releases/tag/v0.1.0
