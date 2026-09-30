"""The exported markdown as a reader rereading a finished lesson sees it."""

from pathlib import Path

import pytest
from grokcheck.export import QuestionThread, render_markdown
from grokcheck.lesson import load_lesson

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _heading_line(lines: list[str], title: str) -> int:
    return next(
        index
        for index, line in enumerate(lines)
        if line.startswith("#") and title in line
    )


def test_export_places_each_reply_under_its_section() -> None:
    """A reader question and its reply appear inside the section they were asked in.

    Rereading the export later only helps if each answer sits next to the
    explanation that prompted the question.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    purpose, following = lesson.sections[0], lesson.sections[1]
    reply = "The cache exists so repeated lookups skip the slow backing store."
    thread = QuestionThread(
        id="q1",
        section_id=purpose.id,
        selection="",
        text="Why not just call the store every time?",
        reply=reply,
    )

    lines = render_markdown(lesson, {}, [thread]).splitlines()

    start = _heading_line(lines, purpose.title)
    end = _heading_line(lines, following.title)
    inside = [index for index in range(start, end) if reply in lines[index]]
    if not inside:
        context = "\n".join(lines[max(start - 2, 0) : end + 3])
        pytest.fail(
            f"reply not found between the '{purpose.title}' heading (line {start})"
            f" and the '{following.title}' heading (line {end}):\n{context}"
        )
