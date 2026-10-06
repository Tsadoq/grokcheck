"""Export: a finished lesson rendered as one markdown file for rereading later.

The file stands alone: it holds the explanation, the reader's questions with the
agent's replies under the section they were asked in, and the final quiz with
the reader's answers, how they were graded, and why.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from grokcheck.lesson import (
    AssumptionsElement,
    ChangeImpact,
    CodeBlock,
    CodeElement,
    DataBacking,
    DiagramElement,
    DiffElement,
    FillBlank,
    FillTable,
    FixTheBug,
    LinesBacking,
    MultipleChoice,
    MutationQuiz,
    OpenAnswer,
    OptionConstraint,
    OptionsElement,
    OrderSteps,
    Parsons,
    ParsonsLine,
    PickLine,
    PlaygroundElement,
    PredictOutput,
    ProseElement,
    SelectItems,
    SingleChoice,
    SourceBacking,
    SpikeBacking,
    SpikeElement,
    TraceElement,
    VideoElement,
    ViewElement,
    VocabElement,
)
from grokcheck.schedule import local_today, missed

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import Any

    from grokcheck.grading import Grade, Response
    from grokcheck.lesson import (
        Backing,
        Claim,
        Element,
        Lesson,
        Option,
        Question,
        Section,
    )

QuestionMode = Literal["answer", "socratic"]

_BACKTICK_RUN = re.compile(r"`+")
_COLON_PAIR = re.compile(r":(?=:)")
_CODE_OR_COLON_PAIR = re.compile(
    r"(?P<code>^[ ]{0,3}(?P<fence>`{3,}|~{3,})"
    r".*?(?:\n[ ]{0,3}(?P=fence)[`~]*[ \t]*$|\Z)"
    r"|(?P<ticks>`+).+?(?<!`)(?P=ticks)(?!`))"
    r"|:(?=:)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class QuestionThread:
    """A question the reader asked in a section, and the agent's reply to it.

    `selection` is the text the reader highlighted when asking, empty when they
    used the section's Ask box; `reply` is empty until the agent answers.
    With `mode` "socratic" the reader asked to be questioned, not answered.
    """

    id: str
    section_id: str
    selection: str
    text: str
    reply: str = ""
    mode: QuestionMode = "answer"


def render_markdown(
    lesson: Lesson,
    grades: Mapping[str, Grade],
    qa: Sequence[QuestionThread],
) -> str:
    """Render `lesson` with the reader's `qa` threads and final-quiz `grades`.

    `grades` is keyed by question id; a final question with no grade is shown
    as unanswered. Threads naming a section the lesson lacks are left out.
    """
    blocks = [f"# {lesson.title}", lesson.scope.summary]
    if lesson.scope.files:
        blocks.append("\n".join(f"- `{path}`" for path in lesson.scope.files))
    for section in lesson.sections:
        threads = [thread for thread in qa if thread.section_id == section.id]
        blocks.extend(_section(section, threads))
    blocks.append("## Final quiz")
    for number, question in enumerate(lesson.final, start=1):
        blocks.extend(_final_question(number, question, grades.get(question.id)))
    return "\n\n".join(blocks) + "\n"


def _section(section: Section, threads: Sequence[QuestionThread]) -> list[str]:
    blocks = [f"## {section.title}", section.body]
    for element in section.elements:
        blocks.extend(_element(element))
    if threads:
        blocks.append("### Your questions")
        blocks.extend(_thread(thread) for thread in threads)
    return blocks


def _element(element: Element) -> list[str]:
    render = _ELEMENT_RENDERERS.get(type(element))
    blocks = render(element) if render else []
    if element.claims:
        blocks.append("\n".join(_claim(claim) for claim in element.claims))
    return blocks


def _code_element(element: CodeElement) -> list[str]:
    return [_code(element.code), *([element.caption] if element.caption else [])]


def _vocab_element(element: VocabElement) -> list[str]:
    return [*([_code(element.code)] if element.code else []), _vocab(element)]


def _assumptions(element: AssumptionsElement) -> list[str]:
    return [
        "\n".join(
            f"- {item.claim} {_cite(item.backing)}"
            + (
                f" (checked by spike `{item.checked_by_spike}`)"
                if item.checked_by_spike
                else ""
            )
            for item in element.items
        )
    ]


def _diagram(element: DiagramElement) -> list[str]:
    return [f"```mermaid\n{element.mermaid}\n```", element.caption]


def _prose(element: ProseElement) -> list[str]:
    return [element.markdown]


def _video(element: VideoElement) -> list[str]:
    link = Path(element.src).as_uri()
    return [f"[Video, {round(element.duration)} s](<{link}>)", element.transcript]


def _trace(element: TraceElement) -> list[str]:
    """Render each version as its code, then its steps as a numbered list."""
    blocks = [f"**{element.title}**"]
    for version in element.versions:
        blocks.append(f"*{version.label}*\n\n{_code(version.code)}")
        blocks.append(
            "\n".join(
                f"{number}. "
                + (f"Line {step.cur_line}: " if step.cur_line is not None else "")
                + " ".join(step.narration)
                for number, step in enumerate(version.steps, start=1)
            )
        )
    return blocks


def _spike(element: SpikeElement) -> list[str]:
    """Render the hypothesis, the pinned versions, then the recorded stdout."""
    pins = ", ".join(f"`{name}=={version}`" for name, version in element.pins.items())
    return [
        f"**Spike `{element.spike_id}`:** {element.hypothesis}",
        (
            f"Pinned: {pins or 'stdlib only'}; packages newer than"
            f" {element.exclude_newer} excluded."
        ),
        _fence(str(element.result["stdout"]).rstrip("\n"), "text"),
    ]


def _playground(element: PlaygroundElement) -> list[str]:
    """Render one table row per recorded state, then the tasks as a list."""
    header = ["Inputs", *element.variants, "What happens"]
    rows = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    rows.extend(
        "| "
        + " | ".join(
            cell.replace("|", "\\|")
            for cell in (
                state.key,
                *(
                    f"lost {o.lost}" if o.outcome == "lost" else o.outcome
                    for o in state.outcomes
                ),
                state.explain,
            )
        )
        + " |"
        for state in element.states
    )
    tasks = "\n".join(f"- {task.text}" for task in element.tasks)
    return [
        f"Inputs, in order: {', '.join(item.label for item in element.inputs)}.",
        "\n".join(rows),
        *([tasks] if tasks else []),
    ]


def _options(element: OptionsElement) -> list[str]:
    """Render the question and criteria, then one table row per option."""
    rows = [
        "| Option | Standing | Costs | Assumes | Constraints |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        "| "
        + " | ".join(
            cell.replace("|", "\\|")
            for cell in (
                option.name,
                option.standing.replace("_", " "),
                option.costs,
                option.assumes,
                "; ".join(
                    f"{constraint.text} ({_constraint_kind(constraint)})"
                    for constraint in option.constraints
                ),
            )
        )
        + " |"
        for option in element.options
    )
    return [
        f"**{element.question}**",
        "Criteria: " + ", ".join(element.criteria),
        "\n".join(rows),
    ]


def _constraint_kind(constraint: OptionConstraint) -> str:
    if constraint.hard:
        return "hard"
    return f"waivable by {constraint.waivable_by}" if constraint.waivable_by else "soft"


def _diff(element: DiffElement) -> list[str]:
    """Render the hunk as a unified diff fence, then each note on its new lines."""
    hunk = "\n".join(
        [f"@@ -{element.old_start} +{element.new_start} @@"]
        + [line.op + line.text for line in element.lines]
    )
    blocks = [f"`{element.file}`\n\n{_fence(hunk, 'diff')}"]
    if element.notes:
        blocks.append(
            "\n".join(
                f"- Lines {note.lines[0]}-{note.lines[1]}: {note.text}"
                f" {_cite(note.cite)}"
                for note in element.notes
            )
        )
    return blocks


def _vocab(element: VocabElement) -> str:
    rows = [
        "| Term | Owner | Definition | Library term | False friend |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        "| "
        + " | ".join(
            cell.replace("|", "\\|")
            for cell in (
                f"`{term.term}`",
                term.owner,
                term.definition,
                term.library_term or "",
                "yes" if term.false_friend else "",
            )
        )
        + " |"
        for term in element.terms
    )
    return "\n".join(rows)


_MAX_VIEW_ROWS = 50


def _view(element: ViewElement) -> list[str]:
    """Render steps as code and a note each, any other layout as a table of items."""
    blocks = [f"**{element.caption}**"]
    if element.layout == "steps":
        for number, step in enumerate(element.steps, start=1):
            code = "\n\n".join(
                _code(
                    CodeBlock(
                        step.code.language, span.text, step.code.file, span.start_line
                    )
                )
                for span in step.code.spans
            )
            shown = ", ".join(f"`{k}` = `{v}`" for k, v in step.show.items())
            notes = " ".join(text for text in (step.note, shown) if text)
            blocks.append(
                f"{number}. {notes}\n\n{code}" if notes else f"{number}.\n\n{code}"
            )
        return blocks
    if element.layout == "decision":
        blocks.append(
            "\n".join(
                f"- **{node.label}** ({node.kind}, `{node.code.file}`"
                f" lines {node.lines[0]}-{node.lines[1]}): {node.note}"
                for node in element.nodes
            )
        )
    else:
        blocks.append(_items_table(element))
    if element.notes:
        blocks.append("\n".join(f"- {note.text}" for note in element.notes))
    return blocks


def _items_table(element: ViewElement) -> str:
    encode = element.encode
    columns = encode.get("columns")
    if isinstance(columns, list):
        fields = [
            (str(c.get("field")), str(c.get("label")))
            for c in columns
            if isinstance(c, dict)
        ]
    else:
        names = ("lane", "x", "key", "label", "class", "group")
        fields = [
            (str(encode[name]), str(encode[name]))
            for name in names
            if isinstance(encode.get(name), str)
        ]
    if element.split:
        fields.insert(0, (element.split, element.split))
    fields = list(dict.fromkeys(fields))
    flags = [("_lost", "lost"), ("_dup", "repeat")]
    rows = [
        "| " + " | ".join(label for _, label in fields) + " | |",
        "|" + " --- |" * (len(fields) + 1),
    ]
    for item in element.items[:_MAX_VIEW_ROWS]:
        marks = ", ".join(word for flag, word in flags if item.get(flag))
        cells = [str(item.get(name, "")) for name, _ in fields]
        line = " | ".join(c.replace("|", "\\|") for c in (*cells, marks))
        rows.append("| " + line.replace("\n", " ") + " |")
    extra = len(element.items) - _MAX_VIEW_ROWS
    if extra > 0:
        rows.append(f"\n_{extra} more rows not shown._")
    return "\n".join(rows)


_ELEMENT_RENDERERS: dict[type, Callable[[Any], list[str]]] = {
    CodeElement: _code_element,
    DiffElement: _diff,
    VocabElement: _vocab_element,
    TraceElement: _trace,
    SpikeElement: _spike,
    PlaygroundElement: _playground,
    OptionsElement: _options,
    AssumptionsElement: _assumptions,
    DiagramElement: _diagram,
    ProseElement: _prose,
    VideoElement: _video,
    ViewElement: _view,
}


def _claim(claim: Claim) -> str:
    """Render a claim, quoting its source and naming a document's page."""
    backing = claim.backing
    if not isinstance(backing, LinesBacking) or backing.quote is None:
        return f"- {claim.text} {_cite(backing)}"
    cite = (
        f"[{PurePosixPath(backing.file).name} p.{claim.page}]"
        if claim.page is not None
        else _cite(backing)
    )
    return f'- {claim.text} "{backing.quote}" {cite}'


