"""Cutting a git diff into annotatable diff elements."""

import json
import subprocess
from pathlib import Path

import pytest
from grokcheck.diffs import as_json, hunks
from grokcheck.lesson import DiffElement, load_lesson


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


def _choice(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "type": "single_choice",
        "prompt": "Pick one.",
        "options": [{"text": "a", "why": "a"}, {"text": "b", "why": "b"}],
        "correct": 0,
    }


def test_hunks_number_new_and_old_sides_from_a_real_commit(tmp_path: Path) -> None:
    """Line 2 is replaced by two lines; the hunk keeps git's numbering.

    The validator then accepts a note on new line 3, which exists only on the
    new side.
    """
    letters = tmp_path / "letters.txt"
    letters.write_text("a\nb\nc\n", encoding="utf-8")
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "first")
    letters.write_text("a\nx\ny\nc\n", encoding="utf-8")
    _git(tmp_path, "commit", "--quiet", "-am", "second")

    [element] = hunks(tmp_path, "letters.txt", "HEAD~1", "HEAD")

    if [line.op for line in element.lines] != [" ", "-", "+", "+", " "]:
        pytest.fail(f"unexpected ops: {element.lines}")
    if (element.old_start, element.new_start) != (1, 1):
        pytest.fail(f"unexpected starts: {element.old_start}, {element.new_start}")

    draft = as_json(element)
    draft["notes"] = [
        {
            "lines": [3, 3],
            "cite": {"file": "letters.txt", "lines": [3, 3], "symbol": "y"},
            "text": "The second inserted line.",
        }
    ]
    lesson = {
        "schema_version": 2,
        "title": "Letters",
        "scope": {"summary": "One file.", "files": ["letters.txt"]},
        "sections": [
            {
                "id": "change",
                "title": "The change",
                "body": "Line b became two lines.",
                "elements": [draft],
                "checkpoints": [_choice("cp")],
            }
        ],
        "final": [_choice("f1"), _choice("f2"), _choice("f3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(lesson), encoding="utf-8")

    [loaded] = load_lesson(lesson_file, tmp_path).sections[0].elements
    if not isinstance(loaded, DiffElement) or loaded.notes[0].lines != (3, 3):
        pytest.fail(f"note not kept: {loaded}")
