"""Grading: one response to one question becomes a `Grade`, a set becomes a `Summary`.

Responses arrive as decoded JSON from the browser, shaped per question type:

- `single_choice`, `predict_state`, choice-mode `predict_output`: the chosen
  option index.
- `multiple_choice`: a list of option indexes; `pick_line`: a list of line numbers.
- free-text `predict_output`: the typed output.
- `order_steps`: the step texts in the reader's order.
- `fill_blank`: an object mapping blank id to text, or a plain string when the
  question has a single blank.
- `open_answer`: `{"text": ..., "met": [...]}`, one boolean per rubric item from
  the reader's self-rating.
- `mutation_quiz`: a list of the test names the reader ticked as failing.
- `fix_the_bug`: `{"passed": ...}`, the result of the server's own test run.
- `change_impact`: a list of candidate indexes.
- `parsons`: `[{"text": ..., "indent": ...}]`, the lines the reader placed, in order.
- `select_items`: a list of the `_cell` strings the reader picked.
- `fill_table`: `{"<cell>": {"<field>": value}}`, one value per blank.

A response of the wrong shape raises `ResponseError`.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeVar

from grokcheck.lesson import (
    Blank,
    ChangeImpact,
    FillBlank,
    FillTable,
    FixTheBug,
    MultipleChoice,
    Mutation,
    MutationQuiz,
    OpenAnswer,
    Option,
    OrderSteps,
    Parsons,
    ParsonsLine,
    PickLine,
    PredictOutput,
    PredictState,
    SelectItems,
    SingleChoice,
    TestCase,
)
from grokcheck.selector import same

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from grokcheck.lesson import Question

Outcome = Literal["correct", "incorrect", "partial", "needs_review", "self_rated"]
Confidence = Literal["sure", "unsure", "guess"]
Response = (
    int | str | list[int] | list[str] | list[dict[str, object]] | dict[str, object]
)

_CURLY_QUOTES = str.maketrans(
    {
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{SINGLE LOW-9 QUOTATION MARK}": "'",
        "\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{DOUBLE LOW-9 QUOTATION MARK}": '"',
        "\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}": '"',
    },
)
_T = TypeVar("_T")
_SPACE_AROUND_SYMBOL = re.compile(r"\s*([^\w\s])\s*")
_WHITESPACE_RUN = re.compile(r"\s+")


class ResponseError(ValueError):
    """A response does not have the shape its question type expects."""


@dataclass(frozen=True)
class Reveal:
    """What the reader is shown after answering; only the fields for the type are set.

    `why` is aligned with the question's options; `accepted` holds free-text
    `predict_output` answers and `blanks` the fill-the-blank answer key.
    `tests` and `log` are a mutation quiz's run; `mutation` is a fixed bug's
    original line.
    `lines` and `distractors` are a Parsons problem's solution and decoys.
    `answer_cells` are the cells a `select_items` answer holds, `cells` the
    right values of a `fill_table`.
    `element_payload` is what a gated element withheld until this answer.
    """

    explanation: str
    why: tuple[str, ...] = ()
    correct: tuple[int, ...] = ()
    answer_lines: tuple[int, ...] = ()
    steps: tuple[str, ...] = ()
    accepted: tuple[str, ...] = ()
    blanks: tuple[Blank, ...] = ()
    model_answer: str = ""
    rubric: tuple[str, ...] = ()
    tests: tuple[TestCase, ...] = ()
    log: str = ""
    mutation: Mutation | None = None
    lines: tuple[ParsonsLine, ...] = ()
    distractors: tuple[Option, ...] = ()
    answer_cells: tuple[str, ...] = ()
    cells: dict[str, dict[str, object]] = field(default_factory=dict)
    element_payload: dict[str, object] | None = None


@dataclass(frozen=True)
class Grade:
    """The outcome of one response, with `score` in [0, 1] and the reveal payload.

    For `needs_review` the score is provisional, counting only what matched an
    accepted answer; for `self_rated` it is the reader's own rubric tally.
    """

    question_id: str
    outcome: Outcome
    score: float
    response: Response
    confidence: Confidence | None
    reveal: Reveal


@dataclass(frozen=True)
class Summary:
    """Totals over a set of grades; the id lists tell the agent what to look at first.

    `confident_wrong` holds questions rated `sure` whose outcome is not `correct`.
    """

    final_score: float
    confident_wrong: tuple[str, ...]
    needs_review: tuple[str, ...]
    self_rated: tuple[str, ...]


def grade(
    question: Question,
    response: Response,
    confidence: Confidence | None = None,
) -> Grade:
    """Grade `response` to `question`, recording the reader's `confidence` if given.

    Closed types are graded outright. Free text that matches no accepted answer
    is `needs_review`, never `incorrect`, because it may be an unforeseen right
    answer that only the agent can judge.
    """
    outcome, score, reveal = _grade(question, response)
    return Grade(question.id, outcome, score, response, confidence, reveal)


def summarise(grades: Iterable[Grade]) -> Summary:
    """Summarise `grades`; `final_score` is their mean score, 0 when there are none."""
    graded = tuple(grades)
    return Summary(
        final_score=sum(g.score for g in graded) / len(graded) if graded else 0.0,
        confident_wrong=tuple(
            g.question_id
            for g in graded
            if g.confidence == "sure" and g.outcome != "correct"
        ),
        needs_review=tuple(
            g.question_id for g in graded if g.outcome == "needs_review"
        ),
        self_rated=tuple(g.question_id for g in graded if g.outcome == "self_rated"),
    )


def normalise(
    text: str,
    *,
    case_sensitive: bool = True,
    quote_insensitive: bool = False,
) -> str:
    """Reduce `text` to the form compared against accepted answers.

    Applies Unicode NFC, straightens curly quotes, drops whitespace next to a
    symbol, and collapses other whitespace runs to one space. Punctuation is kept.
    """
    text = unicodedata.normalize("NFC", text).translate(_CURLY_QUOTES)
    text = _SPACE_AROUND_SYMBOL.sub(r"\1", text)
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    if quote_insensitive:
        text = text.replace('"', "'")
    return text if case_sensitive else text.casefold()


def _grade(  # noqa: C901
    question: Question, response: Response
) -> tuple[Outcome, float, Reveal]:
    explanation = question.explanation
    match question:
        case (
            SingleChoice(options=options, correct=int() as correct)
            | PredictOutput(options=options, correct=int() as correct)
            | PredictState(options=options, correct=correct)
        ):
            result = _exact(_index(response, len(options)) == correct)
            reveal = Reveal(
                explanation, why=tuple(o.why for o in options), correct=(correct,)
            )
        case (
            MultipleChoice(options=options, correct=correct_set)
            | ChangeImpact(candidates=options, affected=correct_set)
        ):
            chosen = {_index(item, len(options)) for item in _items(response)}
            result = _overlap(chosen, set(correct_set))
            reveal = Reveal(
                explanation, why=tuple(o.why for o in options), correct=correct_set
            )
        case PredictOutput(accepted=accepted):
            result = _free_text(_text(response), accepted)
            reveal = Reveal(explanation, accepted=accepted)
        case PickLine(code=code, answer_lines=answer_lines):
            line_count = len(code.text.splitlines())
            picked = {_index(item, line_count, first=1) for item in _items(response)}
            result = _overlap(picked, set(answer_lines))
            reveal = Reveal(explanation, answer_lines=answer_lines)
        case OrderSteps(steps=steps):
            result = _order(response, steps)
            reveal = Reveal(explanation, steps=steps)
        case FillBlank(blanks=blanks):
            result = _fill(response, blanks)
            reveal = Reveal(explanation, blanks=blanks)
        case OpenAnswer(model_answer=model_answer, rubric=rubric):
            result = ("self_rated", _self_rating(response, len(rubric)))
            reveal = Reveal(explanation, model_answer=model_answer, rubric=rubric)
        case MutationQuiz(tests=tests, log=log):
            failing = {test.name for test in tests if test.fails}
            result = _exact(_ticked(response) == failing)
            reveal = Reveal(explanation, tests=tests, log=log)
        case FixTheBug(mutation=mutation):
            result = _exact(_passed(response))
            reveal = Reveal(explanation, mutation=mutation)
        case Parsons(lines=lines, distractors=distractors):
            result = _parsons(response, lines, distractors)
            reveal = Reveal(explanation, lines=lines, distractors=distractors)
        case SelectItems(candidates=candidates, answer_cells=answer_cells):
            result = _overlap(_cells(response, candidates), set(answer_cells))
            reveal = Reveal(explanation, answer_cells=answer_cells)
        case FillTable(cells=cells):
            result = _table(response, cells)
            reveal = Reveal(explanation, cells=cells)
    return (*result, reveal)


def _free_text(answer: str, accepted: tuple[str, ...]) -> tuple[Outcome, float]:
    return ("correct", 1.0) if _matches(answer, accepted) else ("needs_review", 0.0)


def _exact(is_correct: bool) -> tuple[Outcome, float]:  # noqa: FBT001
    return ("correct", 1.0) if is_correct else ("incorrect", 0.0)


def _overlap(chosen: set[_T], correct: set[_T]) -> tuple[Outcome, float]:
    """Exact match is correct; otherwise partial by Jaccard index, or incorrect at 0."""
    if chosen == correct:
        return "correct", 1.0
    union = chosen | correct
    score = len(chosen & correct) / len(union) if union else 0.0
    return ("partial" if score else "incorrect"), score


def _cells(response: Response, candidates: tuple[str, ...]) -> set[str]:
    picked = _items(response)
    if not all(isinstance(cell, str) and cell in candidates for cell in picked):
        msg = "must list cells of the view"
        raise ResponseError(msg)
    return {str(cell) for cell in picked}


def _table(
    response: Response, cells: dict[str, dict[str, object]]
) -> tuple[Outcome, float]:
    """Score the share of blanks filled with the recorded value."""
    msg = "must map each cell to an object of field values"
    if not isinstance(response, dict):
        raise ResponseError(msg)
    given: dict[str, dict[str, object]] = {}
    for cell, row in response.items():
        if not isinstance(row, dict):
            raise ResponseError(msg)
        given[cell] = row
    total = sum(len(row) for row in cells.values())
    right = sum(
        field_name in given.get(cell, {}) and same(given[cell][field_name], value)
        for cell, row in cells.items()
        for field_name, value in row.items()
    )
    score = right / total if total else 0.0
    if score == 1:
        return "correct", 1.0
    return ("partial" if score else "incorrect"), score


def _order(response: Response, steps: tuple[str, ...]) -> tuple[Outcome, float]:
    """Score by the longest subsequence of the reader's order that is in order."""
    shown = [step for step in _items(response) if isinstance(step, str)]
    if sorted(shown) != sorted(steps) or len(shown) != len(_items(response)):
        msg = "must list every step exactly once"
        raise ResponseError(msg)
    positions = [steps.index(step) for step in shown]
    if positions == sorted(positions):
        return "correct", 1.0
    return "partial", _longest_increasing(positions) / len(steps)


