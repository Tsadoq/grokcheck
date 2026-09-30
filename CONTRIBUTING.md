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
| `skills/grokcheck/schema/` | The published lesson JSON Schema |
| `skills/grokcheck/references/` | The lesson format, the authoring guide and an example lesson |
| `tests/python/` | Unit tests for the Python package |
| `tests/js/` | Node tests for the page's pure modules |
| `tests/e2e/` | A Playwright test that drives a full lesson in Chromium |

## Rules the code keeps

- **No runtime dependencies.** The skill must run on a bare Python 3.11 and a browser. Anything else belongs in the `dev` dependency group.
- **The schema and the validator agree.** If you change what a lesson may contain, update both `lesson.py` and `lesson.schema.json`; `test_schema_drift.py` fails when they disagree.
- **Answers never reach the page early.** `Lesson.public_view()` strips the answer key before the lesson is served. A new question type must say which of its fields are secret.

## Before you open a pull request

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy skills/grokcheck/grokcheck tests
uv run pytest
node --test tests/js/
uv run pytest -m e2e tests/e2e
```

The end-to-end test needs `uv run playwright install chromium` once. CI runs every command above on each pull request.

Write the failing test first, keep each pull request to one change, and describe the behaviour it adds or fixes in the description.