def _cite(backing: Backing) -> str:
    match backing:
        case LinesBacking(file=file, lines=(start, end)):
            return f"[{file}:{start}-{end}]"
        case SourceBacking(url=url, version=version):
            return f"[{url} @ {version}]"
        case SpikeBacking(spike_id=spike_id):
            return f"[spike {spike_id}]"
        case DataBacking(data=data):
            return f"[recorded data {data}]"
    return f"[unverified: {backing.reason}]"


def _thread(thread: QuestionThread) -> str:
    lines = [_quote(thread.selection), ">"] if thread.selection else []
    label = "Q (Socratic)" if thread.mode == "socratic" else "Q"
    lines.append(_quote(f"**{label}:** {thread.text}"))
    reply = thread.reply or "_No reply yet._"
    return "\n".join(lines) + "\n\n" + reply


def _final_question(number: int, question: Question, grade: Grade | None) -> list[str]:
    blocks = [f"### {number}. {question.prompt}"]
    if isinstance(question, FillBlank):
        blocks.append(_fence(question.text, question.language))
    elif isinstance(question, (MutationQuiz, FixTheBug)):
        blocks.extend(_mutation(question))
    elif isinstance(question, (SelectItems, FillTable)):
        if question.frame:
            blocks.append(f"**{question.frame.get('caption')}**")
    elif not isinstance(question, Parsons) and question.code:
        blocks.append(_code(question.code))
    if isinstance(question, ChangeImpact):
        blocks.append(f"**Change:** {question.change}")
    if grade is None:
        blocks.append("_Not answered._")
        return blocks
    blocks.append(f"**Your answer:** {_answer(question, grade.response)}")
    blocks.append(
        f"**Confidence:** {grade.confidence or 'not given'}  \n"
        f"**Outcome:** {grade.outcome.replace('_', ' ')} ({grade.score:.0%})"
    )
    if isinstance(question, Parsons):
        solution = _fence(_parsons_text(question.lines), "text")
        blocks.append(f"**Solution:**\n\n{solution}")
    if question.explanation:
        blocks.append(question.explanation)
    return blocks


