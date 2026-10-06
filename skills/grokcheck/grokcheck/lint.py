"""Warnings for item-writing flaws a rule can detect, after Moore et al.

Each flaw lets a test-wise reader answer without understanding the code, so
`validate` reports them beside a lesson that otherwise loads cleanly.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from statistics import median
from typing import TYPE_CHECKING

from grokcheck.lesson import (
    CodeElement,
    DiagramElement,
    MultipleChoice,
    OpenAnswer,
    PredictOutput,
    PredictState,
    ProseElement,
    SingleChoice,
    VocabElement,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grokcheck.lesson import Lesson, Question

_CODE_SPAN = re.compile(r"`[^`]*`")
_EMPHASIS = re.compile(r"(\*\*?|__?)[^*_]+?\1")
_ABSOLUTE = re.compile(r"\b(always|never|all|none|only)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|except|false)\b", re.IGNORECASE)
_ALL_OF_THE_ABOVE = re.compile(r"\ball of the above\b", re.IGNORECASE)
_POSITION_BIAS = 0.6
_POSITION_MIN_QUESTIONS = 5
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_PARAGRAPH = re.compile(r"\n\s*\n")
_ACRONYM = re.compile(r"\b[A-Z]{3,}\b")
_DEFINITION = re.compile(r"\b([A-Z]{3,})\s*\(|\(([A-Z]{3,})\)")
_ANALOGY = re.compile(r"\b(like a|as if)\b", re.IGNORECASE)
_ANALOGY_LIMIT = re.compile(r"\b(but|unlike|breaks)\b", re.IGNORECASE)
_BANNED = re.compile(
    r"\b(delve|crucial|pivotal|underscore|tapestry|testament|intricate|showcase"
    r"|foster|robust|seamless|leverage|comprehensive|illuminate|unpack|catalyze"
    r"|load-bearing|lean into|it is worth noting|crucially|fundamentally|notably)\b",
    re.IGNORECASE,
)
_TIME_REASON = re.compile(r"minute|time|budget|too long|cost|effort", re.IGNORECASE)
_MAX_SENTENCE_WORDS = 25
_MAX_INTRO_SENTENCES = 12
_ACTION_STARTS = frozenset(
    {
        "so", "ask", "cancel", "check", "compare", "find", "fix", "follow",
        "keep", "look", "note", "open", "predict", "read", "remember", "run",
        "start", "test", "trace", "try", "use", "watch",
    }
)  # fmt: skip


@dataclass(frozen=True)
class LintWarning:
    """One flaw at the JSON path of the text it is in, or `$` for the whole lesson."""

    path: str
    rule: str
    message: str


def item_flaws(lesson: Lesson) -> list[LintWarning]:
    """Return a warning for every item-writing flaw in `lesson`'s questions."""
    warnings: list[LintWarning] = []
    positions: Counter[int] = Counter()
    for path, question in lesson.questions():
        warnings.extend(
            LintWarning(path, rule, message) for rule, message in _flaws(question)
        )
        correct = _correct(question)
        if len(correct) == 1:
            positions.update(correct)
    total = positions.total()
    if total >= _POSITION_MIN_QUESTIONS:
        position, count = positions.most_common(1)[0]
        if count / total > _POSITION_BIAS:
            warnings.append(
                LintWarning(
                    "$",
                    "correct_position_bias",
                    f"{count} of {total} single-answer questions put the correct "
                    f"option at index {position}",
                )
            )
    return warnings


def _flaws(question: Question) -> Iterator[tuple[str, str]]:
    if _unemphasised_negation(question.prompt):
        yield (
            "negated_stem",
            "the prompt negates (not, except, false) without emphasising it",
        )
    if isinstance(question, OpenAnswer) and not (
        "Met:" in question.explanation and "Unmet:" in question.explanation
    ):
        yield (
            "too_few_rubric_examples",
            "the explanation needs a 'Met:' and an 'Unmet:' example answer",
        )
    options = [option.text for option in getattr(question, "options", ())]
    if not options:
        return
    yield from _option_flaws(options, _correct(question))


