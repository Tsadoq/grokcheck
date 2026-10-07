"""One live lesson as the reader's browser and the agent's `wait` loop see it."""

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from grokcheck.grading import ResponseError
from grokcheck.lesson import load_lesson
from grokcheck.run import LessonRun, RunError, public_view_for

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _choice(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "type": "single_choice",
        "prompt": "Which?",
        "options": [{"text": "a", "why": "first"}, {"text": "b", "why": "second"}],
        "correct": 0,
    }


def test_question_asked_between_waits_is_not_lost(tmp_path: Path) -> None:
    """A reader question asked while no agent `wait` is pending still reaches it.

    The agent runs `wait` once per event, so there are gaps between runs; a
    question asked in one of those gaps must be handed to the next `wait`.
    """
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", project)
    run = LessonRun.create(lesson, project)
    text = "Why does eviction pick the oldest entry?"

    run.ask(lesson.sections[0].id, "", text)
    events = run.events_after(0, timeout=0.1)

    questions = [event for event in events if event.type == "question"]
    if len(events) != 1 or len(questions) != 1 or questions[0].body["text"] != text:
        pytest.fail(f"expected one question event with text {text!r}, got {events}")


def test_ask_records_mode_and_rejects_unknown_mode(tmp_path: Path) -> None:
    """A Socratic question tells the agent to guide instead of answer.

    The mode reaches the agent on the logged event; a mode the agent has no
    rule for is refused rather than silently treated as a plain question.
    """
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", project)
    run = LessonRun.create(lesson, project)
    section_id = lesson.sections[0].id

    run.ask(section_id, "", "Why evict the oldest entry?", "socratic")

    mode = run.events_after(0, timeout=0)[0].body.get("mode")
    if mode != "socratic":
        pytest.fail(f"expected question mode 'socratic', got {mode!r}")
    with pytest.raises(RunError, match="mode must be"):
        run.ask(section_id, "", "And this?", "lecture")


