"""A chapter's persistent scene: one diagram that changes with the beats.

A scene is a `flow` (nodes and labelled edges, or a Mermaid flowchart), a
`sequence` (actors and messages) or a `state` (items and moving pointers).
Each beat may `add`, `focus`, `move` and `branch`; `plan` turns those into
what the Manim scene animates, beat by beat.
"""

from __future__ import annotations

import re
import textwrap
from itertools import pairwise
from typing import Any

from grokcheck import diagrams

KINDS = ("flow", "sequence", "state")
CHANGES = ("add", "focus", "move", "branch")
MAX_CODE_LINES = 5
MAX_UNSCENED_BEATS = 3
MAX_NODES = diagrams.MAX_NODES
LABEL_WIDTH = 18
EDGE_LABEL_WIDTH = 18
_DIRECTIONS = {"TD": "TD", "TB": "TD", "BT": "TD", "LR": "LR", "RL": "LR"}
_NODE = re.compile(
    r"(?P<id>[A-Za-z_][\w.]*)\s*"
    r"(?P<open>\[\(|\(\[|\[\[|\{\{|\(\(|\[|\{|\()"
    r"(?P<label>\"[^\"]*\"|[^\]\)\}]*)"
    r"(?:\)\]|\]\)|\]\]|\}\}|\)\)|\]|\}|\))"
)
_PIPE_LABEL = re.compile(r"\|[^|]*\|")
_ADD_SECONDS = 0.45
_ADD_SHARE = 0.4
_MOVE_SECONDS = 1.2
_MOVE_SHARE = 0.45
_SETTLE_SECONDS = 0.4


def parse_mermaid(text: str) -> dict[str, Any]:
    """Read the Mermaid flowchart subset `A[label] -->|label| B{label}` as a flow.

    `{...}` marks a decision node; other shapes are plain boxes. A node with no
    label shows its id.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    header = lines[0].split() if lines else []
    if not header or header[0] not in {"flowchart", "graph"}:
        msg = "a Mermaid scene must start with `flowchart` or `graph`"
        raise ValueError(msg)
    direction = _DIRECTIONS.get(header[1] if len(header) > 1 else "TD", "TD")
    nodes: dict[str, dict[str, Any]] = {}
    for line in lines[1:]:
        for match in _NODE.finditer(_PIPE_LABEL.sub("", line)):
            nodes[match["id"]] = {
                "id": match["id"],
                "label": match["label"].strip().strip("\"'`"),
                "decision": match["open"] == "{",
            }
    edges = [
        {"from": edge.src, "to": edge.dst, "label": edge.label}
        for edge in diagrams.edges(text)
    ]
    for ident in (end for edge in edges for end in (edge["from"], edge["to"])):
        nodes.setdefault(ident, {"id": ident, "label": ident, "decision": False})
    return {
        "kind": "flow",
        "direction": direction,
        "nodes": [*nodes.values()],
        "edges": edges,
    }


def normalise(scene: dict[str, Any]) -> dict[str, Any]:
    """Fill a scene's defaults: Mermaid parsed, labels wrapped, message ids `m1`..."""
    if scene.get("kind") == "flow" and "mermaid" in scene:
        scene = parse_mermaid(scene["mermaid"])
    kind = scene.get("kind")
    if kind == "flow":
        return {
            "kind": "flow",
            "direction": _DIRECTIONS.get(scene.get("direction", "TD"), "TD"),
            "nodes": [
                _box(node) | {"decision": bool(node.get("decision"))}
                for node in scene.get("nodes", [])
            ],
            "edges": [
                {
                    "id": f"{edge['from']}->{edge['to']}",
                    "from": edge["from"],
                    "to": edge["to"],
                    "label": _wrap(edge.get("label", ""), EDGE_LABEL_WIDTH),
                }
                for edge in scene.get("edges", [])
            ],
        }
    if kind == "sequence":
        return {
            "kind": "sequence",
            "actors": [_box(actor) for actor in scene.get("actors", [])],
            "messages": [
                {
                    "id": message.get("id", f"m{number}"),
                    "from": message["from"],
                    "to": message["to"],
                    "label": _wrap(message.get("label", ""), 2 * EDGE_LABEL_WIDTH),
                }
                for number, message in enumerate(scene.get("messages", []), start=1)
            ],
        }
    if kind == "state":
        return {
            "kind": "state",
            "items": [_box(item) for item in scene.get("items", [])],
        }
    return {"kind": kind}


