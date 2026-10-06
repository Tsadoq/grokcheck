"""The reveal.js deck a presenter opens from disk to walk through a lesson."""

import re
from pathlib import Path

import pytest
from grokcheck.lesson import Lesson, load_lesson
from grokcheck_media.reveal import render_deck

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _lesson() -> Lesson:
    return load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")


def test_reveal_export_has_one_slide_with_notes_per_section() -> None:
    """Each section becomes one slide whose speaker notes hold its checkpoints."""
    lesson = _lesson()
    html = render_deck(lesson)

    slides = html.count("<section")
    notes = html.count('<aside class="notes">')
    if slides != len(lesson.sections) or notes != len(lesson.sections):
        pytest.fail(
            f"{len(lesson.sections)} sections gave {slides} slides and {notes} notes"
        )
    first_aside = html.split('<aside class="notes">')[1].split("</aside>")[0]
    first_prompt = lesson.sections[0].checkpoints[0].prompt
    if first_prompt not in first_aside:
        pytest.fail(f"{first_prompt!r} missing from the first notes: {first_aside}")


def test_reveal_export_references_only_vendored_relative_assets() -> None:
    """The deck opens offline: reveal.js and its styles come from the vendored copy."""
    html = render_deck(_lesson())

    missing = [
        asset
        for asset in (
            "../vendor/reveal.js/dist/reveal.js",
            "../vendor/reveal.js/dist/reveal.css",
        )
        if asset not in html
    ]
    if missing:
        pytest.fail(f"deck does not reference {missing}")
    url = re.search(r"https?://\S*", html)
    if url:
        pytest.fail(f"deck references the network: {url.group()}")
