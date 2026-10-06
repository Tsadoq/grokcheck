# Contributing to grokcheck

Thanks for helping out. Bug reports, question-type ideas and pull requests are all welcome.

## Setup

You need [uv](https://docs.astral.sh/uv/), Python 3.11 or newer, and Node 20 or newer.

```sh
git clone https://github.com/Tsadoq/grokcheck.git
cd grokcheck
uv sync
```

To try your checkout inside Claude Code, add it as a local marketplace:

```
/plugin marketplace add /path/to/grokcheck
/plugin install grokcheck@grokcheck-marketplace
```

## Layout

| Path | What lives there |
|------|------------------|
| `skills/grokcheck/SKILL.md` | The instructions the agent follows during a session |
| `skills/grokcheck/grokcheck/` | The CLI, the lesson model and validator, grading, the run state and the localhost server |
| `skills/grokcheck/web/` | The lesson page: plain ES modules, no build step, vendored highlight.js |
| `skills/grokcheck/web/elements.js` | The element renderer registry, `ELEMENT_RENDERERS`, one entry per element type |
| `skills/grokcheck/web/questions.js` | The question renderer registry, one entry per question type |
| `skills/grokcheck/web/vendor/mermaid/` | Vendored `@mermaid-js/tiny`, loaded only by lessons that have a diagram |
| `skills/grokcheck/schema/` | The published lesson JSON Schema |
| `skills/grokcheck/references/` | The lesson format, the authoring guide, an example lesson and the spike template |
| `skills/grokcheck-media/` | The optional media skill: `doctor`, the reveal.js deck, the stepper MP4 and concept videos, with vendored reveal.js |
| `tests/python/` | Unit tests for both skills; the media tests are `test_media_*.py` |
| `tests/js/` | Node tests for the page's pure modules |
| `tests/e2e/` | A Playwright test that drives a full lesson in Chromium |

## Rules the code keeps

- **No runtime dependencies in the core.** `skills/grokcheck/` must run on a bare Python 3.11 and a browser, and never imports `grokcheck_media`. Anything that needs another tool goes in `skills/grokcheck-media/`, probes for it through `doctor`, and prints a skip message when it is missing. Development tools belong in the `dev` dependency group.
- **The schema and the validator agree.** If you change what a lesson may contain, update both `lesson.py` and `lesson.schema.json`; `test_schema_drift.py` fails when they disagree.
- **Answers never reach the page early.** `Lesson.public_view()` strips the answer key before the lesson is served. A new question type must say which of its fields are secret.
- **A new element declares its secret payload and its gate.** An element that withholds something until a checkpoint is answered names that checkpoint in `gate`, and `Lesson.gated_payload()` returns exactly what is withheld. The server sends it only after the gate is graded, or, for a reader-first options element, after the reader commits their own list.
- **Spikes and mutations never run in the reader's tree.** Spikes run under `.grokcheck/spikes/<id>/` in a throwaway uv environment, or in a container for unvetted packages; mutations run in a scratch git worktree. Never change the reader's working tree to get an answer key.

## Before you open a pull request

```sh
uv run ruff check skills tests
uv run ruff format --check skills tests
uv run mypy skills/grokcheck/grokcheck skills/grokcheck-media/grokcheck_media tests
uv run pytest
node --test tests/js/*.test.mjs
python3 skills/grokcheck/grokcheck validate skills/grokcheck/references/example-lesson.json --project . --strict
uv run pytest -m e2e tests/e2e
```

The end-to-end test needs `uv run playwright install chromium` once. CI runs every command above on each pull request.

Write the failing test first, keep each pull request to one change, and describe the behaviour it adds or fixes in the description.
