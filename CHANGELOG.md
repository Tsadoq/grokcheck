# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-10-06

### Added

- The `view` element draws recorded rows in one of five layouts: `lanes` (events per lane over time, with lost, repeated and stacked events computed), `table`, `steps` (a code walk of at most 6 lines per step, or a step per recorded line event), `blocks` and `decision` (the path each recorded run took through the cited checks). A view can split into panes to compare two versions, vary its scenario with inputs and presets, set tasks, and link to the other views of its section.
- Two question types answered on a view: `select_items` (click the cells that match) and `fill_table` (fill the hidden cells of a table). A varied case stays masked until its checkpoint is answered, and `predict_state` can gate a steps view.
- Finals can draw rows held out of the sections (transfer items) and a deliberately wrong copy of a dataset (find the wrong item).
- Lint for code-first sections, long code and diff hunks, crowded views, a masked first case, extra or authored datasets without a reason, stale datasets and finals without transfer or wrong-data items.
- `results.json` counts answers per element type in `by_element`.
- Recorded datasets: `grokcheck record <driver> --matrix ...` runs a driver script once per input combination, each in a fresh process, and keeps every row it emits with the cited lines that run executed, the commit and the hashes of the driver and the cited files, in `.grokcheck/data/<id>.json`. `--python` runs the driver under another interpreter, such as the project's own virtual environment, without installing grokcheck there. `record --from-trace` turns a recorded trace into rows.
- `grokcheck data show`, `data author` and `data wrong`: summarise a dataset, write rows by hand with a cited span for each, and copy a dataset with deliberate errors for the final quiz.
- Datasets are refused when a run emitted rows without executing a cited line inside a function, when a hand-written row lacks a valid citation or was contradicted, or when a wrong copy no longer matches its original.
- A JSON selector grammar for picking rows and view items, with test vectors shared by the Python and browser implementations.
- Grounding covers recorded rows: a claim can be backed by the rows a selector matches, and each hand-written row is checked like a cited claim, its verdict kept in the data file.
- `grokcheck refresh` lists declared datasets whose cited files or driver changed.

### Changed

- Lesson schema version 3 adds `kinds`, `datasets` and the view element; version 2 lessons load unchanged.
- The `no_vocab` lint is now `no_names_first`: a vocabulary element, or a blocks or table view, in the first section satisfies it.

## [0.3.0] - 2026-10-06

### Added

- Video in the lesson flow: the first time a lesson is made on a machine, grokcheck asks once whether to install the video tools (about 2.3 GB). After a yes, every section opens with a one-minute narrated chapter. `grokcheck_media setup` installs the tools into the plugin's data folder, which survives updates, and re-syncs them when an update changes the pinned versions.
- `export --format html` writes a lesson as one self-contained HTML page that works offline: the live page with its styles, fonts, scripts and videos inlined, graded in the browser.
- `validate --strict` lint for lessons that lean on code alone: a section whose short view has only prose and code, a diagram marked `detail`, a trace rejected without a reason other than time, and an area or decision lesson without a vocabulary element.

### Changed

- The lesson page follows the design mockup: a header with the depth toggle, a chapter rail with locked and done states, cards, side-by-side panels, dark mode, and the Bricolage Grotesque, Atkinson Hyperlegible and JetBrains Mono fonts, vendored under the SIL Open Font License.
- Diagrams render at full size and scroll sideways when wide, instead of shrinking to fit the column.
- Concept video frames show highlighted code, boxes joined by arrows and lists on a light background, instead of one line of text per beat.
- A concept video chapter animates one picture that changes beat by beat: a flow, sequence or state scene builds up, highlights the narrated path and moves a marker along it. A flow scene can reuse the section's Mermaid diagram. Code is limited to one excerpt of at most 5 lines per chapter, shown beside the scene.
- Narration rules and lint: plain words for what code does, at most two identifiers per chapter, no class names or dotted names read aloud, sentences of at most 25 words. A pronunciation table covers terms such as grokcheck, keepalive, Last-Event-ID and HTTP statuses.
- The authoring guide asks for every medium that fits, keeps diagrams in the short view, and prefers top-down flowcharts. The skill validates with `--strict`.

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

[0.4.0]: https://github.com/Tsadoq/grokcheck/releases/tag/v0.4.0
[0.2.0]: https://github.com/Tsadoq/grokcheck/releases/tag/v0.2.0
[0.1.0]: https://github.com/Tsadoq/grokcheck/releases/tag/v0.1.0
