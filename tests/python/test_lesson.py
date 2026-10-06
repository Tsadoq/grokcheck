"""The lesson validator as the agent that wrote the lesson experiences it."""

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from grokcheck import run
from grokcheck.lesson import (
    CodeElement,
    LessonError,
    Parsons,
    PlaygroundElement,
    Problem,
    SpikeElement,
    load_lesson,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _matches(problem: Problem, path: str, fragment: str) -> bool:
    return problem.path == path and fragment in problem.message


def test_load_lesson_reports_every_problem_in_one_pass() -> None:
    """Every defect in a lesson is reported at once, each at its JSON path.

    The agent fixes a rejected lesson in one edit only if the first failure
    already names all of them; stopping at the first would cost a round trip
    per defect.
    """
    expected = [
        ("sections[0].code.file", "outside the project root"),
        ("sections[1].checkpoints[0].id", "duplicate question id"),
        ("final[1].text", "[[blank:extra]]"),
        ("final[2].correct[1]", "out of range"),
    ]

    with pytest.raises(LessonError) as caught:
        load_lesson(
            FIXTURES / "lessons" / "invalid_many_errors.json",
            FIXTURES / "project",
        )

    problems = caught.value.problems
    missing = [
        pair
        for pair in expected
        if not any(_matches(problem, *pair) for problem in problems)
    ]
    unexpected = [
        problem
        for problem in problems
        if not any(_matches(problem, *pair) for pair in expected)
    ]
    if missing or unexpected:
        pytest.fail(f"missing: {missing}\nunexpected: {unexpected}")


def test_public_view_shows_the_open_answer_rubric_but_not_the_model_answer() -> None:
    """The reader self-rates an open answer against its rubric, so the browser needs it.

    The model answer stays on the server until the answer is graded.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")

    public = lesson.public_view()
    sections = cast("list[dict[str, Any]]", public["sections"])
    final = cast("list[dict[str, Any]]", public["final"])
    questions = [
        question for section in sections for question in section["checkpoints"]
    ] + final
    open_answers = [q for q in questions if q["type"] == "open_answer"]

    if not open_answers:
        pytest.fail("fixture has no open_answer question")
    expected_rubric = [
        "Says a read counts as a use",
        "Links the end of the dict to the most recently used position",
        "Links the front of the dict to eviction",
    ]
    for question in open_answers:
        if question.get("rubric") != expected_rubric:
            pytest.fail(f"{question['id']} rubric is {question.get('rubric')!r}")
        if "model_answer" in question:
            pytest.fail(f"{question['id']} leaks its model_answer")


def _choice(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "type": "single_choice",
        "prompt": "Which?",
        "options": [{"text": "a", "why": "first"}, {"text": "b", "why": "second"}],
        "correct": 0,
    }


def test_section_elements_round_trip_and_code_sugar(tmp_path: Path) -> None:
    """Sections written with `elements` or the `code` sugar load and persist alike.

    A running lesson is snapshotted and reloaded from disk, so every element must
    come back from the snapshot exactly as it was loaded.
    """
    (tmp_path / "mod.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    cited = {"file": "mod.py", "lines": [1, 2]}
    (tmp_path / ".grokcheck" / "traces").mkdir(parents=True)
    recording = {
        "events": [
            {"seq": seq, "event": "line", "file": "mod.py", "line": seq + 1}
            for seq in range(2)
        ],
        "truncated": False,
    }
    (tmp_path / ".grokcheck" / "traces" / "run.json").write_text(
        json.dumps(recording), encoding="utf-8"
    )
    raw = {
        "schema_version": 2,
        "title": "Elements",
        "intro": "Two names, one module.",
        "scope": {"summary": "One module.", "files": ["mod.py"]},
        "sections": [
            {
                "id": "listed",
                "title": "Listed",
                "body": "Lead prose.",
                "elements": [
                    {
                        "type": "prose",
                        "markdown": "More.",
                        "depth": "detail",
                        "claims": [
                            {"text": "Two names.", "backing": {**cited, "symbol": "b"}},
                            {"text": "Cheap.", "backing": {"reason": "not measured"}},
                        ],
                    },
                    {"type": "code", "code": cited, "caption": "The two names."},
                    {
                        "type": "diff",
                        "file": "mod.py",
                        "old_start": 1,
                        "new_start": 1,
                        "lines": [
                            {"op": " ", "text": "a = 1"},
                            {"op": "+", "text": "b = 2"},
                        ],
                        "notes": [{"lines": [2, 2], "cite": cited, "text": "New."}],
                        "asks": [{"line": 2, "question": "Why?", "answer": "Two."}],
                    },
                    {
                        "type": "trace",
                        "trace_id": "run",
                        "title": "Binding names",
                        "gate": "gate-1",
                        "panels": [{"id": "names", "label": "Names", "kind": "chips"}],
                        "versions": [
                            {
                                "label": "only",
                                "code": cited,
                                "steps": [
                                    {"narration": ["a binds."], "state": {"names": 1}},
                                    {"narration": ["b binds."], "state": {}, "at": 1},
                                ],
                            }
                        ],
                    },
                ],
                "checkpoints": [
                    _choice("cp-1"),
                    {
                        **_choice("gate-1"),
                        "type": "predict_state",
                        "trace_id": "run",
                        "version": 0,
                        "step": 1,
                    },
                ],
            },
            {
                "id": "sugar",
                "title": "Sugar",
                "body": "Lead prose.",
                "code": cited,
                "checkpoints": [_choice("cp-2")],
            },
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    lesson = load_lesson(lesson_file, tmp_path)
    reloaded = run._load_lesson(json.loads(json.dumps(run._dump_lesson(lesson))))  # noqa: SLF001

    listed, sugar = lesson.sections
    kinds = [type(element).__name__ for element in listed.elements]
    if kinds != ["ProseElement", "CodeElement", "DiffElement", "TraceElement"]:
        pytest.fail(f"section {listed.id}: element types are {kinds}")
    sugar_kinds = [type(element).__name__ for element in sugar.elements]
    if sugar_kinds != ["CodeElement"]:
        pytest.fail(f"section {sugar.id}: element types are {sugar_kinds}")
    leading = sugar.elements[0]
    if not isinstance(leading, CodeElement) or leading.code.file != "mod.py":
        pytest.fail(f"section {sugar.id} element 0: {leading!r} does not cite mod.py")
    for before, after in zip(lesson.sections, reloaded.sections, strict=True):
        for index, (old, new) in enumerate(
            zip(before.elements, after.elements, strict=False)
        ):
            if old != new:
                pytest.fail(f"section {before.id} element {index}: {old!r} != {new!r}")
    if reloaded != lesson:
        pytest.fail(f"reloaded lesson differs:\n{reloaded!r}\n!=\n{lesson!r}")


def test_plan_media_must_be_used_and_probe_must_be_closed(tmp_path: Path) -> None:
    """A plan may not promise a medium the lesson lacks, and a probe must self-grade.

    The probe sets the reader's depth before any section opens, so it cannot wait
    for a self-rated open answer.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    raw["plan"]["media"][0] = "trace"
    raw["probe"][0] = {
        "id": "probe-open",
        "type": "open_answer",
        "prompt": "What is an LRU cache?",
        "model_answer": "A cache that evicts the least recently used item.",
        "rubric": ["Says it evicts", "Says least recently used"],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    problems = sorted(caught.value.problems, key=lambda p: p.path)
    paths = [problem.path for problem in problems]
    if paths != ["plan.media[0]", "probe[0].type"]:
        pytest.fail(f"problems are {problems}")
    media, probe = problems
    if "'trace' is not used by any section" not in media.message:
        pytest.fail(f"plan.media[0] message: {media.message!r}")
    if "probe questions must be a closed type" not in probe.message:
        pytest.fail(f"probe[0].type message: {probe.message!r}")


def test_claim_backing_lines_and_symbol_are_checked(tmp_path: Path) -> None:
    """A claim must cite lines that hold its symbol, and a refuted claim is refused.

    The grounding pass trusts the cited span; a symbol missing from it means the
    citation points at the wrong code, and a contradicted claim would teach the
    reader something false.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(
        "".join(f"line_{number} = {number}\n" for number in range(1, 11)),
        encoding="utf-8",
    )
    lines_without_symbol = {"file": "pkg/a.py", "lines": [3, 5], "symbol": "frobnicate"}
    raw = {
        "schema_version": 2,
        "title": "Claims",
        "scope": {"summary": "One module.", "files": ["pkg/a.py"]},
        "sections": [
            {
                "id": "claimed",
                "title": "Claimed",
                "body": "Lead prose.",
                "elements": [
                    {
                        "type": "prose",
                        "markdown": "Two claims.",
                        "claims": [
                            {
                                "text": "It frobnicates.",
                                "backing": lines_without_symbol,
                            },
                            {
                                "text": "It never fails.",
                                "backing": {"reason": "not checked yet"},
                                "verified": "contradicted",
                            },
                        ],
                    }
                ],
                "checkpoints": [_choice("cp-1")],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, tmp_path)

    found = sorted((p.path, p.message) for p in caught.value.problems)
    expected = [
        (
            "sections[0].elements[0].claims[0].backing.symbol",
            "'frobnicate' does not appear in lines 3-5 of 'pkg/a.py'",
        ),
        (
            "sections[0].elements[0].claims[1].verified",
            "a contradicted claim cannot be served",
        ),
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")


def test_claims_are_addressed_by_their_authored_json_path() -> None:
    """A claim's path points into the lesson file as written, past any `code` sugar.

    The grounding pass writes each verdict back to the file at that path, so an
    index shifted by the sugar element would mark the wrong claim.
    """
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")

    paths = [path for path, _ in lesson.claims()]

    expected = [
        "sections[1].elements[0].claims[0]",
        "sections[2].elements[0].claims[0]",
        "sections[2].elements[1].claims[0]",
        "sections[2].elements[1].claims[1]",
        "sections[2].elements[1].claims[2]",
        "sections[3].elements[1].items[0]",
        "sections[3].elements[1].items[1]",
    ]
    if paths != expected:
        pytest.fail(f"claim paths are {paths}")


def test_diff_notes_and_asks_must_sit_on_the_new_side(tmp_path: Path) -> None:
    """A note or ask past the hunk's new side is reported at its own path.

    The page anchors notes and asks to new-side line numbers, so one outside
    the hunk would point at a line the reader cannot see.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    diff = raw["sections"][1]["elements"][1]
    diff["notes"][0]["lines"] = [16, 20]
    diff["asks"][0]["line"] = 22
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    found = sorted((p.path, p.message) for p in caught.value.problems)
    expected = [
        (
            "sections[1].elements[1].asks[0].line",
            "line 22 is not on the new side (17 to 21)",
        ),
        (
            "sections[1].elements[1].notes[0].lines",
            "16-20 is not on the new side (17 to 21)",
        ),
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")


def test_public_view_hides_the_answer_to_a_diff_line_ask() -> None:
    """An ask's answer is revealed only on request, so the page never holds it."""
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")

    sections = cast("list[dict[str, Any]]", lesson.public_view()["sections"])
    [diff] = [e for e in sections[1]["elements"] if e["type"] == "diff"]
    [ask] = diff["asks"]

    if ask != {"line": 21, "question": "Why `last=False`?"}:
        pytest.fail(f"public ask is {ask}")


def test_vocab_lines_min_opened_and_translation_columns_are_checked(
    tmp_path: Path,
) -> None:
    """Report each vocab problem at its own path.

    A term line outside the code, an unreachable `min_opened` and a term
    missing from a translation table each count.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    vocab = raw["sections"][2]["elements"][-1]
    vocab["terms"][0]["lines"] = [6]
    vocab["min_opened"] = 4
    del vocab["terms"][0]["library_term"]
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    at = "sections[2].elements[2]"
    found = sorted((p.path, p.message) for p in caught.value.problems)
    expected = [
        (f"{at}.min_opened", "must be between 1 and the number of terms (3)"),
        (
            f"{at}.terms[0].library_term",
            "is required in a translation table; write null for no equivalent",
        ),
        (f"{at}.terms[0].lines[0]", "line 6 is outside the code (1 to 5)"),
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")


def test_public_view_scrambles_parsons_lines_with_distractors_and_no_indent() -> None:
    """The reader sees every piece, decoys included, but neither order nor indent."""
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", FIXTURES / "project")
    question = next(q for _, q in lesson.questions() if isinstance(q, Parsons))
    pieces = [line.text for line in question.lines]
    pieces += [d.text for d in question.distractors]

    sections = cast("list[dict[str, Any]]", lesson.public_view()["sections"])
    shown = next(
        q
        for section in sections
        for q in section["checkpoints"]
        if q["id"] == question.id
    )

    if sorted(shown["lines"]) != sorted(pieces) or shown["lines"] == pieces:
        pytest.fail(f"expected {pieces} scrambled, got {shown['lines']}")
    if "distractors" in shown:
        pytest.fail(f"distractors leak separately: {shown['distractors']}")


def _spike_lesson(project: Path, correct: int) -> Path:
    spike_dir = project / ".grokcheck" / "spikes" / "sum"
    spike_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "spikes" / "sum" / "result.json", spike_dir)
    (spike_dir / "spike.py").write_text(
        "print(sum(2 * n for n in range(1, 7)))\n", "utf-8"
    )
    gate = {
        "id": "gate-sum",
        "type": "predict_output",
        "prompt": "What does the spike print?",
        "code": {
            "language": "python",
            "text": "print(sum(2 * n for n in range(1, 7)))",
        },
        "options": [
            {"text": "42", "why": "2 + 4 + 6 + 8 + 10 + 12"},
            {"text": "41", "why": "off by one"},
        ],
        "correct": correct,
    }
    raw = {
        "schema_version": 2,
        "title": "Spike",
        "scope": {"summary": "One spike.", "files": []},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "elements": [{"type": "spike", "spike_id": "sum", "gate": "gate-sum"}],
                "checkpoints": [gate],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = project / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    return lesson_file


def test_spike_gate_must_match_recorded_output(tmp_path: Path) -> None:
    """A spike's gate can never mark as correct an output the run did not print."""
    wrong, right = tmp_path / "wrong", tmp_path / "right"

    with pytest.raises(LessonError) as caught:
        load_lesson(_spike_lesson(wrong, correct=1), wrong)
    lesson = load_lesson(_spike_lesson(right, correct=0), right)

    found = [(p.path, p.message) for p in caught.value.problems]
    expected = [
        (
            "sections[0].checkpoints[0].correct",
            "option 1 does not match the recorded output '42'",
        )
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")
    element = lesson.sections[0].elements[0]
    if not isinstance(element, SpikeElement) or element.result["stdout"] != "42\n":
        pytest.fail(f"spike element is {element}")


_GUIDELINE = "\n".join(
    [
        "<!-- page 1 -->",
        *(f"Background paragraph {number}." for number in range(2, 10)),
        "<!-- page 2 -->",
        "Dosing in older adults.",
        "In adults over 65,",
        "  start at the lowest",
        "dose and titrate weekly.",
        "Monitor kidney function.",
    ]
)


def _quoted_lesson(project: Path, quotes: list[str | None]) -> Path:
    claims = [
        {
            "text": "Older adults start low.",
            "backing": {
                "file": ".grokcheck/sources/guideline.md",
                "lines": [12, 14],
                **({} if quote is None else {"quote": quote}),
            },
        }
        for quote in quotes
    ]
    raw = {
        "schema_version": 2,
        "title": "Dosing",
        "scope": {"summary": "One guideline.", "files": []},
        "sections": [
            {
                "id": "dosing",
                "title": "Dosing",
                "body": "Lead prose.",
                "elements": [
                    {"type": "prose", "markdown": "Quoted.", "claims": claims}
                ],
                "checkpoints": [_choice("cp-1")],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = project / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    return lesson_file


def test_source_citation_requires_a_verbatim_quote_in_the_span(tmp_path: Path) -> None:
    """A claim citing an ingested document must quote the cited lines verbatim.

    A line span alone cannot show which sentence carries the claim, and the
    reader of a paper checks the claim against its words; the page comes from
    the copy's page markers so the reader can find it in the original.
    """
    sources = tmp_path / ".grokcheck" / "sources"
    sources.mkdir(parents=True)
    (sources / "guideline.md").write_text(_GUIDELINE, encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(
            _quoted_lesson(tmp_path, [None, "reduce the dose by half"]), tmp_path
        )
    lesson = load_lesson(
        _quoted_lesson(tmp_path, ["start at the lowest dose"]), tmp_path
    )

    found = sorted((p.path, p.message) for p in caught.value.problems)
    expected = [
        (
            "sections[0].elements[0].claims[0].backing.quote",
            "a citation into .grokcheck/sources needs a quote",
        ),
        (
            "sections[0].elements[0].claims[1].backing.quote",
            "quote 'reduce the dose by half' is not in lines 12-14 of 'guideline.md'",
        ),
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")
    (_, claim), *_ = lesson.claims()
    if claim.page != 2:  # noqa: PLR2004
        pytest.fail(f"claim is {claim}")


def test_spike_claim_must_cite_a_recorded_result(tmp_path: Path) -> None:
    """A claim backed by a spike that was never run has no evidence to show."""
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    claim = {"text": "Never measured.", "backing": {"spike_id": "never-run"}}
    raw["sections"][0]["elements"] = [
        {"type": "prose", "markdown": "Claims.", "claims": [claim]}
    ]
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    found = [(p.path, p.message) for p in caught.value.problems]
    expected = [
        (
            "sections[0].elements[0].claims[0].backing.spike_id",
            "no recorded result for spike 'never-run' (run `grokcheck spike run`)",
        )
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")


def _playground_state(drop: int, back: int) -> dict[str, object]:
    lost = "lost" if back > drop + 1 else "live"
    return {
        "cells": {"link": ["up", "down"], "old": ["live", lost]},
        "outcomes": {
            "old": {"outcome": "lost", "lost": 1}
            if lost == "lost"
            else {"outcome": "complete"}
        },
        "explain": f"Dropped at {drop}, back at {back}.",
    }


def test_playground_table_must_cover_every_input_combination(tmp_path: Path) -> None:
    """Every combination of slider values needs its own recorded state.

    A gap would leave the reader on a slider position with nothing to show.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    states = {
        f"{drop},{back}": _playground_state(drop, back)
        for drop in range(1, 4)
        for back in range(2, 5)
    }
    raw["sections"][0].pop("code", None)
    raw["sections"][0]["elements"] = [
        {
            "type": "playground",
            "inputs": [
                {"id": "drop", "label": "Drops at tick", "min": 1, "max": 3},
                {"id": "back", "label": "Back at tick", "min": 2, "max": 4},
            ],
            "constraints": [{"after": "back", "gt": "drop"}],
            "presets": [{"label": "Long drop", "values": {"drop": 1, "back": 4}}],
            "variants": ["old"],
            "ticks": 2,
            "rows": [
                {"id": "link", "label": "connection"},
                {"id": "old", "label": "old code"},
            ],
            "legend": [
                {"cell": "up", "label": "connected", "colour": "accent"},
                {"cell": "down", "label": "dropped", "colour": "muted"},
                {"cell": "live", "label": "received live", "colour": "good"},
                {"cell": "lost", "label": "never delivered", "colour": "bad"},
            ],
            "states": states,
            "tasks": [
                {
                    "text": "Make the old code lose an event.",
                    "when": [{"variant": "old", "outcome": "lost", "min_lost": 1}],
                }
            ],
        }
    ]
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    complete = load_lesson(lesson_file, FIXTURES / "project")
    del states["3,4"]
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    element = complete.sections[0].elements[0]
    if not isinstance(element, PlaygroundElement) or len(element.states) != 9:  # noqa: PLR2004
        pytest.fail(f"complete table loaded as {element}")
    found = [(p.path, p.message) for p in caught.value.problems]
    expected = [("sections[0].elements[0].states", "missing state for inputs 3,4")]
    if found != expected:
        pytest.fail(f"problems are {found}")


def test_options_table_needs_every_baseline_and_no_waivable_hard_limit(
    tmp_path: Path,
) -> None:
    """A table lacking a baseline or waiving a hard limit is reported.

    The missing do-nothing row is reported at the table, the waivable hard
    constraint at its `waivable_by`.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    table = raw["sections"][3]["elements"][0]
    table["options"][0]["standing"] = "other"
    table["options"][1]["constraints"][0]["waivable_by"] = "the maintainer"
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    at = "sections[3].elements[0].options"
    found = sorted((p.path, p.message) for p in caught.value.problems)
    expected = [
        (at, "needs an option standing do_nothing"),
        (
            f"{at}[1].constraints[0].waivable_by",
            "a hard constraint cannot be waived",
        ),
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")


def test_video_element_must_name_files_in_the_lesson_dir_or_video_cache(
    tmp_path: Path,
) -> None:
    """A video plays only files the lesson dir or the video cache hold.

    The server streams whatever path a video names, so a path outside those
    two places, or one that does not exist, must stop the lesson loading.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    film = tmp_path / "film.mp4"
    film.write_bytes(b"mp4")
    (tmp_path / "film.vtt").write_text("WEBVTT\n", encoding="utf-8")
    video = {
        "type": "video",
        "id": "film",
        "src": str(film),
        "captions": str(tmp_path / "film.vtt"),
        "duration": 120.5,
        "transcript": "The narration, sentence by sentence.",
    }
    raw["sections"][0].setdefault("elements", []).append(video)
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    shown = load_lesson(lesson_file, FIXTURES / "project").public_view()
    sections = cast("list[dict[str, Any]]", shown["sections"])
    shown_types = [element["type"] for element in sections[0]["elements"]]
    if "video" not in shown_types:
        pytest.fail(f"the loaded section shows {shown_types}, no video")

    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    outside.write_bytes(b"mp4")
    index = len(raw["sections"][0]["elements"]) - 1
    raw["sections"][0]["elements"][index] = {
        **video,
        "src": str(outside),
        "captions": str(tmp_path / "missing.vtt"),
    }
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    where = f"sections[0].elements[{index}]"
    expected = [
        (f"{where}.captions", "does not exist"),
        (f"{where}.src", "outside the lesson directory and the video cache"),
    ]
    problems = sorted(caught.value.problems, key=lambda p: p.path)
    if len(problems) != len(expected) or not all(
        _matches(problem, path, fragment)
        for problem, (path, fragment) in zip(problems, expected, strict=False)
    ):
        pytest.fail(f"expected {expected}, got {problems}")
