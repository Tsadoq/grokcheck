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
    DiffElement,
    MultipleChoice,
    OpenAnswer,
    PlaygroundElement,
    PredictOutput,
    PredictState,
    ProseElement,
    SelectItems,
    SingleChoice,
    TraceElement,
    VideoElement,
    ViewElement,
    VocabElement,
)
from grokcheck.selector import matches

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grokcheck.lesson import Element, Lesson, Question, Section

_CODE_SPAN = re.compile(r"`[^`]*`")
_EMPHASIS = re.compile(r"(\*\*?|__?)[^*_]+?\1")
_ABSOLUTE = re.compile(r"\b(always|never|all|none|only)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|except|false)\b", re.IGNORECASE)
_ALL_OF_THE_ABOVE = re.compile(r"\ball of the above\b", re.IGNORECASE)
_POSITION_BIAS = 0.6
_POSITION_MIN_QUESTIONS = 5
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.?!])[\"'\u201d\u2019)]*\s+")
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
_MAX_CODE_LINES = 12
_MAX_HUNK_LINES = 20
_MAX_LANES = 4
_MAX_X = 12
_MAX_TABLE_ROWS = 10
_MAX_TABLE_COLUMNS = 5
_MAX_SENTENCE_WORDS = 25
_MAX_INTRO_SENTENCES = 12
_MIN_ANSWER_CHARS = 8
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
    warnings = data_media(lesson)
    for i, section in enumerate(lesson.sections):
        warnings.extend(_section_media(i, section))
        warnings.extend(_gates_given_away(i, section))
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
    if plan.subject in {"area", "decision"} and not _names_first(lesson):
        warnings.append(
            LintWarning(
                "sections[0]",
                "no_names_first",
                f"an {plan.subject} lesson needs a vocab element, or a short blocks or"
                " table view, in its first section",
            )
        )
    warnings.extend(_rejected_for_time(plan.rejected, plan.rationale))
    return warnings


def _gates_given_away(i: int, section: Section) -> Iterator[LintWarning]:
    """Warn when a section's body or video transcript states a gate's answer."""
    told = " ".join(
        [section.body]
        + [e.transcript for e in section.elements if isinstance(e, VideoElement)]
    )
    views = {e.id: e for e in section.elements if isinstance(e, ViewElement)}
    for k, question in enumerate(section.checkpoints):
        patterns = _answer_patterns(question, views)
        if patterns and all(re.search(p, told, re.IGNORECASE) for p in patterns):
            yield LintWarning(
                f"sections[{i}].checkpoints[{k}]",
                "gate_given_away",
                "the section's body or video states this gate's answer; ask about"
                " a case they do not state",
            )


def _answer_patterns(question: Question, views: dict[str, ViewElement]) -> list[str]:
    """Return patterns that together find a gate's answer stated in prose."""
    if isinstance(question, (SingleChoice, PredictOutput)) and question.options:
        text = question.options[question.correct or 0].text.strip().rstrip(".")
        if len(text) < _MIN_ANSWER_CHARS:
            return []
        return [rf"(?<!\w){re.escape(text)}(?!\w)"]
    if not isinstance(question, SelectItems):
        return []
    view = views.get(question.view or "")
    where = question.frame.get("where") if question.frame else view and view.where
    if not isinstance(where, dict):
        return []
    return [
        rf"(?<!\w){re.escape(field)}\W{{1,3}}{re.escape(str(value))}(?!\w)"
        for field, value in where.items()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
    ]


def _views(lesson: Lesson) -> Iterator[tuple[str, ViewElement]]:
    for i, section in enumerate(lesson.sections):
        for j, element in enumerate(section.elements):
            if isinstance(element, ViewElement):
                yield f"sections[{i}].elements[{j - section.code_sugar}]", element


def _element_path(i: int, section: Section, j: int) -> str:
    if section.code_sugar and j == 0:
        return f"sections[{i}].code"
    return f"sections[{i}].elements[{j - section.code_sugar}]"


def _section_media(i: int, section: Section) -> Iterator[LintWarning]:
    short = [e for e in section.elements if e.depth != "detail"]
    if short and isinstance(short[0], (CodeElement, DiffElement)):
        yield LintWarning(
            f"sections[{i}]",
            "code_first",
            "open the section with a picture or prose, not code",
        )
    if sum(_heavy(e) for e in short) > 1:
        yield LintWarning(
            f"sections[{i}]",
            "heavy_elements",
            "the short view has more than one heavy element; move one to detail",
        )
    for j, element in enumerate(section.elements):
        path = _element_path(i, section, j)
        if isinstance(element, CodeElement):
            count = len(element.code.text.splitlines())
            if count > _MAX_CODE_LINES:
                yield LintWarning(
                    path,
                    "code_too_long",
                    f"{count} lines of code, over {_MAX_CODE_LINES}",
                )
        if isinstance(element, DiffElement) and len(element.lines) > _MAX_HUNK_LINES:
            yield LintWarning(
                path,
                "code_too_long",
                f"a hunk of {len(element.lines)} lines, over {_MAX_HUNK_LINES}",
            )
        if isinstance(element, ViewElement) and element.depth != "detail":
            message = _too_wide(element)
            if message:
                yield LintWarning(path, "view_too_wide", message)


