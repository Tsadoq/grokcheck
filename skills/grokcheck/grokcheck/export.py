"""Export: a finished lesson rendered as one markdown file for rereading later.

The file stands alone: it holds the explanation, the reader's questions with the
agent's replies under the section they were asked in, and the final quiz with
the reader's answers, how they were graded, and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grokcheck.lesson import (
    FillBlank,
    MultipleChoice,
    OpenAnswer,
    OrderSteps,
    PickLine,
    PredictOutput,
    SingleChoice,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from grokcheck.grading import Grade, Response
    from grokcheck.lesson import CodeBlock, Lesson, Option, Question, Section

_BACKTICK_RUN = re.compile(r"`+")


@dataclass(frozen=True)
class QuestionThread:
    """A question the reader asked in a section, and the agent's reply to it.

    `selection` is the text the reader highlighted when asking, empty when they
    used the section's Ask box; `reply` is empty until the agent answers.
    """

    id: str
    section_id: str
    selection: str
    text: str
    reply: str = ""


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
    if section.code:
        blocks.append(_code(section.code))
    if threads:
        blocks.append("### Your questions")
        blocks.extend(_thread(thread) for thread in threads)
    return blocks


def _thread(thread: QuestionThread) -> str:
    lines = [_quote(thread.selection), ">"] if thread.selection else []
    lines.append(_quote(f"**Q:** {thread.text}"))
    reply = thread.reply or "_No reply yet._"
    return "\n".join(lines) + "\n\n" + reply


def _final_question(number: int, question: Question, grade: Grade | None) -> list[str]:
    blocks = [f"### {number}. {question.prompt}"]
    if isinstance(question, FillBlank):
        blocks.append(_fence(question.text, question.language))
    elif question.code:
        blocks.append(_code(question.code))
    if grade is None:
        blocks.append("_Not answered._")
        return blocks
    blocks.append(f"**Your answer:** {_answer(question, grade.response)}")
    blocks.append(
        f"**Confidence:** {grade.confidence or 'not given'}  \n"
        f"**Outcome:** {grade.outcome.replace('_', ' ')} ({grade.score:.0%})"
    )
    if question.explanation:
        blocks.append(question.explanation)
    return blocks


def _answer(question: Question, response: Response) -> str:  # noqa: PLR0911
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
        case _, str():
            return _inline(response)
    return str(response)


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
