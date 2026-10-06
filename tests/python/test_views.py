"""Views as the page receives them: items, masks, gated payloads and answer keys."""

import dataclasses
import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from grokcheck import views
from grokcheck.lesson import (
    FillTable,
    Lesson,
    LessonError,
    SelectItems,
    ViewElement,
    load_lesson,
)
from grokcheck.run import LessonRun, _dump_lesson, _load_lesson, public_view_for

FIXTURES = Path(__file__).parent.parent / "fixtures"
PROJECT = FIXTURES / "project"
LESSON = FIXTURES / "lessons" / "valid_views.json"
GOLDEN = FIXTURES / "views_public.json"

LOAD_ERRORS = {
    "dataset_missing": ("datasets[1]", "no dataset 'nowhere'"),
    "encode_field": ("sections[0].elements[0].encode.label", "not a field"),
    "bad_selector": ("sections[0].elements[0].where.seq.over", "gt, gte, lt or lte"),
    "undeclared_kind": (
        "sections[0].elements[0].encode.class",
        "'thinking' is not a declared kind",
    ),
    "duplicate_cell": ("sections[0].elements[0]", "share the cell"),
    "note_matches_nothing": ("sections[0].elements[0].notes[0].where", "no item"),
    "all_held_out": ("sections[0].elements[0].where", "held out"),
    "wrong_in_section": ("sections[0].elements[0].data", "wrong dataset"),
    "not_named": ("sections[0].elements[0]", "never asked about"),
    "gate_mismatch": ("sections[0].elements[0].gate", "names this view"),
    "gated_inputs": ("sections[0].elements[0].inputs", "cannot have inputs"),
    "answer_empty": ("sections[0].checkpoints[0].answer", "matches no item"),
    "answer_total": ("sections[0].checkpoints[0].answer", "every candidate"),
    "answer_not_candidate": ("sections[0].checkpoints[0].answer", "not candidates"),
    "steps_too_long": ("sections[0].elements[1].steps[0].code.lines", "7 lines"),
    "steps_far_apart": ("sections[0].elements[1].steps[0].code.lines", "40 lines"),
    "final_names_section_view": ("final[3].view", "needs an inline view"),
}


def _lesson() -> Lesson:
    return load_lesson(LESSON, PROJECT)


def _view(lesson: Lesson, view_id: str) -> ViewElement:
    return next(
        e
        for s in lesson.sections
        for e in s.elements
        if isinstance(e, ViewElement) and e.id == view_id
    )


def _public(lesson: Lesson, view_id: str) -> dict[str, Any]:
    sections = cast("list[dict[str, Any]]", lesson.public_view()["sections"])
    return next(e for s in sections for e in s["elements"] if e.get("id") == view_id)


def _question(lesson: Lesson, question_id: str) -> Any:  # noqa: ANN401
    return next(q for _, q in lesson.questions() if q.id == question_id)


@pytest.mark.parametrize("name", sorted(LOAD_ERRORS))
def test_each_view_load_error_is_reported_at_its_path(name: str) -> None:
    """Every rule a view breaks is a load error at the JSON path to fix."""
    path, fragment = LOAD_ERRORS[name]

    with pytest.raises(LessonError) as caught:
        load_lesson(FIXTURES / "lessons" / f"invalid_view_{name}.json", PROJECT)

    found = [(p.path, p.message) for p in caught.value.problems]
    if not any(at == path and fragment in message for at, message in found):
        pytest.fail(f"expected {fragment!r} at {path}, got {found}")


def test_a_version_2_lesson_using_views_needs_version_3() -> None:
    """A view in a version 2 lesson names the version it needs."""
    with pytest.raises(LessonError) as caught:
        load_lesson(FIXTURES / "lessons" / "invalid_shape_view_needs_v3.json", PROJECT)

    messages = {p.path: p.message for p in caught.value.problems}
    if "schema_version 3" not in messages.get("sections[0].elements[0]", ""):
        pytest.fail(f"view in a version 2 lesson reported as {messages}")


