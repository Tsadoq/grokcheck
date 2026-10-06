"""The item-writing-flaw lint the agent sees as warnings from `validate`."""

import dataclasses
import json
from pathlib import Path

import pytest
from grokcheck.lesson import (
    DiagramElement,
    Element,
    Lesson,
    Plan,
    Section,
    Subject,
    Term,
    VocabElement,
    load_lesson,
)
from grokcheck.lint import intro, item_flaws, media, prose

VALID_FULL = Path(__file__).parent.parent / "fixtures" / "lessons" / "valid_full.json"
PROJECT = Path(__file__).parent.parent / "fixtures" / "project"

_CLEAN_FINAL = {
    "id": "final-zero",
    "type": "single_choice",
    "prompt": "What happens when `LruCache(0)` is constructed?",
    "options": [
        {"text": "It raises ValueError.", "why": "Capacities below 1 are refused."},
        {"text": "It returns an empty cache.", "why": "Zero is refused."},
        {"text": "It returns a cache of size one.", "why": "Zero is kept as is."},
    ],
    "correct": 0,
}
_FLAWED_FINAL = {
    "id": "final-zero",
    "type": "single_choice",
    "prompt": "What happens when `LruCache(0)` is constructed?",
    "options": [
        {"text": "It returns an empty cache.", "why": "Zero is refused."},
        {"text": "It always grows on demand.", "why": "Zero is not unlimited."},
        {
            "text": "It raises ValueError before any state is built.",
            "why": "Capacities below 1 are refused.",
        },
    ],
    "correct": 2,
}


def _lesson(tmp_path: Path, final_zero: dict[str, object]) -> Lesson:
    """Load a lesson whose other questions are flawless, around `final_zero`."""
    raw = {
        "schema_version": 2,
        "title": "How LruCache evicts",
        "scope": {"summary": "The cache.", "files": ["src/cache.py"]},
        "sections": [
            {
                "id": "purpose",
                "title": "What the cache is for",
                "body": "It keeps at most `capacity` items.",
                "checkpoints": [
                    {
                        "id": "cp-size",
                        "type": "single_choice",
                        "prompt": "How many items can `LruCache(2)` hold?",
                        "options": [
                            {"text": "One item.", "why": "Capacity is two."},
                            {"text": "Two items.", "why": "That is the capacity."},
                            {"text": "Three items.", "why": "Capacity is two."},
                        ],
                        "correct": 1,
                    }
                ],
            }
        ],
        "final": [
            final_zero,
            {
                "id": "final-evict",
                "type": "single_choice",
                "prompt": "Which item does a full cache evict?",
                "options": [
                    {"text": "The newest item.", "why": "It was just used."},
                    {"text": "A random item.", "why": "Order is kept."},
                    {"text": "The oldest item.", "why": "Least recently used."},
                ],
                "correct": 2,
            },
            {
                "id": "final-order",
                "type": "order_steps",
                "prompt": "Order what `put` does.",
                "steps": ["Store the value", "Move the key to the end"],
            },
        ],
    }
    path = tmp_path / "lesson.json"
    path.write_text(json.dumps(raw))
    return load_lesson(path, tmp_path)


def test_item_flaws_flag_longest_correct_and_absolute_distractor(
    tmp_path: Path,
) -> None:
    """A long correct option and an "always" distractor each give one warning.

    A test-wise reader picks the longest option and rules out absolutes without
    knowing the code, so either flaw lets a guess pass for understanding.
    """
    clean = _lesson(tmp_path, _CLEAN_FINAL)
    flawed = _lesson(tmp_path, _FLAWED_FINAL)

    warnings = item_flaws(flawed)

    rules = sorted(w.rule for w in warnings)
    if rules != ["absolute_distractor", "longest_correct"]:
        pytest.fail(f"flawed question warns {warnings}")
    if not warnings[0].path.startswith("final[0]"):
        pytest.fail(f"warning at {warnings[0].path}, not the flawed question")
    if clean_warnings := item_flaws(clean):
        pytest.fail(f"clean lesson warns {clean_warnings}")