def check_script(
    scene: dict[str, Any] | None, beats: list[dict[str, Any]]
) -> list[str]:
    """Return what is wrong with a script's code beats and its scene."""
    problems = []
    codes = [index for index, beat in enumerate(beats) if beat.get("code")]
    if len(codes) > 1:
        problems.append(
            f"{len(codes)} code beats; keep at most one and draw the rest in the scene"
        )
    problems += [
        f"beats[{index}] shows {lines} lines of code; keep it to {MAX_CODE_LINES}"
        for index in codes
        if (lines := len(str(beats[index].get("show", "")).strip("\n").splitlines()))
        > MAX_CODE_LINES
    ]
    if scene is None:
        if len(beats) > MAX_UNSCENED_BEATS:
            problems.append(
                f"{len(beats)} beats and no scene; give the chapter one `scene`"
                " that changes with the beats"
            )
        return problems
    try:
        normal = normalise(scene)
    except (KeyError, TypeError, ValueError) as error:
        return [*problems, f"scene: {error}"]
    return problems + _scene_problems(normal, beats)


def _scene_problems(scene: dict[str, Any], beats: list[dict[str, Any]]) -> list[str]:
    kind = scene["kind"]
    if kind not in KINDS:
        return [f"scene.kind must be one of {', '.join(KINDS)}"]
    parts, links = _parts(scene)
    problems = []
    if not parts:
        problems.append("the scene has nothing to show")
    if len(parts) > MAX_NODES:
        problems.append(f"the scene has {len(parts)} boxes; keep it to {MAX_NODES}")
    ids = [*parts, *links]
    problems += [
        f"scene id {ident!r} is used twice"
        for ident in set(ids)
        if ids.count(ident) > 1
    ]
    problems += [
        f"scene link {ident} joins an unknown box"
        for ident, (start, end) in links.items()
        if start not in parts or end not in parts
    ]
    shown: set[str] = set()
    for index, beat in enumerate(beats):
        at = f"beats[{index}]"
        added = beat.get("add", [])
        problems += [
            f"{at}.add: unknown id {ident!r}" for ident in added if ident not in ids
        ]
        shown |= _with_ends(added, links)
        problems += [
            f"{at}.focus: {ident!r} is not on screen yet"
            for ident in beat.get("focus", [])
            if ident not in shown
        ]
        problems += _move_problems(scene, beat.get("move"), shown, links, at)
        branch = beat.get("branch")
        if branch is not None:
            problems += _branch_problems(scene, branch, shown, at)
    return problems


def _move_problems(
    scene: dict[str, Any],
    move: object,
    shown: set[str],
    links: dict[str, tuple[str, str]],
    at: str,
) -> list[str]:
    if move is None:
        return []
    if scene["kind"] == "state":
        if not isinstance(move, dict):
            return [f"{at}.move must map pointer names to item ids in a state scene"]
        return [
            f"{at}.move: item {item!r} is not on screen yet"
            for item in move.values()
            if item not in shown
        ]
    if not isinstance(move, list):
        return [f"{at}.move must list the links a marker travels"]
    problems = [
        f"{at}.move: {ident!r} is not a link on screen"
        for ident in move
        if ident not in links or ident not in shown
    ]
    path = [links[ident] for ident in move if ident in links]
    problems += [
        f"{at}.move: {first[0]}->{first[1]} does not lead into {second[0]}->{second[1]}"
        for first, second in pairwise(path)
        if first[1] != second[0]
    ]
    return problems


