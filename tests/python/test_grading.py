"""Grading of reader responses as the reader and the agent see the outcome."""

import pytest
from grokcheck.grading import grade
from grokcheck.lesson import Blank, FillBlank


def test_fill_blank_miss_is_needs_review_and_spacing_variants_match() -> None:
    """A spacing variant of an accepted answer is correct; other misses await review.

    The server cannot tell a wrong answer from an unforeseen right one, so it
    leaves that call to the agent instead of telling the reader they were wrong.
    """
    question = FillBlank(
        id="count",
        prompt="Complete the expression that counts the items.",
        text="total = [[blank:call]]",
        blanks=(Blank(id="call", accepted=("len(items)",)),),
    )
    expected = {"len( items )": "correct", "items.len()": "needs_review"}

    wrong = {
        answer: outcome
        for answer in expected
        if (outcome := grade(question, answer).outcome) != expected[answer]
    }
    if wrong:
        pytest.fail(f"expected {expected}, but these inputs got: {wrong}")