def test_probe_answers_are_logged_but_never_enter_final_score(tmp_path: Path) -> None:
    """A probe measures prior knowledge, so a wrong probe answer never costs score.

    It is still kept in the results, where the debrief reads it.
    """
    raw = {
        "schema_version": 2,
        "title": "Probe",
        "scope": {"summary": "Nothing on disk.", "files": []},
        "probe": [_choice("p1")],
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "checkpoints": [_choice("cp-1")],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    run = LessonRun.create(load_lesson(lesson_file, tmp_path), tmp_path)

    run.answer("p1", 1)
    for final_id in ("f-1", "f-2", "f-3"):
        run.answer(final_id, 0)
    summary = run.submit()

    results = json.loads((run.lesson_dir / "results.json").read_text("utf-8"))
    final_ids = [question["question_id"] for question in results["final"]]
    if summary.final_score != 1.0:
        pytest.fail(f"final_score is {summary.final_score}")
    if [q["outcome"] for q in results["probe"]] != ["incorrect"]:
        pytest.fail(f"probe results are {results['probe']}")
    if "p1" in final_ids:
        pytest.fail(f"probe p1 is among the final questions {final_ids}")


def _recording(lines: list[int]) -> dict[str, object]:
    events = [
        {"seq": seq, "event": "line", "task": None, "file": "mod.py", "line": line}
        for seq, line in enumerate(lines)
    ]
    return {"events": events, "truncated": False}


def test_gated_trace_steps_are_withheld_until_predict_state_is_answered(
    tmp_path: Path,
) -> None:
    """A trace hides the steps its gate asks about until the reader commits.

    Showing the state before the prediction would turn the prediction into
    reading, so the server keeps it back and hands it over with the feedback.
    """
    (tmp_path / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", "utf-8")
    traces = tmp_path / ".grokcheck" / "traces"
    traces.mkdir(parents=True)
    (traces / "run.json").write_text(json.dumps(_recording([1, 2, 3, 4])), "utf-8")
    revealed = "d is bound last, after the reader predicted it."
    steps = [
        {"narration": [f"Line {n} runs."], "state": {"names": [n]}} for n in (1, 2, 3)
    ]
    steps.append({"narration": [revealed], "state": {"names": [1, 2, 3, 4]}})
    gate = {
        "id": "gate-1",
        "type": "predict_state",
        "prompt": "How many names exist after line 4?",
        "trace_id": "run",
        "version": 0,
        "step": 3,
        "options": [
            {"text": "2", "why": "counts only the earlier lines"},
            {"text": "3", "why": "forgets the line being run"},
            {"text": "4", "why": "every line binds one name"},
        ],
        "correct": 2,
    }
    raw = {
        "schema_version": 2,
        "title": "Gated",
        "scope": {"summary": "One module.", "files": ["mod.py"]},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "elements": [
                    {
                        "type": "trace",
                        "trace_id": "run",
                        "title": "Binding names",
                        "gate": "gate-1",
                        "panels": [{"id": "names", "label": "Names", "kind": "chips"}],
                        "versions": [
                            {
                                "label": "only",
                                "code": {"file": "mod.py", "lines": [1, 4]},
                                "steps": steps,
                            }
                        ],
                    }
                ],
                "checkpoints": [gate],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    run = LessonRun.create(load_lesson(lesson_file, tmp_path), tmp_path)

    def gated_step(view: dict[str, Any]) -> dict[str, Any]:
        step: dict[str, Any] = view["sections"][0]["elements"][0]["versions"][0][
            "steps"
        ][3]
        return step

    before = gated_step(public_view_for(run))
    feedback = run.answer("gate-1", 2, "sure")
    after = gated_step(public_view_for(run))

    if before.keys() != {"cur_line"}:
        pytest.fail(f"gated step served before the answer: {before}")
    payload = feedback.grade.reveal.element_payload if feedback.grade else None
    shown = cast("list[dict[str, Any]]", payload["steps"]) if payload else []
    if len(shown) < 4 or shown[3]["narration"][0] != revealed:  # noqa: PLR2004
        pytest.fail(f"answer feedback does not reveal the gated step: {payload}")
    if after.get("narration") != [revealed]:
        pytest.fail(f"gated step still withheld after the answer: {after}")


def test_spike_result_is_withheld_until_its_gate_is_answered(tmp_path: Path) -> None:
    """The recorded run stays on the server until the reader predicts its output."""
    spike_dir = tmp_path / ".grokcheck" / "spikes" / "sum"
    spike_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "spikes" / "sum" / "result.json", spike_dir)
    (spike_dir / "spike.py").write_text("print(42)\n", "utf-8")
    gate = {
        "id": "gate-sum",
        "type": "predict_output",
        "prompt": "What does the spike print?",
        "code": {"language": "python", "text": "print(42)"},
        "accepted": ["42"],
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
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    run = LessonRun.create(load_lesson(lesson_file, tmp_path), tmp_path)

    def spike(view: dict[str, Any]) -> dict[str, Any]:
        element: dict[str, Any] = view["sections"][0]["elements"][0]
        return element

    before = spike(public_view_for(run))
    feedback = run.answer("gate-sum", "41", "sure")
    after = spike(public_view_for(run))

    if "result" in before:
        pytest.fail(f"recorded run served before the answer: {before}")
    payload = feedback.grade.reveal.element_payload if feedback.grade else None
    if not payload or cast("dict[str, Any]", payload["result"])["stdout"] != "42\n":
        pytest.fail(f"answer feedback does not reveal the recorded run: {payload}")
    if after.get("result", {}).get("stdout") != "42\n":
        pytest.fail(f"recorded run still withheld after the answer: {after}")


def _option_row(name: str, standing: str) -> dict[str, object]:
    return {
        "name": name,
        "costs": "Some upkeep.",
        "assumes": "Load stays flat.",
        "constraints": [{"text": "Runs on one host.", "hard": True}],
        "standing": standing,
    }


def test_reader_first_options_table_withheld_until_commit(tmp_path: Path) -> None:
    """The reader lists their own options before the agent's table appears.

    Seeing the agent's options first anchors the reader on them, so the table
    stays on the server until the reader's own list is logged, and that list
    is kept in the results for the debrief.
    """
    typed = "redis, sqlite; criteria: ops cost"
    raw = {
        "schema_version": 2,
        "title": "Options",
        "scope": {"summary": "Pick a cache.", "files": []},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "elements": [
                    {
                        "type": "options",
                        "id": "opt-1",
                        "question": "Which cache should we use?",
                        "criteria": ["ops cost"],
                        "options": [
                            _option_row("No cache", "do_nothing"),
                            _option_row("functools.lru_cache", "existing_dependency"),
                            _option_row("Own LRU", "build"),
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
    run = LessonRun.create(load_lesson(lesson_file, tmp_path), tmp_path)

    def options(view: dict[str, Any]) -> dict[str, Any]:
        element: dict[str, Any] = view["sections"][0]["elements"][0]
        return element

    view_before = options(public_view_for(run))
    run.commit("opt-1", typed)
    view_after = options(public_view_for(run))
    for final_id in ("f-1", "f-2", "f-3"):
        run.answer(final_id, 0)
    run.submit()
    results = json.loads((run.lesson_dir / "results.json").read_text("utf-8"))

    if "options" in view_before:
        pytest.fail(f"options table served before the commit: {view_before}")
    if len(view_after.get("options", [])) != 3:  # noqa: PLR2004
        pytest.fail(f"options table not served after the commit: {view_after}")
    if results.get("commits", [{}])[0].get("text") != typed:
        pytest.fail(f"commit text missing from results: {results.get('commits')}")


def test_assumption_confidences_reach_results_beside_their_spike(
    tmp_path: Path,
) -> None:
    """Each assumption's rating lands in the results beside its spike.

    The debrief compares the reader's confidence with what the spike found.
    """
    spike_dir = tmp_path / ".grokcheck" / "spikes" / "sum"
    spike_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "spikes" / "sum" / "result.json", spike_dir)
    unchecked = {"reason": "No data yet."}
    raw = {
        "schema_version": 2,
        "title": "Assumptions",
        "scope": {"summary": "One choice.", "files": []},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "elements": [
                    {
                        "type": "assumptions",
                        "id": "assume-1",
                        "items": [
                            {
                                "claim": "The sum is 42.",
                                "backing": unchecked,
                                "checked_by_spike": "sum",
                            },
                            {"claim": "Inputs stay small.", "backing": unchecked},
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
    run = LessonRun.create(load_lesson(lesson_file, tmp_path), tmp_path)

    with pytest.raises(ResponseError):
        run.answer("assume-1", ["sure"])
    run.answer("assume-1", ["sure", "guess"])
    for final_id in ("f-1", "f-2", "f-3"):
        run.answer(final_id, 0)
    run.submit()
    results = json.loads((run.lesson_dir / "results.json").read_text("utf-8"))

    rated = [(a["confidence"], a["checked_by_spike"]) for a in results["assumptions"]]
    if rated != [("sure", "sum"), ("guess", None)]:
        pytest.fail(f"assumption ratings in results are {results['assumptions']}")


def test_open_names_the_type_when_no_union_member_matches(tmp_path: Path) -> None:
    """A snapshot value matching no union member is a RunError naming its keys."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", project)
    run = LessonRun.create(lesson, project)
    snapshot = run.lesson_dir / "lesson.json"
    raw = json.loads(snapshot.read_text(encoding="utf-8"))
    raw["sections"][0]["checkpoints"][0]["type"] = "no_such_type"
    snapshot.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RunError, match=r"no member of .* matches the snapshot keys"):
        LessonRun.open(run.lesson_dir)


def test_results_name_the_element_of_each_answer_and_score_by_element(
    tmp_path: Path,
) -> None:
    """Each answer names the element it is about, and checkpoints sum per element.

    The debrief reads `by_element` to see which kind of picture taught well.
    """
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_views.json", project)
    run = LessonRun.create(lesson, project)

    run.answer("cp-lost", ["new|client|3", "new|client|4"])
    run.answer("cp-prompts", ["client|1"])
    run.answer("cp-walk", 2)
    run.answer("cp-names", 0)
    run.answer("f-transfer", ["client|4"])
    run.answer("f-wrong", ["client|4"])
    run.answer("f-kinds", {"1": {"kind": "prompt"}})
    run.answer("f-order", 0)
    run.submit()

    results = json.loads((run.lesson_dir / "results.json").read_text("utf-8"))
    elements = {
        item["question_id"]: item["element"]
        for item in (*results["checkpoints"], *results["final"])
    }
    if elements != {
        "cp-lost": "view:lanes",
        "cp-prompts": "view:lanes",
        "cp-walk": "view:steps",
        "cp-names": None,
        "f-transfer": "view:lanes",
        "f-wrong": "view:lanes",
        "f-kinds": "view:table",
        "f-order": None,
    }:
        pytest.fail(f"elements {elements}")
    by_element = results["by_element"]
    if by_element["view:lanes"] != {"answered": 2, "correct": 1, "mean_score": 0.75}:
        pytest.fail(f"by_element {by_element}")
    if set(by_element) != {"view:lanes", "view:steps", "null"}:
        pytest.fail(f"by_element keys {sorted(by_element)}")


def test_a_retake_keeps_the_kinds_and_datasets_its_frames_draw(
    tmp_path: Path,
) -> None:
    """A retake has no sections, but its frames still need kinds and provenance."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_views.json", project)
    run = LessonRun.create(lesson, project)
    run.answer("f-transfer", [])
    run.answer("f-wrong", [])
    run.answer("f-kinds", {})
    run.answer("f-order", 1)
    run.submit()

    retake = LessonRun.retake_from(run.lesson_dir, {"incorrect", "partial"})

    if retake.kinds != lesson.kinds or retake.datasets != lesson.datasets:
        pytest.fail("the retake dropped the lesson's kinds or datasets")
    if not any(getattr(q, "frame", None) for q in retake.final):
        pytest.fail("the retake lost its framed questions")