def _answer(question: Question, response: Response) -> str:  # noqa: C901, PLR0911, PLR0912
    """Show `response` in the reader's terms: option and step text, not indexes."""
    match question, response:
        case (SingleChoice(options=options) | PredictOutput(options=options), int()):
            return _option(options, response)
        case MultipleChoice(options=options), list():
            return "; ".join(_option(options, item) for item in response)
        case PickLine(), list():
            return "line(s) " + ", ".join(str(item) for item in response)
        case OrderSteps(), list():
            return "".join(f"\n{at}. {step}" for at, step in enumerate(response, 1))
        case FillBlank(), dict():
            return "; ".join(f"`{key}` = `{value}`" for key, value in response.items())
        case OpenAnswer(), dict():
            return f"\n\n{_quote(str(response.get('text', '')))}"
        case MutationQuiz(), list():
            return "ticked " + (", ".join(f"`{name}`" for name in response) or "none")
        case FixTheBug(), dict():
            return "tests pass" if response.get("passed") else "tests still fail"
        case ChangeImpact(candidates=candidates), list():
            return "; ".join(_option(candidates, item) for item in response)
        case SelectItems(), list():
            return "picked " + (", ".join(f"`{cell}`" for cell in response) or "none")
        case FillTable(), dict():
            return "; ".join(
                f"`{cell}` {name} = `{value}`"
                for cell, row in response.items()
                if isinstance(row, dict)
                for name, value in row.items()
            )
        case Parsons(), list():
            placed = [
                ParsonsLine(str(item.get("text", "")), indent)
                for item in response
                if isinstance(item, dict)
                and isinstance(indent := item.get("indent"), int)
            ]
            return "\n\n" + _fence(_parsons_text(placed), "text")
        case _, str():
            return _inline(response)
    return str(response)


