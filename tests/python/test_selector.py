"""The selector grammar: the shared vectors, validation and type-strict equality."""

import json
from pathlib import Path
from typing import Any

import pytest
from grokcheck.selector import MAX_CLAUSES, matches, problems, same

_VECTORS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "selectors.json").read_text("utf-8")
)
_FIELDS = {"lane", "seq", "event", "kind", "text", "running", "_lost", "_dup"}


@pytest.mark.parametrize("case", _VECTORS["match"])
def test_match_vectors(case: dict[str, Any]) -> None:
    """Every shared vector matches as it says, and is a valid selector."""
    if matches(case["selector"], case["item"]) is not case["expect"]:
        pytest.fail(f"{case} did not give {case['expect']}")
    if found := problems(case["selector"], _FIELDS):
        pytest.fail(f"{case['selector']} is invalid: {found}")


@pytest.mark.parametrize("case", _VECTORS["invalid"])
def test_invalid_vectors(case: dict[str, Any]) -> None:
    """Every invalid vector reports its expected message."""
    found = problems(case["selector"], case["fields"])
    if not any(case["error"] in message for _, message in found):
        pytest.fail(f"{case['selector']} gave {found}, not {case['error']!r}")


def test_true_is_not_one() -> None:
    """Booleans equal only booleans; numbers compare numerically."""
    if same(True, 1) or same(1, True) or same(False, 0) or same(None, 0):  # noqa: FBT003
        pytest.fail("a boolean or null equalled a number")
    if not same(1, 1.0):
        pytest.fail("1 did not equal 1.0")


def test_problem_paths_are_relative() -> None:
    """A problem names the path inside the selector."""
    found = problems({"and": [{"seq": {"over": 1}}]}, ["seq"])
    if found != [("and[0].seq.over", "'over' is not gt, gte, lt or lte")]:
        pytest.fail(f"got {found}")


def test_too_many_clauses() -> None:
    """More than MAX_CLAUSES clauses in total is a problem."""
    selector = {"or": [{"seq": n} for n in range(MAX_CLAUSES)]}
    if not any("clauses" in m for _, m in problems(selector, ["seq"])):
        pytest.fail("no clause count problem")
