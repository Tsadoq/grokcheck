"""A chapter's persistent scene: the Mermaid subset, the lint rules and the plan."""

from typing import Any

import pytest
from grokcheck_media import scene
from grokcheck_media.concept_video import lint_script

DECISION = """flowchart TD
  H[Last-Event-ID] -->|start cursor| R{read}
  R -->|cursor > end| C[409]
  R -->|turn running| F[follow]
  F -->|await wake_up| F
"""


def _same(got: object, want: object) -> None:
    if got != want:
        pytest.fail(f"got {got!r}, want {want!r}")


def _script(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    beats = extra.pop("beats", [{"say": "One plain sentence.", "show": "a"}])
    return {
        "concept_id": "c",
        "chapter_id": "ch01",
        "title": "T",
        "word_budget": 150,
        "beats": beats,
        **extra,
    }


def _says(count: int) -> list[dict[str, Any]]:
    return [{"say": f"Sentence {n}.", "show": "a"} for n in range(count)]


def test_parse_mermaid_reads_nodes_decisions_edges_and_loops() -> None:
    """Shapes give labels, braces mark decisions, pipes label edges."""
    flow = scene.parse_mermaid(DECISION)
    _same(flow["direction"], "TD")
    _same(
        [(n["id"], n["label"], n["decision"]) for n in flow["nodes"]],
        [
            ("H", "Last-Event-ID", False),
            ("R", "read", True),
            ("C", "409", False),
            ("F", "follow", False),
        ],
    )
    _same(
        [(e["from"], e["to"], e["label"]) for e in flow["edges"]],
        [
            ("H", "R", "start cursor"),
            ("R", "C", "cursor > end"),
            ("R", "F", "turn running"),
            ("F", "F", "await wake_up"),
        ],
    )


def test_parse_mermaid_accepts_lr_quotes_bare_ids_and_refuses_other_diagrams() -> None:
    """LR stays LR, quoted labels lose quotes, an undeclared id is its own label."""
    flow = scene.parse_mermaid(
        'graph LR\n  A["read(...)"] --> B\n  B -- next --> C[(db)]'
    )
    _same(flow["direction"], "LR")
    _same(
        {n["id"]: n["label"] for n in flow["nodes"]},
        {"A": "read(...)", "B": "B", "C": "db"},
    )
    _same(flow["edges"][1]["label"], "next")
    with pytest.raises(ValueError, match="flowchart"):
        scene.parse_mermaid("sequenceDiagram\n  A->>B: hi")


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (
            _script(beats=_says(4)),
            "4 beats and no scene",
        ),
        (
            _script(
                beats=[
                    {"say": "First.", "show": "a = 1", "code": True},
                    {"say": "Second.", "show": "b = 2", "code": True},
                ]
            ),
            "2 code beats",
        ),
        (
            _script(beats=[{"say": "Long.", "show": "1\n2\n3\n4\n5\n6", "code": True}]),
            "shows 6 lines of code",
        ),
        (
            _script(scene={"kind": "tree"}, beats=_says(1)),
            "scene.kind must be one of",
        ),
        (
            _script(
                scene={"kind": "flow", "mermaid": DECISION},
                beats=[{"say": "Go.", "add": ["H->R"], "branch": "H->R"}],
            ),
            "H is not a decision node",
        ),
        (
            _script(
                scene={"kind": "flow", "mermaid": DECISION},
                beats=[{"say": "Go.", "add": ["H"], "focus": ["R"]}],
            ),
            "beats[0].focus: 'R' is not on screen yet",
        ),
        (
            _script(
                scene={"kind": "flow", "mermaid": DECISION},
                beats=[
                    {"say": "Go.", "add": ["R->C", "H->R"], "move": ["R->C", "H->R"]}
                ],
            ),
            "R->C does not lead into H->R",
        ),
        (
            _script(
                scene={"kind": "state", "items": [{"id": "e1", "label": "1"}]},
                beats=[{"say": "Go.", "move": {"cursor": "e1"}}],
            ),
            "item 'e1' is not on screen yet",
        ),
        (
            _script(
                scene={"kind": "flow", "mermaid": DECISION},
                beats=[{"say": "Go.", "add": ["X"]}],
            ),
            "unknown id 'X'",
        ),
    ],
)
def test_lint_refuses_code_heavy_or_sceneless_chapters(
    script: dict[str, Any], expected: str
) -> None:
    """Each broken script names its problem."""
    problems = lint_script(script)
    if not (any(expected in problem for problem in problems)):
        pytest.fail(str(problems))


def test_lint_accepts_a_scened_chapter_with_one_short_code_beat() -> None:
    """A scene lets beats skip `show`; one code beat of five lines is fine."""
    beats = [
        {"say": "It starts.", "add": ["H"]},
        {"say": "It reads.", "add": ["H->R"], "show": "1\n2\n3\n4\n5", "code": True},
        {"say": "It refuses.", "add": ["R->C"], "branch": "R->C"},
        {"say": "It follows.", "add": ["R->F", "F->F"], "move": ["R->F", "F->F"]},
    ]
    _same(
        lint_script(_script(scene={"kind": "flow", "mermaid": DECISION}, beats=beats)),
        [],
    )