def test_public_view_matches_the_golden_file() -> None:
    """The page and its tests read `views_public.json`; it must match the code.

    Regenerate it from `Lesson.public_view()` when the public shape changes.
    """
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    if json.loads(json.dumps(_lesson().public_view())) != golden:
        pytest.fail("views_public.json is out of date with Lesson.public_view()")


def test_public_view_carries_kinds_datasets_and_never_an_answer_key() -> None:
    """Kinds and dataset provenance are public; answer cells and edits are not."""
    public = _lesson().public_view()
    text = json.dumps(public)

    datasets = cast("list[dict[str, Any]]", public["datasets"])
    if [d["id"] for d in datasets] != ["reconnect", "terms", "producer"]:
        pytest.fail(f"public datasets {datasets}")
    if [k["id"] for k in cast("list[dict[str, Any]]", public["kinds"])] != [
        "prompt",
        "answer",
        "thinking",
        "error",
    ]:
        pytest.fail(f"public kinds {public['kinds']}")
    for secret in ('"answer_cells"', '"cells"', '"_edited"'):
        if secret in text:
            pytest.fail(f"{secret} reached the public view")


def test_masked_lanes_show_one_placeholder_per_reference_key() -> None:
    """A masked lane is replaced by `?` cells at the reference lane's columns.

    Real positions would leak how many items arrive and which repeat.
    """
    lesson = _lesson()
    items = _public(lesson, "v-drop2")["items"]
    masked = [i for i in items if i.get("_pane") == "new" and i["lane"] == "client"]
    server = [i for i in items if i.get("_pane") == "new" and i["lane"] == "server"]

    if not all(i.get("_masked") for i in masked):
        pytest.fail(f"real items left in the masked lane: {masked}")
    if [i["seq"] for i in masked] != [i["seq"] for i in server]:
        pytest.fail(f"placeholders {masked} do not sit at the server columns")
    if any("_lost" in i or "text" in i for i in masked):
        pytest.fail(f"a placeholder leaks what arrived: {masked}")
    question = _question(lesson, "cp-lost")
    if question.candidates != tuple(i["_cell"] for i in masked):
        pytest.fail(f"candidates {question.candidates} are not the placeholders")
    if question.answer_cells != ("new|client|3", "new|client|4"):
        pytest.fail(f"answer cells {question.answer_cells}")


def test_masked_table_decision_and_steps_withhold_only_the_asked_part() -> None:
    """Each layout withholds what its gate asks and keeps the rest visible."""
    lesson = _lesson()
    table = _public(lesson, "v-rules")["items"]
    decision = _public(lesson, "v-read2")["items"]
    steps = _public(lesson, "v-walk")["steps"]
    data_steps = _public(lesson, "v-prod")

    if [i["kind"] for i in table] != ["prompt", "answer", None, None, None]:
        pytest.fail(f"table fill cells {table}")
    if any("_taken" in i or "answer" in i for i in decision):
        pytest.fail(f"decision items leak the path: {decision}")
    if "note" not in steps[1] or "note" in steps[2] or "code" not in steps[2]:
        pytest.fail(f"steps from the gated step on are not withheld: {steps}")
    if not all("l_items" in item for item in data_steps["items"]):
        pytest.fail("an ungated data steps view lost its shown fields")


def test_gated_payload_reaches_the_reader_with_the_gate_feedback(
    tmp_path: Path,
) -> None:
    """Answering a view's gate hands back its full items and unmasks the view."""
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project)
    run = LessonRun.create(load_lesson(LESSON, project), project)

    feedback = run.answer("cp-lost", ["new|client|3"], "sure")
    payload = feedback.grade.reveal.element_payload if feedback.grade else None
    sections = cast("list[dict[str, Any]]", public_view_for(run)["sections"])
    after = next(e for s in sections for e in s["elements"] if e["id"] == "v-drop2")

    if not payload or payload["view"] != "v-drop2":
        pytest.fail(f"gate feedback carries {payload}")
    items = cast("list[dict[str, Any]]", payload["items"])
    lost = [i["_cell"] for i in items if i.get("_lost")]
    if lost != ["new|client|3", "new|client|4"]:
        pytest.fail(f"payload lost items {lost}")
    if any(i.get("_masked") for i in after["items"]):
        pytest.fail("the view is still masked after its gate was answered")