def test_prose_lint_flags_a_sentence_over_25_words(tmp_path: Path) -> None:
    """A 30-word sentence in a body gives one `long_sentence` warning there.

    Long sentences are where a skimming reader loses the thread, so the lint
    points at the one to split and leaves short ones alone.
    """
    base = _lesson(tmp_path, _CLEAN_FINAL)
    long_body = (
        "The cache keeps every item in an ordered dict so that a read can move "
        "the key to the end and a full cache can drop the first key fast."
    )
    section = dataclasses.replace(base.sections[0], body=long_body)
    lesson = dataclasses.replace(base, sections=(section,))

    warnings = prose(lesson)

    if [(w.rule, w.path) for w in warnings] != [("long_sentence", "sections[0].body")]:
        pytest.fail(f"long body warns {warnings}")
    if "The cache keeps every item in" not in warnings[0].message:
        pytest.fail(f"message does not quote the sentence: {warnings[0].message}")
    if fixture_warnings := prose(load_lesson(VALID_FULL, PROJECT)):
        pytest.fail(f"fixture lesson warns {fixture_warnings}")


def test_prose_lint_flags_banned_words_case_insensitively(tmp_path: Path) -> None:
    """A banned word in a prompt and a banned phrase in an explanation each warn."""
    final = {
        **_CLEAN_FINAL,
        "prompt": "Leverage what you know: what does `LruCache(0)` do?",
        "explanation": "It is worth noting that zero is refused.",
    }
    lesson = _lesson(tmp_path, final)

    warnings = prose(lesson)

    found = sorted((w.rule, w.path, w.message) for w in warnings)
    expected = [
        ("banned_word", "final[0].explanation", "banned phrase 'it is worth noting'"),
        ("banned_word", "final[0].prompt", "banned word 'leverage'"),
    ]
    if found != expected:
        pytest.fail(f"banned words warn {found}")


def _intro_lesson(tmp_path: Path, text: str) -> Lesson:
    base = _lesson(tmp_path, _CLEAN_FINAL)
    second = Section(
        id="evict",
        title="Eviction order",
        body="A full cache drops the oldest key.",
        elements=(),
        checkpoints=base.sections[0].checkpoints,
    )
    first = dataclasses.replace(base.sections[0], title="Capacity")
    return dataclasses.replace(base, sections=(first, second), intro=text)


def test_intro_lint_requires_a_closing_action(tmp_path: Path) -> None:
    """An intro ending on a statement warns; ending on an action does not.

    The intro is the video's script, and a closing action tells the viewer
    what to watch for in the sections that follow.
    """
    opening = (
        "A cache of 2 items just dropped the key you read a second ago. "
        "That read should have kept it. "
        "We cover Capacity and then Eviction order. "
    )
    lesson_declarative = _intro_lesson(
        tmp_path, opening + "The cache stops at the await."
    )
    lesson_action = _intro_lesson(
        tmp_path, opening + "So, cancel at the await, not after it."
    )

    if (rules := [w.rule for w in intro(lesson_declarative)]) != [
        "intro_last_not_action"
    ]:
        pytest.fail(f"declarative intro warns {rules}")
    if action_warnings := intro(lesson_action):
        pytest.fail(f"intro ending in an action warns {action_warnings}")


_DIAGRAM = DiagramElement(mermaid="flowchart TD\n  a -->|to| b", caption="Flow.")
_VOCAB = VocabElement(
    code=None,
    terms=(Term(term="capacity", owner="ours", definition="Most items kept."),),
    min_opened=1,
)


def _plan(
    subject: Subject,
    content: tuple[str, ...],
    rejected: tuple[str, ...],
    rationale: str = "Fifteen minutes fits one section.",
) -> Plan:
    return Plan(
        subject=subject,
        time_budget=15,
        content=content,
        media=(),
        rejected=rejected,
        rationale=(rationale,),
        default_depth="short",
    )


