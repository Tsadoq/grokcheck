"""Grading of reader responses as the reader and the agent see the outcome."""

import pytest
from grokcheck.grading import grade
from grokcheck.lesson import (
    Blank,
    ChangeImpact,
    FillBlank,
    FixTheBug,
    Mutation,
    MutationQuiz,
    Option,
    Parsons,
    ParsonsLine,
)
from grokcheck.lesson import TestCase as MutantTest


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


def test_mutation_quiz_is_correct_only_for_the_exact_set_of_failing_tests() -> None:
    """Ticking a subset or a superset of the failing tests earns nothing.

    Partial credit would reward ticking every test, which needs no understanding.
    """
    question = MutationQuiz(
        id="mutant",
        prompt="Which tests fail once the guard is removed?",
        mutation=Mutation(file="src/cache.py", line=7, replacement=None),
        tests=(
            MutantTest(name="test_zero", fails=True),
            MutantTest(name="test_one", fails=True),
            MutantTest(name="test_put", fails=False),
        ),
        log="FAILED tests/test_cache.py::test_zero",
    )
    expected = {
        ("test_one", "test_zero"): "correct",
        ("test_zero",): "incorrect",
        ("test_zero", "test_one", "test_put"): "incorrect",
    }

    got = {ticked: grade(question, list(ticked)).outcome for ticked in expected}
    if got != expected:
        pytest.fail(f"expected {expected}, got {got}")


def test_fix_the_bug_takes_its_outcome_from_the_server_test_run() -> None:
    """The reader's fix is judged by the test run, not by comparing their edit."""
    question = FixTheBug(
        id="fix",
        prompt="Make the tests pass again.",
        mutation=Mutation(file="src/cache.py", line=7, replacement="    x = 1"),
        worktree="/tmp/grokcheck-mutant",  # noqa: S108
        test_command=("pytest",),
    )

    got = (
        grade(question, {"passed": True}).outcome,
        grade(question, {"passed": False}).outcome,
    )
    if got != ("correct", "incorrect"):
        pytest.fail(f"expected (correct, incorrect) for passed True/False, got {got}")


def test_parsons_rejects_distractors_and_scores_indent() -> None:
    """A distractor sinks the answer; a wrong indent costs that line's credit."""
    question = Parsons(
        id="evict",
        prompt="Assemble the eviction step.",
        lines=(
            ParsonsLine(text="if len(self.items) > self.size:", indent=0),
            ParsonsLine(text="oldest = next(iter(self.items))", indent=1),
            ParsonsLine(text="del self.items[oldest]", indent=1),
        ),
        distractors=(
            Option(text="self.items.clear()", why="drops every entry, not one"),
        ),
    )
    solution = [{"text": line.text, "indent": line.indent} for line in question.lines]
    misindented = [*solution[:2], {**solution[2], "indent": 0}]
    variants = {
        "with distractor": [*solution, {"text": "self.items.clear()", "indent": 1}],
        "one wrong indent": misindented,
        "exact solution": solution,
    }
    expected = {
        "with distractor": ("incorrect", 0.0),
        "one wrong indent": ("partial", 2 / 3),
        "exact solution": ("correct", 1.0),
    }

    for variant, response in variants.items():
        result = grade(question, response)
        outcome, score = expected[variant]
        if result.outcome != outcome or result.score != pytest.approx(score):
            got = (result.outcome, result.score)
            pytest.fail(f"{variant}: expected {expected[variant]}, got {got}")


def test_change_impact_scores_overlap_and_reveals_affected() -> None:
    """Naming some affected candidates earns Jaccard credit; the reveal shows all."""
    question = ChangeImpact(
        id="impact",
        prompt="Which callers break if `get` stops refreshing recency?",
        change="`get` no longer moves the key to the end.",
        candidates=(
            Option(text="test_get_refreshes", why="asserts the order after a get"),
            Option(text="Session.lookup", why="relies on hot keys surviving"),
            Option(text="test_put_evicts", why="never calls get"),
            Option(text="warm_cache", why="reads keys to keep them alive"),
        ),
        affected=(0, 1, 3),
    )

    result = grade(question, [0, 1, 2])

    got = (
        result.outcome,
        result.score,
        result.reveal.correct,
        len(result.reveal.why),
    )
    expected = ("partial", 0.5, (0, 1, 3), 4)
    if got != expected:
        pytest.fail(f"expected (outcome, score, affected, whys) {expected}, got {got}")
