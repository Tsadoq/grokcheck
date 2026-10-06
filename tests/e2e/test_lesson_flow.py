"""A whole lesson driven through the real page in Chromium, from serve to results."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from conftest import grokcheck
from playwright.sync_api import Page, expect

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from conftest import ServedLesson

pytestmark = pytest.mark.e2e

_REPLY = "Keys are kept in the order they were last used."


def _answer(
    page: Page, question: dict[str, Any], button: str, option: int | None = None
) -> None:
    """Answer a choice question, correctly unless `option` names another one."""
    chosen = question["correct"] if option is None else option
    card = page.locator(f'[data-question="{question["id"]}"]')
    card.get_by_label(question["options"][chosen]["text"]).check()
    card.get_by_label("Sure", exact=True).check()
    card.get_by_role("button", name=button).click()


def test_gated_lesson_live_question_and_closed_book_submit(
    page: Page, served_lesson: ServedLesson
) -> None:
    """A reader is gated, gets a live reply, then submits the closed-book quiz."""
    lesson = served_lesson.lesson
    first, second = lesson["sections"]
    page.goto(served_lesson.url)
    _answer(page, lesson["probe"][0], "Check answer")
    first_view = page.locator(f'[data-section="{first["id"]}"]')
    expect(first_view.get_by_text(first["body"])).to_be_visible()
    expect(page.get_by_role("heading", name=second["title"])).to_have_count(0)

    first_view.get_by_label(f"Ask a question about {first['title']}").fill(
        "Why an ordered dict?"
    )
    first_view.get_by_label("Socratic: ask me questions instead").check()
    first_view.get_by_role("button", name="Ask", exact=True).click()
    asked = served_lesson.wait()
    if asked.get("mode") != "socratic":
        pytest.fail(f"expected a Socratic question event, got {asked}")
    served_lesson.reply(str(asked["question_id"]), _REPLY)
    expect(first_view.locator(".reply")).to_have_text(_REPLY)

    _answer(page, first["checkpoints"][0], "Check answer")
    expect(page.get_by_role("heading", name=second["title"])).to_be_visible()
    _answer(page, second["checkpoints"][0], "Check answer")
    graded = page.locator(f'[data-question="{second["checkpoints"][0]["id"]}"]')
    expect(graded.get_by_role("button", name="Check answer")).to_have_count(0)
    start_final = page.get_by_role("button", name="Start final quiz")
    expect(start_final).to_have_count(0)
    page.get_by_role("button", name="capacity").click()
    start_final.click()

    expect(page.locator(".final-quiz")).to_be_visible()
    for section in lesson["sections"]:
        expect(page.get_by_text(section["body"])).to_have_count(0)
    for question in lesson["final"]:
        _answer(page, question, "Save answer")
    page.get_by_role("button", name="Submit final quiz").click()
    expect(page.get_by_role("heading", name="Results")).to_be_visible()

    results_file = served_lesson.lesson_dir / "results.json"
    results = json.loads(results_file.read_text(encoding="utf-8"))
    recorded = [item["question_id"] for item in results["final"]]
    expected = [question["id"] for question in lesson["final"]]
    if recorded != expected:
        pytest.fail(f"results.json final items {recorded}, expected {expected}")


def test_probe_sets_depth_and_toggle_hides_detail(
    page: Page, served_lesson: ServedLesson
) -> None:
    """A wrong probe answer starts in detail; Short hides it; the plan is explained."""
    lesson = served_lesson.lesson
    probe = lesson["probe"][0]
    wrong_option = 1 - probe["correct"]
    page.goto(served_lesson.url)

    _answer(page, probe, "Check answer", option=wrong_option)
    detail = page.locator('[data-depth="detail"]').first
    expect(detail).to_be_visible()

    page.get_by_role("button", name="Short", exact=True).click()
    expect(detail).to_be_hidden()
    expect(page.locator("details.plan li")).to_have_count(
        len(lesson["plan"]["rationale"])
    )


_REVEALED = "Nobody will ever put anything in this queue."

TRACE_LESSON: dict[str, Any] = {
    "schema_version": 2,
    "title": "Trace test lesson",
    "scope": {"summary": "A reader that reconnects.", "files": []},
    "sections": [
        {
            "id": "reconnect",
            "title": "A reader reconnects",
            "body": "Step through the recorded run.",
            "elements": [
                {
                    "type": "trace",
                    "trace_id": "reconnect",
                    "title": "The reader blocks",
                    "gate": "cp-predict",
                    "panels": [
                        {"id": "queue", "label": "Reader's queue", "kind": "chips"}
                    ],
                    "versions": [
                        {
                            "label": "before",
                            "code": {
                                "language": "python",
                                "text": "queue = []\nlog = [2, 3]\nawait queue.get()\n",
                            },
                            "steps": [
                                {
                                    "narration": ["follow() creates an empty queue."],
                                    "state": {"queue": []},
                                },
                                {
                                    "narration": ["The log holds the missed events."],
                                    "state": {"queue": []},
                                },
                                {"narration": [_REVEALED], "state": {"queue": []}},
                            ],
                        }
                    ],
                }
            ],
            "checkpoints": [
                {
                    "id": "cp-predict",
                    "type": "predict_state",
                    "prompt": "What happens at the await?",
                    "trace_id": "reconnect",
                    "version": 0,
                    "step": 2,
                    "options": [
                        {"text": "It waits forever", "why": "Nothing fills the queue."},
                        {"text": "It reads the log", "why": "It never looks there."},
                    ],
                    "correct": 0,
                }
            ],
        }
    ],
    "final": [
        {
            "id": f"final-{name}",
            "type": "single_choice",
            "prompt": f"Question {name}?",
            "options": [
                {"text": f"{name} right", "why": "Right."},
                {"text": f"{name} wrong", "why": "Wrong."},
            ],
            "correct": 0,
        }
        for name in ("a", "b", "c")
    ],
}


@pytest.fixture
def trace_lesson_url(tmp_path: Path) -> Iterator[str]:
    """Serve `TRACE_LESSON` with a three-line recording; stop the server after."""
    project = tmp_path / "project"
    traces = project / ".grokcheck" / "traces"
    traces.mkdir(parents=True)
    events = [
        {"seq": seq, "event": "line", "file": "reader.py", "line": seq + 1}
        for seq in range(3)
    ]
    (traces / "reconnect.json").write_text(
        json.dumps({"events": events, "truncated": False}), encoding="utf-8"
    )
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(TRACE_LESSON), encoding="utf-8")
    served = grokcheck(project, "serve", str(lesson_file), "--no-open")
    try:
        yield served["url"]
    finally:
        grokcheck(project, "stop", served["lesson_id"])


def test_trace_stepper_stops_at_gate_and_reveals_after_answer(
    page: Page, trace_lesson_url: str
) -> None:
    """Stepping reaches the withheld step; answering its gate shows the narration."""
    gate = TRACE_LESSON["sections"][0]["checkpoints"][0]
    page.goto(trace_lesson_url)
    stepper = page.locator(".stepper")
    next_step = stepper.get_by_role("button", name="Next step")
    next_step.click()
    next_step.click()
    expect(stepper.locator(".progress")).to_have_text("Step 3 of 3")
    expect(next_step).to_be_disabled()
    expect(stepper.get_by_text(_REVEALED)).to_have_count(0)

    _answer(page, gate, "Check answer")
    expect(stepper.get_by_text(_REVEALED)).to_be_visible()
    expect(page).to_have_url(re.compile(r"[#&]step-reconnect=3"))