def _mutation(question: MutationQuiz | FixTheBug) -> list[str]:
    """Render the planted change and, for a quiz, how each test fared on it."""
    mutation = question.mutation
    where = f"`{mutation.file}` line {mutation.line}"
    if isinstance(question, FixTheBug):
        return [
            f"Bug planted at {where}. The original line:\n\n"
            + _fence(mutation.replacement or "", "text"),
            f"Tests: `{shlex.join(question.test_command)}`",
        ]
    change = (
        "deleted"
        if mutation.replacement is None
        else "changed to:\n\n" + _fence(mutation.replacement, "text")
    )
    results = "\n".join(
        f"- `{test.name}`: {'fails' if test.fails else 'passes'}"
        for test in question.tests
    )
    return [f"Mutation: {where} {change}", results, _fence(question.log, "text")]


def _parsons_text(lines: Sequence[ParsonsLine]) -> str:
    """Lay out Parsons lines as code, four spaces per indent level."""
    return "\n".join("    " * line.indent + line.text for line in lines)


def _option(options: Sequence[Option], index: object) -> str:
    if isinstance(index, int) and 0 <= index < len(options):
        return options[index].text
    return str(index)


def _code(code: CodeBlock) -> str:
    fenced = _fence(code.text, code.language)
    if code.file is None:
        return fenced
    last = code.start_line + len(code.text.splitlines()) - 1
    return f"`{code.file}` lines {code.start_line}-{last}\n\n{fenced}"