def _heavy(element: Element) -> bool:
    if isinstance(element, ViewElement):
        return bool(element.inputs) or element.layout == "steps"
    return isinstance(element, (TraceElement, PlaygroundElement))


def _too_wide(view: ViewElement) -> str | None:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for item in view.items:
        key = (*(str(item.get(n)) for n in view.matrix), str(item.get("_pane")))
        groups.setdefault(key, []).append(item)
    if view.layout == "lanes":
        lane, x = str(view.encode.get("lane")), str(view.encode.get("x"))
        for items in groups.values():
            lanes = {str(i.get(lane)) for i in items}
            xs = {str(i.get(x)) for i in items}
            if len(lanes) > _MAX_LANES or len(xs) > _MAX_X:
                return (
                    f"{len(lanes)} lanes and {len(xs)} columns; keep to"
                    f" {_MAX_LANES} lanes and {_MAX_X} columns"
                )
    if view.layout == "table":
        columns = view.encode.get("columns")
        width = len(columns) if isinstance(columns, list) else 0
        rows = max((len(items) for items in groups.values()), default=0)
        if rows > _MAX_TABLE_ROWS or width > _MAX_TABLE_COLUMNS:
            return (
                f"{rows} rows and {width} columns; keep to {_MAX_TABLE_ROWS} rows"
                f" and {_MAX_TABLE_COLUMNS} columns"
            )
    return None


def _names_first(lesson: Lesson) -> bool:
    first = lesson.sections[0].elements if lesson.sections else ()
    return any(
        isinstance(e, VocabElement)
        or (
            isinstance(e, ViewElement)
            and e.depth != "detail"
            and e.layout in {"blocks", "table"}
        )
        for e in first
    )


def _rejected_for_time(
    rejected: tuple[str, ...], rationale: tuple[str, ...]
) -> Iterator[LintWarning]:
    for index, medium in enumerate(rejected):
        if medium == "trace":
            continue
        naming = [line for line in rationale if _names(line, medium)]
        if naming and all(_TIME_REASON.search(line) for line in naming):
            yield LintWarning(
                f"plan.rejected[{index}]",
                "rejected_for_time",
                f"'{medium}' is rejected for time alone; parallel subagents make it"
                " cheap, so give a reason the medium does not fit",
            )


def _names(line: str, name: str) -> bool:
    return (
        re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", line, re.IGNORECASE)
        is not None
    )


def data_media(lesson: Lesson) -> list[LintWarning]:
    """Return a warning for every place `lesson`'s views and datasets break a rule."""
    if not lesson.datasets:
        return []
    warnings: list[LintWarning] = []
    rationale = lesson.plan.rationale if lesson.plan else ()
    first: dict[str, tuple[str, ViewElement]] = {}
    for path, view in _views(lesson):
        if view.data:
            first.setdefault(view.data, (path, view))
    warnings.extend(
        LintWarning(path, "first_case_masked", "show the first case whole: ungate it")
        for path, view in first.values()
        if view.gate is not None
    )
    sources = {use.id: use.source for use in lesson.datasets}
    recorded = [d for d in first if sources.get(d) in {"run", "trace"}]
    warnings.extend(
        LintWarning(
            "plan.rationale",
            "many_datasets",
            f"views draw dataset '{extra}' beside '{recorded[0]}'; say in the"
            " rationale why one running example is not enough",
        )
        for extra in recorded[1:]
        if not any(_names(line, extra) for line in rationale)
    )
    for index, use in enumerate(lesson.datasets):
        if use.source == "authored" and not any(
            _names(line, use.id) and not _TIME_REASON.search(line) for line in rationale
        ):
            warnings.append(
                LintWarning(
                    f"datasets[{index}]",
                    "authored_data",
                    f"'{use.id}' is typed by hand; name it in the rationale with the"
                    " reason a driver cannot record it",
                )
            )
        if use.stale:
            warnings.append(
                LintWarning(
                    f"datasets[{index}]",
                    "stale_data",
                    f"'{use.id}' was recorded before {', '.join(use.stale)} changed;"
                    " record it again",
                )
            )
    if not any(_transfer(lesson, q) for q in lesson.final):
        warnings.append(
            LintWarning(
                "final",
                "final_transfer_missing",
                "add a final question whose inline view draws only held-out rows",
            )
        )
    if not any(
        isinstance(q, SelectItems)
        and q.frame is not None
        and sources.get(str(q.frame.get("data"))) == "wrong"
        for q in lesson.final
    ):
        warnings.append(
            LintWarning(
                "final",
                "final_wrong_data_missing",
                "add a final select_items over a wrong copy of a dataset",
            )
        )
    return warnings


def _transfer(lesson: Lesson, question: Question) -> bool:
    frame = getattr(question, "frame", None)
    if not isinstance(frame, dict):
        return False
    held_out = next(
        (use.held_out for use in lesson.datasets if use.id == frame.get("data")), None
    )
    items = frame.get("items")
    return (
        held_out is not None
        and isinstance(items, list)
        and bool(items)
        and all(isinstance(i, dict) and matches(held_out, i) for i in items)
    )


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
