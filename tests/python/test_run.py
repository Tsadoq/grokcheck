"""One live lesson as the reader's browser and the agent's `wait` loop see it."""

import shutil
from pathlib import Path

import pytest
from grokcheck.lesson import load_lesson
from grokcheck.run import LessonRun

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_question_asked_between_waits_is_not_lost(tmp_path: Path) -> None:
    """A reader question asked while no agent `wait` is pending still reaches it.

    The agent runs `wait` once per event, so there are gaps between runs; a
    question asked in one of those gaps must be handed to the next `wait`.
    """
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", project)
    run = LessonRun.create(lesson, project)
    text = "Why does eviction pick the oldest entry?"

    run.ask(lesson.sections[0].id, "", text)
    events = run.events_after(0, timeout=0.1)

    questions = [event for event in events if event.type == "question"]
    if len(events) != 1 or len(questions) != 1 or questions[0].body["text"] != text:
        pytest.fail(f"expected one question event with text {text!r}, got {events}")