def _fence(text: str, language: str) -> str:
    """Fence `text` with more backticks than any run inside it, so none closes it."""
    longest = max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _inline(text: str) -> str:
    return "\n" + _fence(text, "text") if "\n" in text else f"`{text}`"


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def render_anki(
    lesson: Lesson, results: Mapping[str, Any], repo: str, commit: str
) -> str:
    """Render the misses in a run's `results.json` as an Anki import file.

    A missed or confidently wrong final question gives one recall card, an open
    answer one card per rubric item the reader did not meet. GUIDs depend only
    on the lesson title, question id and rubric index, so a re-import updates.
    """
    title = " ".join(lesson.title.split())
    header = [
        "#separator:tab",
        "#html:true",
        "#notetype:Basic",
        f"#deck:grokcheck::{title}",
        "#guid column:1",
        "#tags column:4",
        "#columns:guid\tfront\tback\ttags",
    ]
    questions = {question.id: question for question in lesson.final}
    tags = f"grokcheck {results['lesson_id']}"
    rows = []
    for item in results["final"]:
        question = questions.get(item["question_id"])
        if question is None:
            continue
        source = html.escape(_anki_source(question, repo, commit))
        for index, front, back in _anki_cards(question, item):
            key = f"{lesson.title}\x1f{question.id}\x1f{index}"
            guid = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16]
            rows.append((guid, front, f"{back}<br>{source}", tags))
    out = io.StringIO()
    out.write("\n".join(header) + "\n")
    csv.writer(out, delimiter="\t", lineterminator="\n").writerows(rows)
    return out.getvalue()


def _anki_cards(
    question: Question, item: Mapping[str, Any]
) -> list[tuple[str, str, str]]:
    """Return `(rubric index, front, back)` per card, the index empty off rubrics."""
    if isinstance(question, OpenAnswer):
        response = item.get("response")
        met = response.get("met", []) if isinstance(response, dict) else []
        return [
            (
                str(index),
                _anki_front(question)
                + f"<br><br>Recall point {index + 1} of {len(question.rubric)}"
                " of a full answer.",
                _anki_html(f"{point}\n\n{question.model_answer}"),
            )
            for index, (point, ok) in enumerate(zip(question.rubric, met, strict=False))
            if not ok
        ]
    if not missed(item["outcome"], item.get("confidence")):
        return []
    back = _correct_options(question)
    if back:
        front = _anki_front(question) + "<br><br>What is the answer, and why?"
    else:
        front, back = _anki_front(question), _anki_key(question)
    if question.explanation:
        back = f"{back}\n\n{question.explanation}"
    return [("", front, _anki_html(back))]


def _correct_options(question: Question) -> str:
    """Return each correct option with why it is right, empty off choice questions."""
    options = getattr(question, "options", ())
    correct = getattr(question, "correct", None)
    if not options or correct is None:
        return ""
    chosen = (correct,) if isinstance(correct, int) else correct
    return "\n".join(f"{options[at].text}: {options[at].why}" for at in chosen)


def _anki_front(question: Question) -> str:
    front = _anki_html(question.prompt)
    if isinstance(question, FillBlank):
        return f"{front}<br>{_anki_pre(question.text)}"
    if isinstance(question, (MutationQuiz, FixTheBug)):
        mutation = question.mutation
        change = "deleted" if mutation.replacement is None else mutation.replacement
        where = f"{mutation.file} line {mutation.line}"
        return f"{front}<br>{_anki_html(where)}<br>{_anki_pre(change)}"
    code = getattr(question, "code", None)
    return f"{front}<br>{_anki_pre(code.text)}" if code else front