def _parsons(
    response: Response,
    lines: tuple[ParsonsLine, ...],
    distractors: tuple[Option, ...],
) -> tuple[Outcome, float]:
    """Score zero with any distractor, else lines placed in order at their indent."""
    placed = [_placed_line(item) for item in _items(response)]
    texts = [line.text for line in lines]
    decoys = {d.text for d in distractors}
    shown = [text for text, _ in placed]
    if len(set(shown)) != len(shown) or not set(shown) <= set(texts) | decoys:
        msg = "must list known lines, each at most once"
        raise ResponseError(msg)
    if decoys & set(shown):
        return "incorrect", 0.0
    if placed == [(line.text, line.indent) for line in lines]:
        return "correct", 1.0
    positions = [
        texts.index(text)
        for text, indent in placed
        if indent == lines[texts.index(text)].indent
    ]
    score = _longest_increasing(positions) / len(lines)
    return ("partial" if score else "incorrect"), score


def _placed_line(item: object) -> tuple[str, int]:
    text = item.get("text") if isinstance(item, dict) else None
    indent = item.get("indent") if isinstance(item, dict) else None
    if (
        not isinstance(text, str)
        or not isinstance(indent, int)
        or isinstance(indent, bool)
    ):
        msg = "each placed line must carry 'text' and an integer 'indent'"
        raise ResponseError(msg)
    return text, indent