def _option_flaws(options: list[str], correct: set[int]) -> Iterator[tuple[str, str]]:
    lengths = [len(text) for text in options]
    if len(correct) == 1:
        (right,) = correct
        longest_wrong = max(n for i, n in enumerate(lengths) if i != right)
        if lengths[right] - longest_wrong > 0.25 * median(lengths):
            yield "longest_correct", "the correct option is clearly the longest"
    if max(lengths) > 2 * min(lengths):
        yield "length_spread", "the longest option is over twice the shortest"
    if any(
        _ABSOLUTE.search(_plain(text))
        for i, text in enumerate(options)
        if i not in correct
    ):
        yield (
            "absolute_distractor",
            "a wrong option uses an absolute (always, never, all, none, only)",
        )
    if any(_ALL_OF_THE_ABOVE.search(text) for text in options):
        yield "all_of_the_above", "an option is 'all of the above'"
    if len({text.strip().casefold() for text in options}) < len(options):
        yield "duplicate_option_text", "two options have the same text"


def _correct(question: Question) -> set[int]:
    """Return the correct option indices, empty for a question without options."""
    if isinstance(question, MultipleChoice):
        return set(question.correct)
    if isinstance(question, (SingleChoice, PredictState)):
        return {question.correct}
    if isinstance(question, PredictOutput) and question.correct is not None:
        return {question.correct}
    return set()


def _plain(text: str) -> str:
    """Return `text` without code, which quotes code rather than claims."""
    return _CODE_SPAN.sub("", _FENCE.sub("", text))


def _unemphasised_negation(prompt: str) -> bool:
    """Tell whether `prompt` negates outside `*emphasis*` and ALL CAPS."""
    words = _EMPHASIS.sub("", _plain(prompt)).split()
    return bool(_NEGATION.search(" ".join(w for w in words if not w.isupper())))


def prose(lesson: Lesson) -> list[LintWarning]:
    """Return a warning for every Simple Technical English slip in `lesson`'s prose.

    That prose is the intro, each section body and prose element, and every
    question prompt and explanation.
    """
    texts = list(_prose_texts(lesson))
    defined = {
        match.group(1) or match.group(2)
        for _, text in texts
        for match in _DEFINITION.finditer(_plain(text))
    }
    warnings: list[LintWarning] = []
    reported: set[str] = set()
    for path, text in texts:
        for rule, message in _prose_flaws(text):
            warnings.append(LintWarning(path, rule, message))
        for acronym in _ACRONYM.findall(_plain(text)):
            if (
                acronym in defined
                or acronym in reported
                or _NEGATION.fullmatch(acronym)
            ):
                continue
            reported.add(acronym)
            warnings.append(
                LintWarning(
                    path,
                    "undefined_acronym",
                    f"'{acronym}' is never spelled out in parentheses",
                )
            )
    return warnings


def intro(lesson: Lesson) -> list[LintWarning]:
    """Return a warning for every narration checklist item `lesson.intro` misses."""
    if not lesson.intro:
        return []
    sentences = _sentences(lesson.intro)
    nouns = {
        word.casefold()
        for file in lesson.scope.files
        for part in PurePosixPath(file).with_suffix("").parts
        for word in re.split(r"[^A-Za-z0-9]+", part)
        if len(word) >= 3  # noqa: PLR2004
    }
    opening = " ".join(sentences[:2])
    titles = [section.title.casefold() for section in lesson.sections]
    failures = {
        "intro_no_stakes": (
            not re.search(r"\d", opening)
            and not any(
                re.search(rf"\b{re.escape(noun)}", opening, re.IGNORECASE)
                for noun in nouns
            ),
            "the first two sentences name no file from the scope and no number",
        ),
        "intro_no_preview": (
            not any(sum(t in s.casefold() for t in titles) >= 2 for s in sentences),  # noqa: PLR2004
            "no sentence previews two or more section titles",
        ),
        "intro_two_analogies": (
            len(_ANALOGY.findall(lesson.intro)) > 1,
            "the intro has more than one analogy ('like a', 'as if')",
        ),
        "intro_last_not_action": (
            _first_word(sentences[-1]) not in _ACTION_STARTS,
            "the last sentence is not an action (an imperative or 'So')",
        ),
        "intro_too_long": (
            len(sentences) > _MAX_INTRO_SENTENCES,
            f"the intro has {len(sentences)} sentences, over {_MAX_INTRO_SENTENCES}",
        ),
    }
    return [
        LintWarning("intro", rule, message)
        for rule, (failed, message) in failures.items()
        if failed
    ]


