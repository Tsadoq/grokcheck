"""Spaced re-tests of missed questions, kept in `<project>/.grokcheck/schedule.json`.

A missed question comes due after the first step of `LADDER`; each later
correct review moves it one step up, a wrong one sends it back to the first,
and a correct review on the last step drops it. An entry whose cited lines
changed in git since its lesson is stale: its question may no longer be true.
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from grokcheck import git
from grokcheck.lesson import TraceElement

if TYPE_CHECKING:
    from pathlib import Path

    from grokcheck.lesson import Question
    from grokcheck.run import LessonRun

LADDER = (1, 3, 7, 21, 60)

_MISSED = frozenset({"incorrect", "partial", "needs_review"})
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ", re.MULTILINE)


@dataclass(frozen=True)
class Entry:
    """One scheduled question; `cited_lines[i]` is the line span in `cited_files[i]`.

    `commit` is the HEAD the lesson was submitted at, `None` outside git.
    """

    lesson_id: str
    question_id: str
    commit: str | None
    cited_files: tuple[str, ...]
    cited_lines: tuple[tuple[int, int], ...]
    due: str
    interval_index: int = 0

    @property
    def key(self) -> str:
        """The id the question carries in a `due --serve` retake."""
        return f"{self.lesson_id}/{self.question_id}"


class Schedule:
    """The project's scheduled questions; every change is saved at once."""

    def __init__(self, project_root: Path, entries: list[Entry]) -> None:
        """Hold `entries`; use `load` rather than calling this."""
        self.project_root = project_root
        self._entries = {entry.key: entry for entry in entries}

    @classmethod
    def load(cls, project_root: Path) -> Schedule:
        """Read the project's schedule, empty when none was written yet."""
        path = _path(project_root)
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return cls(
            project_root,
            [
                Entry(
                    **{
                        **item,
                        "cited_files": tuple(item["cited_files"]),
                        "cited_lines": tuple(tuple(s) for s in item["cited_lines"]),
                    }
                )
                for item in raw
            ],
        )

    def record_submit(self, run: LessonRun, today: date | None = None) -> None:
        """Schedule the missed final and gate questions of the submitted `run`.

        A question whose id is a schedule key is a review from `due --serve`
        and goes through `record_review` instead.
        """
        today = today or local_today()
        grades = run.grades
        gates = {
            element.gate
            for section in run.lesson.sections
            for element in section.elements
            if isinstance(element, TraceElement) and element.gate
        }
        questions = [
            *run.lesson.final,
            *(
                question
                for section in run.lesson.sections
                for question in section.checkpoints
                if question.id in gates
            ),
        ]
        commit = _head(self.project_root)
        for question in questions:
            graded = grades.get(question.id)
            if graded is None:
                continue
            if question.id in self._entries:
                self.record_review(
                    question.id,
                    correct=not missed(graded.outcome, graded.confidence),
                    today=today,
                )
            elif missed(graded.outcome, graded.confidence):
                files, lines = _citation(question)
                entry = Entry(
                    run.lesson_id,
                    question.id,
                    commit,
                    files,
                    lines,
                    (today + timedelta(days=LADDER[0])).isoformat(),
                )
                self._entries[entry.key] = entry
        self._save()

    def record_review(
        self, key: str, *, correct: bool, today: date | None = None
    ) -> None:
        """Move the entry `key` up the ladder when `correct`, else back to its start."""
        entry = self._entries[key]
        index = entry.interval_index + 1 if correct else 0
        if index == len(LADDER):
            del self._entries[key]
        else:
            due = (today or local_today()) + timedelta(days=LADDER[index])
            self._entries[key] = dataclasses.replace(
                entry, interval_index=index, due=due.isoformat()
            )
        self._save()

    def due_items(self, today: date) -> list[Entry]:
        """Return the entries due on or before `today`, earliest first."""
        return sorted(
            (e for e in self._entries.values() if date.fromisoformat(e.due) <= today),
            key=lambda e: (e.due, e.key),
        )

    def partition(self, today: date) -> tuple[list[Entry], list[Entry]]:
        """Split `due_items(today)` into those still current and those stale."""
        due: list[Entry] = []
        stale_entries: list[Entry] = []
        for entry in self.due_items(today):
            (stale_entries if stale(self.project_root, entry) else due).append(entry)
        return due, stale_entries

    def _save(self) -> None:
        path = _path(self.project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.tmp")
        entries = [dataclasses.asdict(entry) for entry in self._entries.values()]
        temp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        temp.replace(path)


def stale(project_root: Path, entry: Entry) -> bool:
    """Whether a commit since `entry.commit` changed any of its cited lines.

    An entry without a commit or citation is never stale; one whose commit git
    no longer knows always is.
    """
    if entry.commit is None or not entry.cited_files:
        return False
    files = entry.cited_files
    quiet = git.output(
        project_root, "diff", "--quiet", entry.commit, "HEAD", "--", *files
    )
    if quiet is not None:
        return False
    for file, (start, end) in zip(files, entry.cited_lines, strict=True):
        diff = git.output(project_root, "diff", "-U0", entry.commit, "HEAD", "--", file)
        if diff is None:
            return True
        for hunk in _HUNK.finditer(diff):
            old_start = int(hunk[1])
            old_count = 1 if hunk[2] is None else int(hunk[2])
            if old_count == 0:
                if start <= old_start < end:
                    return True
            elif old_start <= end and old_start + old_count - 1 >= start:
                return True
    return False


def local_today() -> date:
    """Today's date where the reader is, which is what due dates count in."""
    return datetime.now(UTC).astimezone().date()


def missed(outcome: str, confidence: str | None) -> bool:
    """Whether a graded answer was wrong, or not correct though rated `sure`."""
    return outcome in _MISSED or (confidence == "sure" and outcome != "correct")


def _citation(
    question: Question,
) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    code = getattr(question, "code", None)
    if code is None or code.file is None:
        return (), ()
    end = code.start_line + max(len(code.text.splitlines()), 1) - 1
    return (code.file,), ((code.start_line, end),)


def _head(project_root: Path) -> str | None:
    head = git.output(project_root, "rev-parse", "HEAD")
    return None if head is None else head.strip()


def _path(project_root: Path) -> Path:
    return project_root / ".grokcheck" / "schedule.json"