def _longest_increasing(values: Sequence[int]) -> int:
    tails: list[int] = []
    for value in values:
        at = bisect_left(tails, value)
        tails[at : at + 1] = [value]
    return len(tails)


def _fill(response: Response, blanks: tuple[Blank, ...]) -> tuple[Outcome, float]:
    answers = _blank_answers(response, blanks)
    matched = sum(
        _matches(
            answers[blank.id],
            blank.accepted,
            case_sensitive=blank.case_sensitive,
            quote_insensitive=blank.quote_insensitive,
        )
        for blank in blanks
    )
    if matched == len(blanks):
        return "correct", 1.0
    return "needs_review", matched / len(blanks)


def _blank_answers(response: Response, blanks: tuple[Blank, ...]) -> dict[str, str]:
    if isinstance(response, str) and len(blanks) == 1:
        return {blanks[0].id: response}
    if not isinstance(response, dict):
        msg = "must map each blank id to its answer"
        raise ResponseError(msg)
    answers = {}
    for blank in blanks:
        answer = response.get(blank.id)
        if not isinstance(answer, str):
            msg = f"blank '{blank.id}' must have a text answer"
            raise ResponseError(msg)
        answers[blank.id] = answer
    return answers


def _matches(
    answer: str,
    accepted: Iterable[str],
    *,
    case_sensitive: bool = True,
    quote_insensitive: bool = False,
) -> bool:
    def norm(text: str) -> str:
        return normalise(
            text,
            case_sensitive=case_sensitive,
            quote_insensitive=quote_insensitive,
        )

    given = norm(answer)
    return any(norm(candidate) == given for candidate in accepted)


