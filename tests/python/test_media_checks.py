"""Concept video checks: the transcribe-back diff and the chapter planner."""

import json
import shutil
from typing import Any

import pytest
from grokcheck_media import doctor
from grokcheck_media.checks import diff
from grokcheck_media.concept_video import plan_chapters

SCRIPT = [
    "The request was canceled after 38 seconds.",
    "Cleanup runs in the finally block.",
    "Then the connection returns to the pool.",
]


def test_transcribe_back_diff_normalises_spelling_and_numbers() -> None:
    """Spelling and number variants are noise; a skipped sentence is reported."""
    heard_variants = [
        "The request was cancelled after thirty eight seconds.",
        "Cleanup runs in the finally block.",
        "Then the connection returns to the pool.",
    ]
    heard_missing_second = [SCRIPT[0], "", SCRIPT[2]]

    variants = diff(SCRIPT, heard_variants)
    missing = diff(SCRIPT, heard_missing_second)

    if variants:
        pytest.fail(f"expected no differences for spelling variants, got {variants}")
    if [difference.index for difference in missing] != [1]:
        pytest.fail(f"expected only sentence 1 reported as skipped, got {missing}")


def _lesson(section_count: int) -> dict[str, Any]:
    return {
        "title": "Cancellation",
        "sections": [
            {
                "id": f"s{n}",
                "title": f"Section {n}",
                "elements": [
                    {
                        "type": "prose",
                        "markdown": "text",
                        "claims": [
                            {
                                "text": f"claim {n}",
                                "backing": {"file": "a.py", "lines": [n, n + 1]},
                            }
                        ],
                    }
                ],
            }
            for n in range(1, section_count + 1)
        ],
    }


def test_plan_chapters_splits_sections_to_the_minute_budget() -> None:
    """Minutes over chapter length sets the chapter count; sections stay in order."""
    lesson = _lesson(10)

    one_per_section = plan_chapters(lesson, 20, 2)
    grouped = plan_chapters(lesson, 6, 2)

    if [c.word_budget for c in one_per_section] != [300] * 10:
        pytest.fail(
            "expected ten chapters of 300 words (2 minutes at 150 per minute), got"
            f" {[c.word_budget for c in one_per_section]}"
        )
    expected_groups = [
        ["s1", "s2", "s3", "s4"],
        ["s5", "s6", "s7"],
        ["s8", "s9", "s10"],
    ]
    if [c.sections for c in grouped] != expected_groups:
        pytest.fail(f"expected adjacent sections grouped 4/3/3, got {grouped}")
    first_cited = {("a.py", n, n + 1) for n in range(1, 5)}
    if set(grouped[0].cited) != first_cited:
        pytest.fail(
            f"expected the first chapter to cite {first_cited}, got {grouped[0].cited}"
        )


def test_require_prints_a_skip_line_and_exits_1_when_a_tool_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing executable becomes a JSON skip naming it and its install hint."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(SystemExit) as exited:
        doctor.require(["ffmpeg"])

    line = json.loads(capsys.readouterr().out)
    code = exited.value.code
    if code != 1 or line["ok"] or not line["skipped"].startswith("ffmpeg"):
        pytest.fail(f"expected exit 1 and an ffmpeg skip, got {code} {line}")


def test_require_rejects_an_unknown_tool_name() -> None:
    """A tool name doctor does not know is a ValueError, not a KeyError."""
    with pytest.raises(ValueError, match="unknown tool 'ffmpge'"):
        doctor.require(["ffmpge"])