def test_fill_table_keys_hold_every_masked_cell() -> None:
    """The blanks are the masked fill cells, and the key holds their values."""
    question = _question(_lesson(), "cp-kinds")

    if not isinstance(question, FillTable):
        pytest.fail(f"cp-kinds is {type(question).__name__}")
    if [(b.cell, b.field) for b in question.blanks] != [
        ("3", "kind"),
        ("4", "kind"),
        ("5", "kind"),
    ]:
        pytest.fail(f"blanks {question.blanks}")
    if question.cells != {
        "3": {"kind": "prompt"},
        "4": {"kind": "thinking"},
        "5": {"kind": "answer"},
    }:
        pytest.fail(f"cells {question.cells}")
    choices = _public(_lesson(), "v-rules")["choices"]
    if choices != {"kind": ["answer", "error", "prompt", "thinking"]}:
        pytest.fail(f"choices {choices}")


def test_final_questions_carry_their_inline_view_as_a_frame() -> None:
    """A final question's view travels as `frame`, masked for good and unedited."""
    lesson = _lesson()
    public = cast("list[dict[str, Any]]", lesson.public_view()["final"])
    transfer, wrong = public[0], public[1]
    question = _question(lesson, "f-wrong")

    client = [i for i in transfer["frame"]["items"] if i["lane"] == "client"]
    if not client or not all(i.get("_masked") for i in client):
        pytest.fail(f"transfer frame not masked: {client}")
    if "answer_cells" in wrong or "answer" in wrong:
        pytest.fail(f"answer key in the public final question: {wrong}")
    if not isinstance(question, SelectItems) or question.answer_cells != ("client|4",):
        pytest.fail(f"wrong-data item key {question}")


def test_held_out_rows_reach_only_final_questions() -> None:
    """Section views never show held-out rows; a final frame may draw only them."""
    lesson = _lesson()
    scrub = _view(lesson, "v-scrub")
    held = {"version": "new", "drop": 3, "lid": 4}

    if any(all(i.get(k) == v for k, v in held.items()) for i in scrub.items):
        pytest.fail("a section view shows held-out rows")
    if {(i["drop"], i["lid"]) for i in scrub.items} == set():
        pytest.fail("the scrubber has no items")
    frame = _question(lesson, "f-transfer").frame
    if not all(i["drop"] == 3 and i["lid"] == 4 for i in frame["items"]):  # noqa: PLR2004
        pytest.fail(f"transfer frame rows {frame['items']}")


def test_inputs_list_every_value_and_share_through_from() -> None:
    """An input lists the values its field takes; `from` shares another view's."""
    lesson = _lesson()
    scrub = _public(lesson, "v-scrub")
    read = _public(lesson, "v-read")

    values = {i["field"]: i["values"] for i in scrub["inputs"]}
    if values != {"drop": [2, 3], "lid": [2, 3, 4], "running": [0, 1]}:
        pytest.fail(f"input values {values}")
    if read["inputs"] != {"from": "v-scrub"}:
        pytest.fail(f"shared inputs {read['inputs']}")


def test_view_notes_steps_and_nodes_are_grounded_claims() -> None:
    """Notes, step notes and node notes are claims at their authored paths."""
    claims = dict(_lesson().claims())

    note = claims["sections[1].elements[0].notes[0]"]
    if dataclasses.asdict(note.backing) != {
        "data": "reconnect",
        "rows": {
            "and": [
                {"version": "new", "drop": 2, "lid": 2, "running": 0},
                {"lane": "client", "kind": "thinking"},
            ]
        },
    }:
        pytest.fail(f"note backing {note.backing}")
    for path in (
        "sections[1].elements[1].steps[0]",
        "sections[3].elements[1].nodes[1]",
    ):
        if path not in claims:
            pytest.fail(f"no claim at {path}: {sorted(claims)}")