def media(lesson: Lesson) -> list[LintWarning]:
    """Return a warning for every place `lesson` shows an idea through prose alone."""
    warnings: list[LintWarning] = []
    for i, section in enumerate(lesson.sections):
        short = [e for e in section.elements if e.depth != "detail"]
        if all(isinstance(e, (ProseElement, CodeElement)) for e in short):
            warnings.append(
                LintWarning(
                    f"sections[{i}]",
                    "prose_only_section",
                    "the short view has only prose and code; add a diagram, trace, "
                    "vocab, diff or other element that shows the idea",
                )
            )
        warnings.extend(
            LintWarning(
                f"sections[{i}].elements[{j - section.code_sugar}]",
                "diagram_in_detail",
                "move the diagram to the short view, next to the text it explains",
            )
            for j, element in enumerate(section.elements)
            if isinstance(element, DiagramElement) and element.depth == "detail"
        )
    plan = lesson.plan
    if plan is None:
        return warnings
    if (
        plan.subject != "document"
        and "trace" in plan.rejected
        and "behaviour" in plan.content
        and not _trace_explained(plan.rationale)
    ):
        warnings.append(
            LintWarning(
                "plan.rejected",
                "trace_rejected",
                "behaviour needs a trace: record one, or say in the rationale why "
                "the code cannot run in isolation",
            )
        )
    if plan.subject in {"area", "decision"} and not any(
        isinstance(e, VocabElement) for s in lesson.sections for e in s.elements
    ):
        warnings.append(
            LintWarning(
                "$",
                "no_vocab",
                f"an {plan.subject} lesson needs a vocab element for its names",
            )
        )
    return warnings


def _trace_explained(rationale: tuple[str, ...]) -> bool:
    """Tell whether a rationale sentence names the trace with a reason besides time."""
    return any(
        "trace" in line.casefold() and not _TIME_REASON.search(line)
        for line in rationale
    )


def _prose_texts(lesson: Lesson) -> Iterator[tuple[str, str]]:
    if lesson.intro:
        yield "intro", lesson.intro
    for i, section in enumerate(lesson.sections):
        yield f"sections[{i}].body", section.body
        for j, element in enumerate(section.elements):
            if isinstance(element, ProseElement):
                authored = j - section.code_sugar
                yield f"sections[{i}].elements[{authored}].markdown", element.markdown
    for path, question in lesson.questions():
        yield f"{path}.prompt", question.prompt
        if question.explanation:
            yield f"{path}.explanation", question.explanation


def _prose_flaws(text: str) -> Iterator[tuple[str, str]]:
    plain = _plain(text)
    for sentence in _sentences(text):
        words = sentence.split()
        if len(words) > _MAX_SENTENCE_WORDS:
            yield (
                "long_sentence",
                f"{len(words)}-word sentence starting '{' '.join(words[:6])}'",
            )
    if ";" in plain:
        yield "semicolon", "split the sentence at the semicolon"
    if "\u2014" in plain:
        yield "em_dash", "replace the em dash with a full stop, comma or parentheses"
    for phrase in dict.fromkeys(m.casefold() for m in _BANNED.findall(plain)):
        noun = "phrase" if " " in phrase else "word"
        yield "banned_word", f"banned {noun} '{phrase}'"
    for paragraph in _PARAGRAPH.split(plain):
        if _ANALOGY.search(paragraph) and not _ANALOGY_LIMIT.search(paragraph):
            yield (
                "analogy_without_limit",
                "an analogy with no 'but', 'unlike' or 'breaks' to say where it stops",
            )


def _sentences(text: str) -> list[str]:
    """Split `text` into sentences, keeping code spans whole as one word each."""
    plain = _CODE_SPAN.sub("code", _FENCE.sub("", text))
    return [s for s in _SENTENCE_END.split(plain.strip()) if s.strip()]


def _first_word(sentence: str) -> str:
    words = re.findall(r"[A-Za-z']+", sentence)
    return words[0].casefold() if words else ""