def _branch_problems(
    scene: dict[str, Any], branch: str, shown: set[str], at: str
) -> list[str]:
    if scene["kind"] != "flow":
        return [f"{at}.branch only applies to a flow scene"]
    edge = next((e for e in scene["edges"] if e["id"] == branch), None)
    if edge is None or branch not in shown:
        return [f"{at}.branch: {branch!r} is not an edge on screen"]
    decision = next(n for n in scene["nodes"] if n["id"] == edge["from"])
    if not decision["decision"]:
        return [f"{at}.branch: {edge['from']} is not a decision node"]
    return []


def plan(
    scene: dict[str, Any], beats: list[dict[str, Any]], seconds: list[float]
) -> list[dict[str, Any]]:
    """Return what changes in each beat of a normalised scene, and when.

    Each step holds `add` (new ids in the order the beat names them, a link's
    new ends around it), `bright` and `dim` (the style of every id on screen),
    `lit` (the taken branch), `path` (links a marker travels), `pointers`
    (`[item, slot]` per pointer after the beat, slots centred on the item),
    `moved` and `timing`. Focus and branch last one beat.
    """
    _, links = _parts(scene)
    shown: list[str] = []
    pointers: dict[str, str] = {}
    steps = []
    for beat, length in zip(beats, seconds, strict=True):
        new = []
        for ident in beat.get("add", []):
            ends = links.get(ident, ())
            order = [ends[0], ident, ends[1]] if ends else [ident]
            new += [i for i in order if i not in shown and i not in new]
        shown += new
        lit = beat.get("branch")
        move = beat.get("move") or []
        path = list(move) if isinstance(move, list) else []
        moved = dict(move) if isinstance(move, dict) else {}
        pointers |= moved
        focus = set(beat.get("focus", []))
        bright = set(focus)
        if focus or lit:
            bright |= {lit, *links[lit]} if lit else set()
            bright |= {i for link in path for i in (link, *links[link])}
        rivals = {
            i
            for link, (start, end) in links.items()
            if lit and start == links[lit][0]
            for i in (link, end)
        }
        steps.append(
            {
                "add": new,
                "bright": [i for i in shown if i in bright],
                "dim": [i for i in shown if i not in bright and (focus or i in rivals)],
                "lit": lit,
                "path": path,
                "pointers": _slots(pointers),
                "moved": sorted(moved),
                "timing": timing(length, adds=len(new), moves=len(path) + len(moved)),
            }
        )
    return steps


def _slots(pointers: dict[str, str]) -> dict[str, list[Any]]:
    slots: dict[str, list[Any]] = {}
    for item in dict.fromkeys(pointers.values()):
        names = sorted(name for name, at in pointers.items() if at == item)
        slots |= {
            name: [item, index - (len(names) - 1) / 2]
            for index, name in enumerate(names)
        }
    return slots


def timing(seconds: float, *, adds: int, moves: int) -> dict[str, float]:
    """Split a beat's `seconds` into settle, add, move and hold phases.

    Adds take at most 40% of the beat and moves at most 45% of what is left,
    so the picture changes while the sentence that names the change is spoken.
    """
    settle = min(_SETTLE_SECONDS, seconds * 0.15)
    add = min(adds * _ADD_SECONDS, seconds * _ADD_SHARE) if adds else 0.0
    move = (
        min(moves * _MOVE_SECONDS, (seconds - settle - add) * _MOVE_SHARE)
        if moves
        else 0.0
    )
    hold = max(seconds - settle - add - move, 0.0)
    return {"settle": settle, "add": add, "move": move, "hold": hold}


