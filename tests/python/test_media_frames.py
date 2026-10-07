"""How a concept video beat's `show` text picks its frame layout."""

from typing import Any

import pytest
from grokcheck_media.concept_video import frame


@pytest.mark.parametrize(
    ("show", "expected"),
    [
        (
            "page  ->  server  ->  agent",
            {"kind": "flow", "nodes": [["page"], ["server"], ["agent"]]},
        ),
        (
            "agent writes code\n→ you approve it",
            {"kind": "flow", "nodes": [["agent writes", "code"], ["you approve it"]]},
        ),
        (
            "page <- reply <- agent",
            {"kind": "flow", "nodes": [["agent"], ["reply"], ["page"]]},
        ),
        (
            "1. the gate\n2. the evidence",
            {
                "kind": "list",
                "numbered": True,
                "items": [["the gate"], ["the evidence"]],
            },
        ),
        (
            "1. checker   2. research",
            {"kind": "list", "numbered": True, "items": [["checker"], ["research"]]},
        ),
        (
            "supported | contradicted",
            {
                "kind": "list",
                "numbered": False,
                "items": [["supported"], ["contradicted"]],
            },
        ),
        (
            "a -> b -> c -> d -> e",
            {
                "kind": "list",
                "numbered": True,
                "items": [["a"], ["b"], ["c"], ["d"], ["e"]],
            },
        ),
        ("video = passive", {"kind": "text", "lines": ["video = passive"]}),
    ],
)
def test_frame_picks_layout_from_show(show: str, expected: dict[str, Any]) -> None:
    """Arrows make a flow, lines or markers a list, the rest text."""
    got = frame(show, code=False)
    if got != expected:
        pytest.fail(f"{show!r} gave {got}")


def test_code_frame_keeps_the_text_as_written() -> None:
    """Code is never wrapped or split on arrows."""
    show = "x = a -> b\n    return x\n"

    got = frame(show, code=True, language="rust")

    if got != {"kind": "code", "code": "x = a -> b\n    return x", "language": "rust"}:
        pytest.fail(f"got {got}")


def test_long_text_wraps_without_breaking_words() -> None:
    """A statement wraps at about 30 characters and keeps long words whole."""
    got = frame("results_path_with_a_very_long_identifier is printed", code=False)

    if got["lines"] != ["results_path_with_a_very_long_identifier", "is printed"]:
        pytest.fail(f"got {got}")
