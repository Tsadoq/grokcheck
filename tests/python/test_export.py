"""The exported markdown as a reader rereading a finished lesson sees it."""

import csv
import dataclasses
from pathlib import Path

import pytest
from grokcheck.cli import CliError, write_note
from grokcheck.export import (
    QuestionThread,
    render_anki,
    render_markdown,
    render_obsidian,
)
from grokcheck.grading import Confidence, Response, grade
from grokcheck.lesson import (
    CodeBlock,
    OrderSteps,
    SpikeElement,
    TraceElement,
    TraceStep,
    TraceVersion,
    VideoElement,
    load_lesson,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _heading_line(lines: list[str], title: str) -> int:
    return next(
        index
        for index, line in enumerate(lines)
        if line.startswith("#") and title in line
    )


def test_export_places_each_reply_under_its_section() -> None:
    """A reader question and its reply appear inside the section they were asked in.

    Rereading the export later only helps if each answer sits next to the
    explanation that prompted the question.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    purpose, following = lesson.sections[0], lesson.sections[1]
    reply = "The cache exists so repeated lookups skip the slow backing store."
    thread = QuestionThread(
        id="q1",
        section_id=purpose.id,
        selection="",
        text="Why not just call the store every time?",
        reply=reply,
    )

    lines = render_markdown(lesson, {}, [thread]).splitlines()

    start = _heading_line(lines, purpose.title)
    end = _heading_line(lines, following.title)
    inside = [index for index in range(start, end) if reply in lines[index]]
    if not inside:
        context = "\n".join(lines[max(start - 2, 0) : end + 3])
        pytest.fail(
            f"reply not found between the '{purpose.title}' heading (line {start})"
            f" and the '{following.title}' heading (line {end}):\n{context}"
        )


def test_export_labels_socratic_threads() -> None:
    """A thread the reader asked in Socratic mode is labelled as such.

    Its reply is a guiding question, not an answer, and reads wrong unlabelled.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    section_id = lesson.sections[0].id
    socratic = QuestionThread(
        id="q1", section_id=section_id, selection="", text="Why?", mode="socratic"
    )
    plain = QuestionThread(id="q2", section_id=section_id, selection="", text="How?")

    markdown = render_markdown(lesson, {}, [socratic, plain])

    if "**Q (Socratic):** Why?" not in markdown or "**Q:** How?" not in markdown:
        pytest.fail(f"expected one Socratic and one plain thread label in:\n{markdown}")


def test_export_renders_every_element_type() -> None:
    """Each element type leaves a line in the export that identifies it.

    An element the export drops is content the reader cannot reread later.
    """
    loaded = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    code = CodeBlock("python", "total = 1 + 1")
    extra = (
        TraceElement(
            trace_id="sum",
            title="Adding one and one",
            versions=(
                TraceVersion("before", code, (TraceStep(1, {}, ("Adds the two.",)),)),
            ),
            panels=(),
        ),
        SpikeElement(
            spike_id="sum",
            hypothesis="Adding one and one gives two.",
            code=code,
            pins={},
            exclude_newer="2026-01-01",
            result={"stdout": "2\n"},
            gate="gate-sum",
        ),
        VideoElement(
            id="tour",
            src="/videos/tour.mp4",
            captions="/videos/tour.vtt",
            duration=41.6,
            transcript="The cache drops its oldest key.",
        ),
    )
    last = loaded.sections[-1]
    lesson = dataclasses.replace(
        loaded,
        sections=(
            *loaded.sections[:-1],
            dataclasses.replace(last, elements=(*last.elements, *extra)),
        ),
    )
    expected = {
        "diff": "@@ -17 +17 @@",
        "vocab": "| `move_to_end` | stdlib | Relinks a key as the newest."
        " | touch | yes |",
        "trace": "1. Line 1: Adds the two.",
        "spike": "**Spike `sum`:** Adding one and one gives two.",
        "diagram": "```mermaid",
        "playground": "Inputs, in order: Capacity, Reads of `a` before `c` is added.",
        "options": "| move_to_end on get | build | One relink per hit."
        " | Hits dominate misses."
        " | Stay under 50 lines. (waivable by the maintainer) |",
        "assumptions": "- Hits dominate misses. [unverified: No traffic data yet.]",
        "video": "[Video, 42 s](<file:///videos/tour.mp4>)",
    }

    lines = render_markdown(lesson, {}, []).splitlines()

    missing = [
        f"{kind}: no line {line!r}"
        for kind, line in expected.items()
        if line not in lines
    ]
    if missing:
        pytest.fail("element types missing from the export:\n" + "\n".join(missing))


def test_anki_export_has_headers_stable_guids_and_one_card_per_miss() -> None:
    """Two misses and one unmet rubric item give three cards with stable GUIDs.

    Re-importing an export must update the reader's cards, not duplicate them.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    steps = next(q.steps for q in lesson.final if isinstance(q, OrderSteps))
    responses: dict[str, tuple[Response, Confidence | None]] = {
        "final-predict": ("None", "sure"),
        "final-predict-choice": (1, "sure"),
        "final-order": (list(reversed(steps)), "unsure"),
        "final-why": ({"text": "reads move keys", "met": [True, False, True]}, None),
    }
    final = [
        {"prompt": question.prompt, **dataclasses.asdict(grade(question, *answer))}
        for question in lesson.final
        if (answer := responses.get(question.id))
    ]
    results = {"lesson_id": "20260101-000000-abcdef", "final": final}

    text = render_anki(lesson, results, "grokcheck", "6863881")
    lines = text.splitlines()
    rows = list(
        csv.reader([ln for ln in lines if not ln.startswith("#")], delimiter="\t")
    )
    expected_cards, fields = 3, 4

    problems = []
    if not all(line.startswith("#") for line in lines[:6]):
        problems.append(f"the first six lines are not all headers: {lines[:6]}")
    if "#guid column:1" not in lines:
        problems.append("no '#guid column:1' header")
    if len(rows) != expected_cards:
        problems.append(f"expected 3 cards, got {len(rows)}: {rows}")
    problems.extend(
        f"row is not 4 fields without newlines: {row}"
        for row in rows
        if len(row) != fields or any("\n" in field for field in row)
    )
    if text != render_anki(lesson, results, "grokcheck", "6863881"):
        problems.append("a second export differs from the first")
    if problems:
        pytest.fail("\n".join(problems))


def test_obsidian_export_writes_new_note_and_never_overwrites(tmp_path: Path) -> None:
    """Two misses give two inline cards, and a second export refuses to overwrite.

    The vault is the reader's own notes: an export adds a note, never edits one.
    """
    loaded = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    lesson = dataclasses.replace(
        loaded,
        final=tuple(
            dataclasses.replace(
                q,
                prompt="Order what std::vector does here.",
                explanation="```cpp\nstd::vector<int> v;\n```\n\nSee `std::vector`.",
            )
            if isinstance(q, OrderSteps)
            else q
            for q in loaded.final
        ),
    )
    steps = next(q.steps for q in lesson.final if isinstance(q, OrderSteps))
    responses: dict[str, tuple[Response, Confidence | None]] = {
        "final-predict": ("None", "sure"),
        "final-predict-choice": (1, "sure"),
        "final-order": (list(reversed(steps)), "unsure"),
    }
    final = [
        {"prompt": question.prompt, **dataclasses.asdict(grade(question, *answer))}
        for question in lesson.final
        if (answer := responses.get(question.id))
    ]
    results = {
        "lesson_id": "20260101-000000-abcdef",
        "summary": {"final_score": 0.33},
        "final": final,
    }

    text = render_obsidian(lesson, results, "grokcheck", "6863881", None)
    path = write_note(tmp_path, lesson.title, text)

    problems = []
    if not path.read_text(encoding="utf-8").startswith("---\nrepo:"):
        problems.append(f"the note does not open with frontmatter:\n{text[:200]}")
    if "#flashcards/grokcheck" not in text:
        problems.append("no '#flashcards/grokcheck' tag")
    cards = text.split("#flashcards/grokcheck", 1)[1]
    if cards.count("::") != 2:  # noqa: PLR2004
        problems.append(f"expected 2 '::' cards, got {cards.count('::')}:\n{text}")
    if "<!--SR:" in text:
        problems.append("the note carries a scheduling comment")
    if "\nOrder what std:\u200b:vector does here.::" not in text:
        problems.append("the '::' in 'std::vector' is not broken by a zero-width space")
    if "```cpp\nstd::vector<int> v;\n```" not in text or "`std::vector`" not in text:
        problems.append(f"the '::' inside code was changed:\n{text}")
    if problems:
        pytest.fail("\n".join(problems))
    with pytest.raises(CliError, match="already exists"):
        write_note(tmp_path, lesson.title, text)
