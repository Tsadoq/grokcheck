"""The lesson validator as the agent that wrote the lesson experiences it."""

from pathlib import Path
from typing import Any, cast

import pytest
from grokcheck.lesson import LessonError, Problem, load_lesson

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _matches(problem: Problem, path: str, fragment: str) -> bool:
    return problem.path == path and fragment in problem.message


def test_load_lesson_reports_every_problem_in_one_pass() -> None:
    """Every defect in a lesson is reported at once, each at its JSON path.

    The agent fixes a rejected lesson in one edit only if the first failure
    already names all of them; stopping at the first would cost a round trip
    per defect.
    """
    expected = [
        ("sections[0].code.file", "outside the project root"),
        ("sections[1].checkpoints[0].id", "duplicate question id"),
        ("final[1].text", "[[blank:extra]]"),
        ("final[2].correct[1]", "out of range"),
    ]

    with pytest.raises(LessonError) as caught:
        load_lesson(
            FIXTURES / "lessons" / "invalid_many_errors.json",
            FIXTURES / "project",
        )

    problems = caught.value.problems
    missing = [
        pair
        for pair in expected
        if not any(_matches(problem, *pair) for problem in problems)
    ]
    unexpected = [
        problem
        for problem in problems
        if not any(_matches(problem, *pair) for pair in expected)
    ]
    if missing or unexpected:
        pytest.fail(f"missing: {missing}\nunexpected: {unexpected}")


def test_public_view_shows_the_open_answer_rubric_but_not_the_model_answer() -> None:
    """The reader self-rates an open answer against its rubric, so the browser needs it.

    The model answer stays on the server until the answer is graded.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")

    public = lesson.public_view()
    sections = cast("list[dict[str, Any]]", public["sections"])
    final = cast("list[dict[str, Any]]", public["final"])
    questions = [
        question for section in sections for question in section["checkpoints"]
    ] + final
    open_answers = [q for q in questions if q["type"] == "open_answer"]

    if not open_answers:
        pytest.fail("fixture has no open_answer question")
    expected_rubric = [
        "Says a read counts as a use",
        "Links the end of the dict to the most recently used position",
        "Links the front of the dict to eviction",
    ]
    for question in open_answers:
        if question.get("rubric") != expected_rubric:
            pytest.fail(f"{question['id']} rubric is {question.get('rubric')!r}")
        if "model_answer" in question:
            pytest.fail(f"{question['id']} leaks its model_answer")