def _with(lesson: Lesson, *elements: Element, plan: Plan | None = None) -> Lesson:
    section = dataclasses.replace(lesson.sections[0], elements=elements)
    return dataclasses.replace(lesson, sections=(section,), plan=plan)


def _rules(lesson: Lesson) -> list[tuple[str, str]]:
    return [(w.rule, w.path) for w in media(lesson)]


def test_media_lint_flags_a_section_showing_only_prose_and_code(
    tmp_path: Path,
) -> None:
    """A section with only prose in its short view warns; a diagram there does not.

    A diagram hidden in detail does not count, since the short view never shows it.
    """
    base = _lesson(tmp_path, _CLEAN_FINAL)
    hidden = dataclasses.replace(_DIAGRAM, depth="detail")

    if ("prose_only_section", "sections[0]") not in _rules(_with(base, hidden)):
        pytest.fail(f"prose-only section warns {media(_with(base, hidden))}")
    if shown := media(_with(base, _DIAGRAM)):
        pytest.fail(f"section with a diagram warns {shown}")


def test_media_lint_flags_a_diagram_in_detail(tmp_path: Path) -> None:
    """A detail diagram warns at its authored path, so it moves next to its text."""
    base = _lesson(tmp_path, _CLEAN_FINAL)
    hidden = dataclasses.replace(_DIAGRAM, depth="detail")

    rules = _rules(_with(base, _DIAGRAM, hidden))

    if rules != [("diagram_in_detail", "sections[0].elements[1]")]:
        pytest.fail(f"detail diagram warns {rules}")


def test_media_lint_flags_trace_rejected_for_behaviour(tmp_path: Path) -> None:
    """Rejecting `trace` when the plan covers behaviour warns; a document is exempt.

    Recording runs in a parallel subagent, so time is never a reason to skip it.
    A document lesson may not hold a trace at all.
    """
    base = _lesson(tmp_path, _CLEAN_FINAL)
    rejected = _with(base, _DIAGRAM, plan=_plan("concept", ("behaviour",), ("trace",)))
    document = _with(base, _DIAGRAM, plan=_plan("document", ("behaviour",), ("trace",)))

    if _rules(rejected) != [("trace_rejected", "plan.rejected")]:
        pytest.fail(f"rejected trace warns {media(rejected)}")
    if "record one" not in media(rejected)[0].message:
        pytest.fail(f"message does not say to record: {media(rejected)[0].message}")
    if shown := media(document):
        pytest.fail(f"document lesson warns {shown}")


@pytest.mark.parametrize(
    ("rationale", "warns"),
    [
        ("No trace: the handler needs a live database to run.", False),
        ("A trace would not fit the time budget.", True),
        ("A trace would take too long to record.", True),
    ],
)
def test_media_lint_accepts_a_trace_rejection_only_with_a_reason_besides_time(
    tmp_path: Path, rationale: str, *, warns: bool
) -> None:
    """A rationale naming why the code cannot run clears the warning; time does not."""
    base = _lesson(tmp_path, _CLEAN_FINAL)
    plan = _plan("concept", ("behaviour",), ("trace",), rationale)

    rules = [rule for rule, _ in _rules(_with(base, _DIAGRAM, plan=plan))]

    if (rules == ["trace_rejected"]) != warns:
        pytest.fail(f"rationale {rationale!r} warns {rules}")


def test_media_lint_flags_an_area_lesson_without_vocab(tmp_path: Path) -> None:
    """An area lesson needs a vocab element; a concept lesson does not."""
    base = _lesson(tmp_path, _CLEAN_FINAL)
    area = _with(base, _DIAGRAM, plan=_plan("area", ("structure",), ()))

    if _rules(area) != [("no_vocab", "$")]:
        pytest.fail(f"area lesson without vocab warns {media(area)}")
    if shown := media(_with(base, _VOCAB, plan=_plan("area", ("structure",), ()))):
        pytest.fail(f"area lesson with vocab warns {shown}")
    if shown := media(_with(base, _DIAGRAM, plan=_plan("concept", ("structure",), ()))):
        pytest.fail(f"concept lesson warns {shown}")
