"""A lesson served by the real `grokcheck` CLI for the browser tests to drive."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_DIR = Path(__file__).parents[2] / "skills" / "grokcheck" / "grokcheck"


def _choice(question_id: str, *texts: str) -> dict[str, Any]:
    """Build a single-choice question whose first option is correct."""
    return {
        "id": question_id,
        "type": "single_choice",
        "prompt": f"Question {question_id}?",
        "options": [{"text": text, "why": f"Because of {text}."} for text in texts],
        "correct": 0,
    }


LESSON: dict[str, Any] = {
    "schema_version": 1,
    "title": "Browser test lesson",
    "scope": {"summary": "A lesson with no code.", "files": []},
    "sections": [
        {
            "id": "ordering",
            "title": "Keeping keys in order",
            "body": "The cache remembers the order keys were used in.",
            "checkpoints": [_choice("cp-ordering", "Use order", "Insert order")],
        },
        {
            "id": "eviction",
            "title": "Dropping the oldest key",
            "body": "When full, the cache forgets the key used longest ago.",
            "checkpoints": [_choice("cp-eviction", "The oldest", "The newest")],
        },
    ],
    "final": [
        _choice("final-a", "Alpha right", "Alpha wrong"),
        _choice("final-b", "Beta right", "Beta wrong"),
        _choice("final-c", "Gamma right", "Gamma wrong"),
    ],
}


@dataclass(frozen=True)
class ServedLesson:
    """`LESSON` running under a detached `grokcheck` server, reached like an agent."""

    lesson: dict[str, Any]
    url: str
    lesson_id: str
    lesson_dir: Path
    project: Path

    def wait(self) -> dict[str, Any]:
        """Return the next reader event, as `grokcheck wait` prints it."""
        return grokcheck(self.project, "wait", self.lesson_id, "--timeout", "30")

    def reply(self, question_id: str, text: str) -> None:
        """Answer reader question `question_id` with `grokcheck reply`."""
        grokcheck(self.project, "reply", self.lesson_id, question_id, "--text", text)


def grokcheck(project: Path, *args: str) -> dict[str, Any]:
    """Run one CLI command in `project` and return its JSON output."""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(PACKAGE_DIR), *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"grokcheck {args[0]} failed: {completed.stdout}{completed.stderr}")
    output: dict[str, Any] = json.loads(completed.stdout)
    return output


@pytest.fixture
def served_lesson(tmp_path: Path) -> Iterator[ServedLesson]:
    """Serve `LESSON` from a fresh project with `--no-open`; stop the server after."""
    project = tmp_path / "project"
    project.mkdir()
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(LESSON), encoding="utf-8")
    served = grokcheck(project, "serve", str(lesson_file), "--no-open")
    try:
        yield ServedLesson(
            lesson=LESSON,
            url=served["url"],
            lesson_id=served["lesson_id"],
            lesson_dir=Path(served["lesson_dir"]),
            project=project,
        )
    finally:
        grokcheck(project, "stop", served["lesson_id"])