def _self_rating(response: Response, rubric_size: int) -> float:
    met = response.get("met") if isinstance(response, dict) else None
    if (
        not isinstance(met, list)
        or len(met) != rubric_size
        or not all(isinstance(item, bool) for item in met)
    ):
        msg = f"must carry 'met' with one boolean per rubric item ({rubric_size})"
        raise ResponseError(msg)
    return sum(met) / rubric_size


def _ticked(response: Response) -> set[str]:
    ticked = _items(response)
    if not all(isinstance(item, str) for item in ticked):
        msg = "must list test names"
        raise ResponseError(msg)
    return {str(item) for item in ticked}


def _passed(response: Response) -> bool:
    passed = response.get("passed") if isinstance(response, dict) else None
    if not isinstance(passed, bool):
        msg = "must carry 'passed' as true or false"
        raise ResponseError(msg)
    return passed


def _index(value: object, count: int, *, first: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not first <= value < first + count
    ):
        msg = f"{value!r} is not between {first} and {first + count - 1}"
        raise ResponseError(msg)
    return value


def _items(response: Response) -> list[object]:
    if not isinstance(response, list):
        msg = "must be a list"
        raise ResponseError(msg)
    return list(response)


def _text(response: Response) -> str:
    if not isinstance(response, str):
        msg = "must be text"
        raise ResponseError(msg)
    return response