def test_a_view_lesson_survives_the_run_snapshot() -> None:
    """A run rebuilds the lesson from its snapshot, views and frames included."""
    lesson = _lesson()

    if _load_lesson(json.loads(json.dumps(_dump_lesson(lesson)))) != lesson:
        pytest.fail("the snapshot of a view lesson does not rebuild equal")


def test_a_view_over_the_item_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A view past the item cap fails to load rather than flooding the page."""
    monkeypatch.setattr(views, "MAX_ITEMS", 5)

    with pytest.raises(LessonError) as caught:
        _lesson()

    if not any("at most 5 allowed" in p.message for p in caught.value.problems):
        pytest.fail(f"item cap not enforced: {caught.value.problems}")


def test_a_playground_keeps_its_cells_beside_the_secret_fill_table_cells() -> None:
    """Only a fill_table's `cells` are secret; a playground state's cells are shown."""
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", PROJECT)
    sections = cast("list[dict[str, Any]]", lesson.public_view()["sections"])
    states = [
        state
        for section in sections
        for element in section["elements"]
        if element["type"] == "playground"
        for state in element["states"]
    ]

    if not states or not all("cells" in state for state in states):
        pytest.fail("playground states lost their cells in the public view")


@pytest.mark.parametrize(
    ("rows", "error"),
    [({"lane": "client", "event": "409"}, None), ({"lane": "nowhere"}, "no row")],
)
def test_a_claim_backed_by_data_must_match_a_recorded_row(
    tmp_path: Path, rows: dict[str, object], error: str | None
) -> None:
    """A data backing cites recorded rows, so a selector matching none is refused."""
    raw = json.loads(LESSON.read_text(encoding="utf-8"))
    claim = {
        "text": "A reconnect can end in 409.",
        "backing": {"data": "reconnect", "rows": rows},
    }
    raw["sections"][1]["elements"][0]["claims"] = [claim]
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    try:
        load_lesson(lesson_file, PROJECT)
    except LessonError as caught:
        found = [p.message for p in caught.problems]
        if error is None or not any(error in message for message in found):
            pytest.fail(f"data backing {rows} refused with {found}")
    else:
        if error is not None:
            pytest.fail(f"data backing {rows} matching no row was accepted")


def test_split_panes_mark_the_items_that_differ_once_revealed() -> None:
    """With `split` on a recorded input, `_differs` compares the panes.

    Grouping each pane alone would leave `_differs` false everywhere.
    """
    payload = views.gated_payload(_view(_lesson(), "v-drop2"))
    items = cast("list[dict[str, Any]]", payload["items"])
    differs = {i["_cell"] for i in items if i.get("_differs")}

    if not {"new|client|3", "old|client|3"} <= differs:
        pytest.fail(f"items that differ across panes: {sorted(differs)}")
    if "new|server|1" in differs:
        pytest.fail("identical server lanes are marked as differing")


def test_a_masked_pane_leaks_no_difference_flag_from_the_other_pane() -> None:
    """While one pane is masked, `_differs` on the other would hint at the answer."""
    items = _public(_lesson(), "v-drop2")["items"]

    if leaked := [i["_cell"] for i in items if "_differs" in i]:
        pytest.fail(f"_differs sent before the gate is answered: {leaked}")


def test_a_gated_decision_sends_no_answer_or_path_before_its_gate() -> None:
    """A gated decision view withholds each node's answer and whether it ran."""
    lesson = _lesson()
    public = _public(lesson, "v-read2")
    full = views.gated_payload(_view(lesson, "v-read2"))

    if any("answer" in i or "_taken" in i for i in public["items"]):
        pytest.fail(f"decision answer key in the public view: {public['items']}")
    if not all("_taken" in i for i in cast("list[dict[str, Any]]", full["items"])):
        pytest.fail("the gated payload does not carry the path")