def layout(scene: dict[str, Any]) -> dict[str, Any]:
    """Place a normalised scene on a grid of unit cells.

    A flow gets `positions` (column, row per node, rows growing down) from
    its longest-path layers, children centred under their parents, and `back`,
    the edges that close a loop. A sequence places actors in columns and
    messages in rows; a state places items in one row.
    """
    if scene["kind"] == "sequence":
        return {
            "positions": {
                actor["id"]: [index, 0] for index, actor in enumerate(scene["actors"])
            },
            "rows": {
                message["id"]: index + 1
                for index, message in enumerate(scene["messages"])
            },
            "back": [],
        }
    if scene["kind"] == "state":
        return {
            "positions": {
                item["id"]: [index, 0] for index, item in enumerate(scene["items"])
            },
            "back": [],
        }
    ids = [node["id"] for node in scene["nodes"]]
    back = _back_edges(ids, scene["edges"])
    forward = [e for e in scene["edges"] if e["id"] not in back]
    layer = dict.fromkeys(ids, 0)
    for _ in ids:
        for edge in forward:
            layer[edge["to"]] = max(layer[edge["to"]], layer[edge["from"]] + 1)
    across: dict[str, float] = {}
    for depth in range(max(layer.values()) + 1):
        row = [ident for ident in ids if layer[ident] == depth]
        desired = [
            _mean(
                [across[e["from"]] for e in forward if e["to"] == ident],
                index - (len(row) - 1) / 2,
            )
            for index, ident in enumerate(row)
        ]
        order = sorted(range(len(row)), key=lambda i: desired[i])
        placed: list[float] = []
        for i in order:
            placed.append(max(desired[i], placed[-1] + 1) if placed else desired[i])
        shift = sum(desired) / len(row) - sum(placed) / len(row)
        across |= {
            row[i]: value + shift for i, value in zip(order, placed, strict=True)
        }
    positions = {
        ident: [across[ident], layer[ident]]
        if scene["direction"] == "TD"
        else [layer[ident], across[ident]]
        for ident in ids
    }
    return {"positions": positions, "back": sorted(back)}


def caption(show: str | None) -> list[str]:
    """Wrap a scene beat's `show` text as the caption under the scene."""
    return _wrap(show or "", 60) if show and show.strip() else []


def _back_edges(ids: list[str], edges: list[dict[str, Any]]) -> set[str]:
    targets = {e["to"] for e in edges}
    roots = [ident for ident in ids if ident not in targets] or ids[:1]
    back: set[str] = set()
    done: set[str] = set()

    def visit(ident: str, stack: list[str]) -> None:
        done.add(ident)
        for edge in (e for e in edges if e["from"] == ident):
            if edge["to"] in stack or edge["to"] == ident:
                back.add(edge["id"])
            elif edge["to"] not in done:
                visit(edge["to"], [*stack, ident])

    for root in [*roots, *ids]:
        if root not in done:
            visit(root, [])
    return back


def _parts(scene: dict[str, Any]) -> tuple[list[str], dict[str, tuple[str, str]]]:
    kind = scene["kind"]
    if kind == "flow":
        boxes = [node["id"] for node in scene["nodes"]]
        joins = scene["edges"]
    elif kind == "sequence":
        boxes = [actor["id"] for actor in scene["actors"]]
        joins = scene["messages"]
    elif kind == "state":
        return [item["id"] for item in scene["items"]], {}
    else:
        return [], {}
    return boxes, {join["id"]: (join["from"], join["to"]) for join in joins}


def _with_ends(added: list[str], links: dict[str, tuple[str, str]]) -> set[str]:
    return {*added, *(end for ident in added if ident in links for end in links[ident])}


def _box(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "label": _wrap(str(raw.get("label") or raw["id"]), LABEL_WIDTH),
    }


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(
        " ".join(text.split()), width, break_long_words=False, break_on_hyphens=False
    )


def _mean(values: list[float], fallback: float) -> float:
    return sum(values) / len(values) if values else fallback
