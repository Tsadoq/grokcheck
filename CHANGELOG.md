# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/Tsadoq/grokcheck/releases/tag/v0.1.0
