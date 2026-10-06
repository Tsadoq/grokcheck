"""Spaced re-tests: which missed items come due, and which went stale in git."""

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from grokcheck.lesson import load_lesson
from grokcheck.run import LessonRun
from grokcheck.schedule import Schedule, local_today


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )


def _cited_choice(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "type": "single_choice",
        "prompt": "Which?",
        "code": {"file": "mod.py", "lines": [1, 3]},
        "options": [{"text": "a", "why": "first"}, {"text": "b", "why": "second"}],
        "correct": 0,
    }


def test_submit_schedules_misses_and_stale_items_are_separated(tmp_path: Path) -> None:
    """A confident miss comes due tomorrow, and turns stale once its lines change."""
    module = tmp_path / "mod.py"
    module.write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "mod.py")
    _git(tmp_path, "commit", "-q", "-m", "start")
    raw = {
        "schema_version": 2,
        "title": "Due",
        "scope": {"summary": "One module.", "files": ["mod.py"]},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "checkpoints": [_cited_choice("cp-1")],
            }
        ],
        "final": [_cited_choice("f1"), _cited_choice("f2"), _cited_choice("f3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    run = LessonRun.create(load_lesson(lesson_file, tmp_path), tmp_path)
    tomorrow = local_today() + timedelta(days=1)

    run.answer("f1", 1, "sure")
    run.answer("f2", 0, "sure")
    run.answer("f3", 0, "unsure")
    run.submit()
    schedule = Schedule.load(tmp_path)
    due_before = [entry.question_id for entry in schedule.due_items(tomorrow)]

    module.write_text("a = 1\nb = 20\nc = 3\nd = 4\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "edit line 2")
    due, stale = schedule.partition(tomorrow)

    if due_before != ["f1"]:
        pytest.fail(f"due tomorrow before the edit: {due_before}")
    if [e.question_id for e in due] or [e.question_id for e in stale] != ["f1"]:
        pytest.fail(f"after the edit due={due} stale={stale}")