def _anki_key(question: Question) -> str:  # noqa: C901, PLR0911
    """Return the answer to a question that has no options, as plain text."""
    match question:
        case PredictOutput(accepted=accepted) if accepted:
            return accepted[0]
        case PickLine(answer_lines=lines):
            return "Line(s) " + ", ".join(str(line) for line in lines)
        case OrderSteps(steps=steps):
            return "\n".join(f"{at}. {step}" for at, step in enumerate(steps, 1))
        case FillBlank(blanks=blanks):
            return "\n".join(f"{blank.id} = {blank.accepted[0]}" for blank in blanks)
        case MutationQuiz(tests=tests):
            failing = [test.name for test in tests if test.fails]
            return "Failing tests: " + (", ".join(failing) or "none")
        case FixTheBug(mutation=mutation):
            return f"The original line: {mutation.replacement or ''}"
        case ChangeImpact(candidates=candidates, affected=affected):
            return "\n".join(
                f"{candidates[at].text}: {candidates[at].why}" for at in affected
            )
        case Parsons(lines=lines):
            return _parsons_text(lines)
        case SelectItems(answer_cells=cells):
            return "Cells: " + ", ".join(cells)
        case FillTable(cells=cells):
            return "\n".join(
                f"{cell} {name} = {value}"
                for cell, row in cells.items()
                for name, value in row.items()
            )
    return ""


def _anki_source(question: Question, repo: str, commit: str) -> str:
    if isinstance(question, (MutationQuiz, FixTheBug)):
        mutation = question.mutation
        return f"{repo} {mutation.file}:{mutation.line} @ {commit}"
    code = getattr(question, "code", None)
    if code is None or code.file is None:
        return f"{repo} @ {commit}"
    last = code.start_line + max(len(code.text.splitlines()), 1) - 1
    return f"{repo} {code.file}:{code.start_line}-{last} @ {commit}"


def _anki_html(text: str) -> str:
    return "<br>".join(html.escape(text).splitlines())


def _anki_pre(text: str) -> str:
    return f"<pre>{_anki_html(text)}</pre>"


OBSIDIAN_TEMPLATE = """---
repo: {repo}
commit: {commit}
files: {files}
date: {date}
score: {score}
tags: [grokcheck, flashcards]
---

# {title}

## Mental model

{mental_model}

## Misses

{misses}

## Cards

#flashcards/grokcheck

{cards}
"""


def render_obsidian(
    lesson: Lesson,
    results: Mapping[str, Any],
    repo: str,
    commit: str,
    template: str | None = None,
) -> str:
    """Render a run's `results.json` as an Obsidian note with inline flashcards.

    `template` is a `str.format` string over the fields `OBSIDIAN_TEMPLATE`
    uses. Each miss gives one `Question::Answer` card line; every other `::`
    outside code gets a zero-width space, as the Spaced Repetition plugin reads
    any line holding one as a card and splits it at the first.
    """
    questions = {question.id: question for question in lesson.final}
    misses = [
        questions[item["question_id"]]
        for item in results["final"]
        if item["question_id"] in questions
        and missed(item["outcome"], item.get("confidence"))
    ]
    first_prose = (
        next((e.markdown for e in s.elements if isinstance(e, ProseElement)), None)
        for s in lesson.sections
    )
    return (template or OBSIDIAN_TEMPLATE).format(
        repo=json.dumps(repo),
        commit=json.dumps(commit),
        files=json.dumps(list(lesson.scope.files)),
        date=local_today().isoformat(),
        score=round(results["summary"]["final_score"], 2),
        title=_no_card(lesson.title),
        mental_model=_no_card("\n\n".join(prose for prose in first_prose if prose)),
        misses=_no_card(
            "\n\n".join(
                f"### {_one_line(q.prompt)}\n\n{q.explanation or _obsidian_answer(q)}"
                for q in misses
            )
            or "None."
        ),
        cards="\n".join(
            _no_card_line(_one_line(q.prompt))
            + "::"
            + _no_card_line(_one_line(_obsidian_answer(q)))
            for q in misses
        ),
    )


def _obsidian_answer(question: Question) -> str:
    if isinstance(question, OpenAnswer):
        return question.model_answer
    return _correct_options(question) or _anki_key(question)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _no_card(text: str) -> str:
    return _CODE_OR_COLON_PAIR.sub(lambda m: m["code"] or ":\u200b", text)


def _no_card_line(text: str) -> str:
    return _COLON_PAIR.sub(":\u200b", text)
