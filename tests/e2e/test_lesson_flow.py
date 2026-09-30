"""A whole lesson driven through the real page in Chromium, from serve to results."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

if TYPE_CHECKING:
    from conftest import ServedLesson

pytestmark = pytest.mark.e2e

_REPLY = "Keys are kept in the order they were last used."


def _answer(page: Page, question: dict[str, Any], button: str) -> None:
    card = page.locator(f'[data-question="{question["id"]}"]')
    card.get_by_label(question["options"][question["correct"]]["text"]).check()
    card.get_by_label("Sure", exact=True).check()
    card.get_by_role("button", name=button).click()


def test_gated_lesson_live_question_and_closed_book_submit(
    page: Page, served_lesson: ServedLesson
) -> None:
    """A reader is gated, gets a live reply, then submits the closed-book quiz."""
    lesson = served_lesson.lesson
    first, second = lesson["sections"]
    page.goto(served_lesson.url)
    first_view = page.locator(f'[data-section="{first["id"]}"]')
    expect(first_view.get_by_text(first["body"])).to_be_visible()
    expect(page.get_by_role("heading", name=second["title"])).to_have_count(0)

    first_view.get_by_label(f"Ask a question about {first['title']}").fill(
        "Why an ordered dict?"
    )
    first_view.get_by_role("button", name="Ask", exact=True).click()
    asked = served_lesson.wait()
    served_lesson.reply(str(asked["question_id"]), _REPLY)
    expect(first_view.locator(".reply")).to_have_text(_REPLY)

    _answer(page, first["checkpoints"][0], "Check answer")
    expect(page.get_by_role("heading", name=second["title"])).to_be_visible()
    _answer(page, second["checkpoints"][0], "Check answer")
    page.get_by_role("button", name="Start final quiz").click()

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