@pytest.mark.parametrize(
    ("say", "expected"),
    [
        ("The route awaits supervisor dot read first.", "'supervisor dot read'"),
        ("It calls supervisor.read before streaming.", "'supervisor.read'"),
        ("The app raises Cursor Beyond End here.", "'Cursor Beyond End'"),
        ("The app raises CursorBeyondEnd here.", "'CursorBeyondEnd'"),
        (" ".join(["word"] * 26), "26 words"),
        ("It sets end_cursor, wake_up and _settled.", "3 identifiers"),
    ],
)
def test_lint_refuses_narration_that_reads_code_aloud(say: str, expected: str) -> None:
    """Narration says what code does; identifiers stay few and short."""
    problems = lint_script(_script(beats=[{"say": say, "show": "a"}]))
    if not (any(expected in problem for problem in problems)):
        pytest.fail(str(problems))


def test_lint_accepts_plain_narration_with_two_identifiers() -> None:
    """Two identifiers, a status with its meaning, under 25 words."""
    say = "It waits on wake_up, and a stale end_cursor gets a 409 conflict."
    _same(lint_script(_script(beats=[{"say": say, "show": "a"}])), [])


def test_layout_layers_flow_and_centres_children_under_parents() -> None:
    """Longest-path layers go down; a loop is a back edge, not a layer."""
    flow = scene.normalise({"kind": "flow", "mermaid": DECISION})
    placed = scene.layout(flow)
    _same(
        placed["positions"],
        {"H": [0.0, 0], "R": [0.0, 1], "C": [-0.5, 2], "F": [0.5, 2]},
    )
    _same(placed["back"], ["F->F"])


def test_layout_turns_lr_and_lays_out_sequences_and_states() -> None:
    """LR swaps axes; actors take columns, messages rows, items one row."""
    lr = scene.normalise({"kind": "flow", "mermaid": "flowchart LR\n  A -->|x| B"})
    _same(scene.layout(lr)["positions"], {"A": [0, 0.0], "B": [1, 0.0]})
    seq = scene.normalise(
        {
            "kind": "sequence",
            "actors": [{"id": "page"}, {"id": "server"}],
            "messages": [{"from": "page", "to": "server", "label": "GET"}],
        }
    )
    _same(
        scene.layout(seq),
        {
            "positions": {"page": [0, 0], "server": [1, 0]},
            "rows": {"m1": 1},
            "back": [],
        },
    )
    items = scene.normalise({"kind": "state", "items": [{"id": "a"}, {"id": "b"}]})
    _same(scene.layout(items)["positions"], {"a": [0, 0], "b": [1, 0]})


def test_plan_adds_ends_with_links_lights_branches_and_moves_markers() -> None:
    """An edge brings its ends; a branch dims its rivals; focus dims the rest."""
    flow = scene.normalise({"kind": "flow", "mermaid": DECISION})
    beats: list[dict[str, Any]] = [
        {"say": "a", "add": ["H->R"]},
        {"say": "b", "add": ["R->C", "R->F"]},
        {"say": "c", "branch": "R->C"},
        {"say": "d", "add": ["F->F"], "focus": ["F"], "move": ["F->F"]},
    ]
    steps = scene.plan(flow, beats, [4.0] * 4)
    _same(
        [step["add"] for step in steps],
        [["H", "H->R", "R"], ["R->C", "C", "R->F", "F"], [], ["F->F"]],
    )
    _same(steps[2]["bright"], ["R", "R->C", "C"])
    _same(steps[2]["dim"], ["R->F", "F"])
    _same(steps[2]["lit"], "R->C")
    _same(steps[3]["bright"], ["F", "F->F"])
    _same(steps[3]["dim"], ["H", "H->R", "R", "R->C", "C", "R->F"])
    _same(steps[3]["path"], ["F->F"])
    _same(steps[1]["dim"], [])


def test_plan_keeps_pointers_and_slots_shared_items() -> None:
    """Pointers stay where they last moved; two on one item sit side by side."""
    state = scene.normalise({"kind": "state", "items": [{"id": "a"}, {"id": "b"}]})
    beats = [
        {"say": "a", "add": ["a", "b"], "move": {"cursor": "a", "end": "b"}},
        {"say": "b", "move": {"cursor": "b"}},
    ]
    steps = scene.plan(state, beats, [3.0, 3.0])
    _same(steps[0]["pointers"], {"cursor": ["a", 0.0], "end": ["b", 0.0]})
    _same(steps[1]["pointers"], {"cursor": ["b", -0.5], "end": ["b", 0.5]})
    _same(steps[1]["moved"], ["cursor"])


@pytest.mark.parametrize(
    ("seconds", "adds", "moves"), [(5.0, 8, 2), (1.0, 3, 3), (6.0, 0, 0)]
)
def test_timing_fits_inside_the_beat(seconds: float, adds: int, moves: int) -> None:
    """The phases never outlast the narration they play under."""
    phases = scene.timing(seconds, adds=adds, moves=moves)
    _same(sum(phases.values()), pytest.approx(seconds))
    if not (phases["add"] <= seconds * 0.4):
        pytest.fail("phases['add'] <= seconds * 0.4")
    if not (min(phases.values()) >= 0):
        pytest.fail("min(phases.values()) >= 0")
