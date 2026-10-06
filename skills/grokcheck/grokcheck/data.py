"""Datasets under `.grokcheck/data/`: rows recorded from real runs, and view items.

`record` runs a driver once per matrix combination, each in a fresh child
process of the chosen interpreter, and keeps every row the driver `emit`s with
the cited lines that run executed. `load` refuses a dataset whose rows did not
come from the cited code running. `derive` turns rows into the items a view
draws.
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import platform
import re
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grokcheck import git
from grokcheck.selector import LOGIC, matches, problems, same
from grokcheck.trace import record as trace_record

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

FORMAT = 1
MAX_ROWS = 5000
MAX_BYTES = 2 * 1024 * 1024
MAX_COMBINATIONS = 200
DEFAULT_TIMEOUT = 60.0
KINDS = ("run", "trace", "authored", "wrong")

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_TYPES = ("int", "float", "str", "bool")
_SKILL_DIR = Path(__file__).resolve().parent.parent
_BOOT = (
    "import sys; sys.path[0] = sys.argv[1]; "
    "from grokcheck.data import run_child; run_child(sys.argv[2])"
)
_TAIL_LINES = 12


class DataError(Exception):
    """A dataset was refused; `problems` lists `(path, message)` pairs."""

    def __init__(self, found: Iterable[tuple[str, str]]) -> None:
        """Keep the problems and render them one per line as the message."""
        self.problems = list(found)
        super().__init__("\n".join(f"{p}: {m}" if p else m for p, m in self.problems))


@dataclass(frozen=True)
class Run:
    """One driver run: its inputs, its rows `[start, end)` and the lines it ran."""

    inputs: dict[str, object]
    rows: tuple[int, int]
    cited_lines: int
    lines_run: dict[str, tuple[tuple[int, int], ...]]


@dataclass(frozen=True)
class Dataset:
    """A loaded dataset; a `wrong` copy's edited rows carry `_edited: True`."""

    id: str
    fields: dict[str, str]
    rows: tuple[dict[str, object], ...]
    runs: tuple[Run, ...]
    source: dict[str, object]
    recorded_at: str

    @property
    def kind(self) -> str:
        """The `source.kind`: run, trace, authored or wrong."""
        return str(self.source["kind"])


@dataclass
class _Checked:
    found: list[tuple[str, str]] = field(default_factory=list)

    def add(self, path: str, message: str) -> None:
        self.found.append((path, message))


def data_path(project_root: Path, dataset_id: str) -> Path:
    """Return where dataset `dataset_id` lives in the project."""
    return project_root / ".grokcheck" / "data" / f"{dataset_id}.json"


def load(project_root: Path, dataset_id: str) -> Dataset:
    """Read and check `dataset_id`; raise `DataError` under any refusal rule."""
    if not _ID.fullmatch(dataset_id):
        raise DataError([("", f"'{dataset_id}' is not a valid dataset id")])
    path = data_path(project_root, dataset_id)
    if not path.is_file():
        raise DataError([("", f"no dataset '{dataset_id}' at {path}")])
    if path.stat().st_size > MAX_BYTES:
        raise DataError([("", f"{path} is over {MAX_BYTES} bytes")])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataError([("", f"{path} is not valid JSON: {error}")]) from error
    return _parse(raw, project_root, dataset_id)


def _parse(raw: object, project_root: Path, dataset_id: str) -> Dataset:  # noqa: C901
    check = _Checked()
    if not isinstance(raw, dict):
        raise DataError([("", "must be an object")])
    if raw.get("format") != FORMAT:
        check.add("format", f"must be {FORMAT}")
    if raw.get("id") != dataset_id:
        check.add("id", f"must be '{dataset_id}', the file name")
    fields = _fields(raw.get("fields"), check)
    source = raw.get("source")
    if not isinstance(source, dict) or source.get("kind") not in KINDS:
        check.add("source.kind", f"must be one of {', '.join(KINDS)}")
        raise DataError(check.found)
    kind = source["kind"]
    rows = _rows(raw.get("rows"), fields, check, cited=kind == "authored")
    runs = _runs(raw.get("runs", []), len(rows), check)
    if not isinstance(raw.get("recorded_at"), str):
        check.add("recorded_at", "must be a string")
    if check.found:
        raise DataError(check.found)
    if kind == "run":
        _check_run(source, runs, check)
    elif kind == "trace":
        _check_trace(source, project_root, check)
    elif kind == "authored":
        _check_authored(source, rows, project_root, check)
    else:
        rows = _check_wrong(source, rows, project_root, check)
    if check.found:
        raise DataError(check.found)
    return Dataset(
        id=dataset_id,
        fields=fields,
        rows=tuple({k: v for k, v in row.items() if k != "cite"} for row in rows),
        runs=runs,
        source=source,
        recorded_at=raw["recorded_at"],
    )


def _fields(value: object, check: _Checked) -> dict[str, str]:
    if not isinstance(value, dict):
        check.add("fields", "must be an object")
        return {}
    for name, kind in value.items():
        if not _FIELD.fullmatch(name) or name in LOGIC:
            check.add(f"fields.{name}", "is not a valid field name")
        if kind not in _TYPES:
            check.add(f"fields.{name}", f"must be one of {', '.join(_TYPES)}")
    return dict(value)


def _rows(
    value: object, fields: Mapping[str, str], check: _Checked, *, cited: bool
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        check.add("rows", "must be a list")
        return []
    if len(value) > MAX_ROWS:
        check.add("rows", f"holds {len(value)} rows, at most {MAX_ROWS} allowed")
        return []
    for index, row in enumerate(value):
        where = f"rows[{index}]"
        if not isinstance(row, dict):
            check.add(where, "must be an object")
            continue
        for key, item in row.items():
            if key == "cite" and cited:
                continue
            if key not in fields:
                check.add(f"{where}.{key}", "is not in fields")
            elif not _fits(item, fields[key]):
                check.add(f"{where}.{key}", f"{item!r} is not {fields[key]} or null")
    return [row for row in value if isinstance(row, dict)]


def _fits(value: object, kind: str) -> bool:
    if value is None:
        return True
    return _type_of(value) == kind or (kind == "float" and _type_of(value) == "int")


def _type_of(value: object) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return None


def _runs(value: object, row_count: int, check: _Checked) -> tuple[Run, ...]:
    if not isinstance(value, list):
        check.add("runs", "must be a list")
        return ()
    runs: list[Run] = []
    for index, run in enumerate(value):
        where = f"runs[{index}]"
        try:
            start, end = run["rows"]
            lines_run = {
                str(file): tuple((int(a), int(b)) for a, b in spans)
                for file, spans in run["lines_run"].items()
            }
            parsed = Run(
                dict(run["inputs"]), (start, end), run["cited_lines"], lines_run
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            check.add(
                where, "must hold inputs, rows [start, end], cited_lines, lines_run"
            )
            continue
        if not (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= row_count
        ):
            check.add(f"{where}.rows", f"must be a range inside 0..{row_count}")
        if not isinstance(parsed.cited_lines, int):
            check.add(f"{where}.cited_lines", "must be an integer")
        runs.append(parsed)
    return tuple(runs)


def _check_run(source: Mapping[str, Any], runs: Sequence[Run], check: _Checked) -> None:
    if not source.get("cited_lines_run"):
        check.add(
            "source.cited_lines_run",
            "is 0: no cited line ran, so the rows did not come from the cited code",
        )
    for index, run in enumerate(runs):
        if run.rows[1] > run.rows[0] and not run.cited_lines:
            check.add(
                f"runs[{index}].cited_lines",
                f"is 0 for inputs {run.inputs}, which emitted rows without"
                " running a cited line",
            )


def _check_trace(
    source: Mapping[str, Any], project_root: Path, check: _Checked
) -> None:
    trace = project_root / str(source.get("trace", ""))
    if not source.get("trace") or not trace.is_file():
        check.add("source.trace", f"the trace file '{source.get('trace')}' is missing")
        return
    if sha256(trace) != source.get("trace_sha256"):
        check.add("source.trace_sha256", "the trace file changed since the record")
    events = json.loads(trace.read_text(encoding="utf-8")).get("events", [])
    if not any(event.get("event") == "line" for event in events):
        check.add("source.trace", "the trace has no line event")


def _check_authored(
    source: Mapping[str, Any],
    rows: Sequence[Mapping[str, object]],
    project_root: Path,
    check: _Checked,
) -> None:
    verdicts = source.get("verdicts") or {}
    for index, row in enumerate(rows):
        where = f"rows[{index}]"
        if "cite" not in row:
            check.add(f"{where}.cite", "is required in an authored dataset")
        else:
            for path, message in cite_problems(
                project_root, row["cite"], f"{where}.cite"
            ):
                check.add(path, message)
        if verdicts.get(str(index)) == "contradicted":
            check.add(where, "the grounding pass found this row contradicted")


def cite_problems(project_root: Path, cite: object, path: str) -> list[tuple[str, str]]:
    """Check `cite` as a lines backing of a claim is checked in a lesson."""
    from grokcheck.lesson import _Checker, _lines_backing  # noqa: PLC0415

    root = project_root.resolve()
    checker = _Checker(root, root)
    _lines_backing(checker, cite, path)
    return [(p.path, p.message) for p in checker.problems]


def _check_wrong(
    source: Mapping[str, Any],
    rows: list[dict[str, object]],
    project_root: Path,
    check: _Checked,
) -> list[dict[str, object]]:
    of_id = str(source.get("of", ""))
    try:
        of = load(project_root, of_id)
    except DataError as error:
        check.add("source.of", f"the original '{of_id}' does not load: {error}")
        return rows
    if sha256(data_path(project_root, of_id)) != source.get("of_sha256"):
        check.add("source.of_sha256", f"'{of_id}' changed since this copy was made")
    expected = [_plain(row) for row in of.rows]
    edited: set[int] = set()
    for index, edit in enumerate(source.get("edits") or []):
        try:
            expected[edit["row"]][edit["field"]] = edit["now"]
            edited.add(edit["row"])
        except (KeyError, IndexError, TypeError):
            check.add(f"source.edits[{index}]", "must name a row and a field")
    if not edited:
        check.add("source.edits", "a wrong copy needs at least one edit")
    if [_plain(row) for row in rows] != expected:
        check.add("rows", f"differ from '{of_id}' with the edits applied")
    return [
        {**row, "_edited": True} if index in edited else row
        for index, row in enumerate(rows)
    ]


def _plain(row: Mapping[str, object]) -> dict[str, object]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def sha256(path: Path) -> str:
    """Return the hex sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stale(project_root: Path, dataset: Dataset) -> list[str]:
    """Return the cited files, and the driver, whose sha256 changed since the record."""
    changed = [
        str(cited["file"])
        for cited in _cited(dataset)
        if _sha256_or_none(project_root / str(cited["file"])) != cited.get("sha256")
    ]
    script = dataset.source.get("script")
    if script and _sha256_or_none(project_root / str(script)) != dataset.source.get(
        "script_sha256"
    ):
        changed.append(str(script))
    return changed


def _cited(dataset: Dataset) -> list[dict[str, object]]:
    cited = dataset.source.get("cited")
    return [c for c in cited if isinstance(c, dict)] if isinstance(cited, list) else []


def _sha256_or_none(path: Path) -> str | None:
    return sha256(path) if path.is_file() else None


def provenance(dataset: Dataset) -> dict[str, object]:
    """Return what the page shows about where a dataset came from."""
    return {
        "id": dataset.id,
        "kind": dataset.kind,
        "script": dataset.source.get("script"),
        "trace": dataset.source.get("trace"),
        "head": dataset.source.get("head"),
        "recorded_at": dataset.recorded_at,
        "cited": [c["file"] for c in _cited(dataset)],
        "cited_lines_run": dataset.source.get("cited_lines_run", 0),
    }


def choices(dataset: Dataset, field_name: str) -> list[object]:
    """Return the distinct values of `field_name`: null, booleans, numbers, strings."""
    return _distinct(row.get(field_name) for row in dataset.rows)


def _distinct(values: Iterable[object]) -> list[object]:
    seen: dict[tuple[int, object], object] = {}
    for value in values:
        seen.setdefault(_order(value), value)
    return [seen[key] for key in sorted(seen)]


def _order(value: object) -> tuple[int, object]:
    if value is None:
        return (0, 0)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, int | float):
        return (2, value)
    return (3, str(value))


def _text(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _cell(*parts: object) -> str:
    return "|".join(_text(part) for part in parts)


def derive(  # noqa: PLR0913
    dataset: Dataset,
    layout: str,
    encode: Mapping[str, object],
    *,
    where: Mapping[str, object],
    inputs: Sequence[str],
    split: str | None,
    exclude: Mapping[str, object] | None,
    nodes: Sequence[Mapping[str, object]] = (),
) -> list[dict[str, object]]:
    """Build the full items of a view, in draw order, with their reserved fields."""
    if layout == "decision":
        return _decision_items(dataset, where, split, exclude, nodes)
    blank = dict.fromkeys(dataset.fields)
    rows = [
        (index, {**blank, **row})
        for index, row in enumerate(dataset.rows)
        if matches(where, row) and not (exclude and matches(exclude, row))
    ]
    groups: dict[tuple[str, ...], list[tuple[int, dict[str, object]]]] = {}
    for index, row in rows:
        key = tuple(_text(row.get(name)) for name in inputs)
        groups.setdefault(key, []).append((index, row))
    items: list[dict[str, object]] = []
    lane_order = (
        _lane_order(encode, [row for _, row in rows]) if layout == "lanes" else []
    )
    for group in groups.values():
        panes: dict[str, list[tuple[int, dict[str, object]]]] = {}
        for index, row in group:
            panes.setdefault(_text(row.get(split)) if split else "", []).append(
                (index, row)
            )
        built: list[dict[str, object]] = []
        for pane, pane_rows in panes.items():
            if layout == "lanes":
                built += _lanes(pane_rows, encode, lane_order, split, pane)
            else:
                built += _flat(pane_rows, layout, encode, split, pane)
        if split:
            _mark_differs(built, encode)
        items += built
    return items


def _lane_order(
    encode: Mapping[str, object], rows: Sequence[Mapping[str, object]]
) -> list[object]:
    declared = encode.get("lanes")
    if isinstance(declared, list) and declared:
        return [lane["value"] for lane in declared if isinstance(lane, dict)]
    return list(
        {
            _order(row.get(str(encode["lane"]))): row.get(str(encode["lane"]))
            for row in rows
        }.values()
    )


def _base(
    row: dict[str, object], encode: Mapping[str, object], split: str | None
) -> dict[str, object]:
    item = dict(row)
    item["_link"] = _text(row.get(str(encode.get("link") or encode.get("key"))))
    if split:
        item["_pane"] = row.get(split)
    return item


def _lanes(
    rows: Sequence[tuple[int, dict[str, object]]],
    encode: Mapping[str, object],
    lane_order: Sequence[object],
    split: str | None,
    pane: str,
) -> list[dict[str, object]]:
    lane, x, key = str(encode["lane"]), str(encode["x"]), str(encode["key"])
    prefix = (pane,) if split else ()
    placed: list[tuple[tuple[object, ...], dict[str, object]]] = []
    keys: dict[str, list[tuple[int, dict[str, object]]]] = {}
    for index, row in rows:
        keys.setdefault(_text(row.get(lane)), []).append((index, row))
    for lane_index, lane_value in enumerate(lane_order):
        seen: dict[str, int] = {}
        for index, row in keys.get(_text(lane_value), []):
            item = _base(row, encode, split)
            cell = _cell(*prefix, lane_value, row.get(key))
            seen[cell] = seen.get(cell, 0) + 1
            item |= {
                "_cell": cell,
                "_id": cell if seen[cell] == 1 else f"{cell}#{seen[cell]}",
                "_dup": seen[cell] > 1,
                "_lost": False,
            }
            placed.append(((lane_index, _order(row.get(x)), index, 0), item))
        ref = encode.get("ref_lane")
        if ref is None or _text(ref) == _text(lane_value):
            continue
        present = {_text(row.get(key)) for _, row in keys.get(_text(lane_value), [])}
        for index, row in _first_per_key(keys.get(_text(ref), []), key):
            if _text(row.get(key)) in present:
                continue
            cell = _cell(*prefix, lane_value, row.get(key))
            item = _base({**row, lane: lane_value}, encode, split)
            item |= {"_cell": cell, "_id": cell, "_dup": False, "_lost": True}
            placed.append(((lane_index, _order(row.get(x)), index, 1), item))
    placed.sort(key=lambda pair: pair[0])
    items = [item for _, item in placed]
    columns: dict[tuple[str, tuple[int, object]], int] = {}
    for item in items:
        spot = (_text(item.get(lane)), _order(item.get(x)))
        columns[spot] = columns.get(spot, 0) + 1
    for item in items:
        item["_burst"] = columns[(_text(item.get(lane)), _order(item.get(x)))] > 1
    return items


def _first_per_key(
    rows: Sequence[tuple[int, dict[str, object]]], key: str
) -> list[tuple[int, dict[str, object]]]:
    first: dict[str, tuple[int, dict[str, object]]] = {}
    for index, row in rows:
        first.setdefault(_text(row.get(key)), (index, row))
    return list(first.values())


def _flat(
    rows: Sequence[tuple[int, dict[str, object]]],
    layout: str,
    encode: Mapping[str, object],
    split: str | None,
    pane: str,
) -> list[dict[str, object]]:
    prefix = (pane,) if split else ()
    group = encode.get("group") if layout == "blocks" else None
    items: list[dict[str, object]] = []
    seen: dict[str, int] = {}
    for _, row in rows:
        parts = (*prefix, row.get(str(group))) if group else prefix
        cell = _cell(*parts, row.get(str(encode["key"])))
        seen[cell] = seen.get(cell, 0) + 1
        if seen[cell] > 1 and layout in ("table", "blocks"):
            raise DataError([("", f"two rows of one {layout} share the cell '{cell}'")])
        item = _base(row, encode, split)
        item["_cell"] = cell
        item["_id"] = cell if seen[cell] == 1 else f"{cell}#{seen[cell]}"
        items.append(item)
    if group:
        order = list(dict.fromkeys(_text(item.get(str(group))) for item in items))
        items.sort(key=lambda item: order.index(_text(item.get(str(group)))))
    return items


def _mark_differs(items: list[dict[str, object]], encode: Mapping[str, object]) -> None:
    panes = list(dict.fromkeys(_text(item["_pane"]) for item in items))
    fields = [str(encode[name]) for name in ("label", "class", "x") if encode.get(name)]
    signatures: dict[str, set[tuple[object, ...]]] = {pane: set() for pane in panes}
    for item in items:
        signatures[_text(item["_pane"])].add(_signature(item, fields))
    for item in items:
        mine = _signature(item, fields)
        item["_differs"] = any(
            mine not in signatures[pane]
            for pane in panes
            if pane != _text(item["_pane"])
        )


def _signature(item: Mapping[str, object], fields: Sequence[str]) -> tuple[object, ...]:
    cell = str(item["_cell"]).split("|", 1)[1]
    occurrence = str(item["_id"]).rpartition("#")[2] if "#" in str(item["_id"]) else "1"
    values = tuple(_order(item.get(name)) for name in fields)
    return (cell, occurrence, *values, item.get("_lost"))


def _decision_items(
    dataset: Dataset,
    where: Mapping[str, object],
    split: str | None,
    exclude: Mapping[str, object] | None,
    nodes: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    cited = {str(c["file"]) for c in _cited(dataset)}
    for node in nodes:
        file = str(_code(node).get("file"))
        if file not in cited:
            msg = f"node '{node.get('id')}' cites '{file}', not a cited file"
            raise DataError([("", f"{msg} of '{dataset.id}'")])
    items: list[dict[str, object]] = []
    for run in dataset.runs:
        if not matches(where, run.inputs) or (exclude and matches(exclude, run.inputs)):
            continue
        prefix = (run.inputs.get(split),) if split else ()
        for node in nodes:
            code = _code(node)
            ran = run.lines_run.get(str(code.get("file")), ())
            yes = node.get("yes")
            answer = None
            if node.get("kind") == "check" and isinstance(yes, dict):
                answer = "yes" if _ran(ran, yes.get("lines")) else "no"
            item: dict[str, object] = {
                **run.inputs,
                "node": node.get("id"),
                "kind": node.get("kind"),
                "label": node.get("label"),
                "answer": answer,
                "_taken": _ran(ran, code.get("lines")),
                "_link": _text(node.get("id")),
                "_cell": _cell(*prefix, node.get("id")),
            }
            item["_id"] = item["_cell"]
            if split:
                item["_pane"] = run.inputs.get(split)
            items.append(item)
    return items


def _code(node: Mapping[str, object]) -> Mapping[str, object]:
    code = node.get("code")
    return code if isinstance(code, dict) else {}


def _ran(ran: Sequence[tuple[int, int]], lines: object) -> bool:
    if not isinstance(lines, list | tuple) or len(lines) != 2:  # noqa: PLR2004
        return False
    first, last = lines
    return any(a <= last and first <= b for a, b in ran)


def parse_matrix(specs: Sequence[str]) -> dict[str, list[object]]:
    """Parse `name=a..b` (inclusive ints) and `name=x,y` (ints when all parse)."""
    matrix: dict[str, list[object]] = {}
    for spec in specs:
        name, _, values = spec.partition("=")
        if not _FIELD.fullmatch(name) or name in LOGIC:
            raise DataError([("--matrix", f"'{name}' is not a valid field name")])
        if name in matrix:
            raise DataError([("--matrix", f"'{name}' is given twice")])
        low, dots, high = values.partition("..")
        if dots:
            try:
                matrix[name] = list(range(int(low), int(high) + 1))
            except ValueError:
                raise DataError(
                    [("--matrix", f"'{spec}' needs integer bounds")]
                ) from None
        else:
            parts = values.split(",")
            try:
                matrix[name] = [int(part) for part in parts]
            except ValueError:
                matrix[name] = list(parts)
        if not matrix[name] or matrix[name] == [""]:
            raise DataError([("--matrix", f"'{spec}' has no values")])
    return matrix


def record(  # noqa: PLR0913
    project_root: Path,
    driver: Path,
    dataset_id: str,
    cite: Sequence[str],
    *,
    matrix: Mapping[str, Sequence[object]],
    entry: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    python: str | None = None,
) -> dict[str, Any]:
    """Run `driver` for every matrix combination and write the dataset.

    Each run is a fresh `python` process (default: this interpreter) that
    imports the recorder from this skill, so `python` needs no install of it.
    """
    root = project_root.resolve()
    _check_id(dataset_id)
    script = driver.resolve()
    if not script.is_file():
        raise DataError([("driver", f"'{driver}' is not a file")])
    cited = [_relative(root, root / c, "--cite") for c in cite]
    if not cited:
        raise DataError([("--cite", "name at least one cited file")])
    combos = [
        dict(zip(matrix, values, strict=True))
        for values in itertools.product(*matrix.values())
    ]
    if len(combos) > MAX_COMBINATIONS:
        raise DataError(
            [("--matrix", f"{len(combos)} combinations, at most {MAX_COMBINATIONS}")]
        )
    rows: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []
    union: set[tuple[str, int]] = set()
    version = ""
    for inputs in combos:
        result = _run_once(root, script, cited, inputs, entry, timeout, python)
        lines = {
            file: sorted(numbers)
            for file, numbers in result["lines"].items()
            if numbers
        }
        union |= {(file, n) for file, numbers in lines.items() for n in numbers}
        runs.append(
            {
                "inputs": inputs,
                "rows": [len(rows), len(rows) + len(result["rows"])],
                "cited_lines": sum(len(n) for n in lines.values()),
                "lines_run": {file: _ranges(n) for file, n in lines.items()},
            }
        )
        rows += result["rows"]
        version = result["python"]
    raw = {
        "format": FORMAT,
        "id": dataset_id,
        "fields": _infer_fields(rows),
        "rows": rows,
        "runs": runs,
        "source": {
            "kind": "run",
            "script": _relative(root, script, None),
            "script_sha256": sha256(script),
            **_git_state(root, cited),
            "cited": [{"file": f, "sha256": sha256(root / f)} for f in cited],
            "cited_lines_run": len(union),
            "matrix": {name: list(values) for name, values in matrix.items()},
            "python": version,
        },
        "recorded_at": _now(),
    }
    return _write(root, raw)


def _run_once(  # noqa: PLR0913, PLR0917
    root: Path,
    script: Path,
    cited: Sequence[str],
    inputs: dict[str, object],
    entry: str | None,
    timeout: float,
    python: str | None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="grokcheck-record-") as scratch:
        request = Path(scratch) / "request.json"
        out = Path(scratch) / "result.json"
        request.write_text(
            json.dumps(
                {
                    "driver": str(script),
                    "project": str(root),
                    "cited": list(cited),
                    "inputs": inputs,
                    "entry": entry,
                    "out": str(out),
                }
            ),
            encoding="utf-8",
        )
        command = [python or sys.executable, "-c", _BOOT, str(_SKILL_DIR)]
        command.append(str(request))
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                cwd=root,
                stdout=2,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise DataError(
                [("", f"the driver ran past {timeout:g}s with inputs {inputs}")]
            ) from None
        if not out.is_file():
            status = completed.returncode
            msg = f"the recorder exited with status {status} before writing a result"
            raise DataError([("", f"{msg}, with inputs {inputs}; see its stderr")])
        result: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    if "error" in result:
        msg = f"the driver raised {result['error']} with inputs {inputs}"
        raise DataError([("", f"{msg}\n{result['traceback']}")])
    return result


def run_child(request_path: str) -> None:
    """Run one driver under the recorder; the `record` child process calls this."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    driver = Path(request["driver"])
    inputs: dict[str, object] = request["inputs"]
    cited = {str(Path(request["project"]) / rel): rel for rel in request["cited"]}
    sys.path[0] = str(driver.parent)
    sys.argv = [str(driver)]
    rows: list[dict[str, object]] = []

    def emit(**row: object) -> None:
        rows.append(_emitted(row, inputs))

    try:
        with contextlib.redirect_stdout(sys.stderr):
            trace = trace_record(
                driver,
                {Path(path) for path in cited},
                request["entry"],
                max_events=0,
                init_globals={"emit": emit, "INPUTS": dict(inputs)},
            )
    except Exception as error:  # noqa: BLE001
        tail = traceback.format_exc().splitlines()[-_TAIL_LINES:]
        result: dict[str, object] = {"error": repr(error), "traceback": "\n".join(tail)}
    else:
        result = {
            "rows": rows,
            "lines": {
                cited[file]: sorted(lines) for file, lines in trace.lines_run.items()
            },
            "python": platform.python_version(),
        }
    Path(request["out"]).write_text(json.dumps(result), encoding="utf-8")


def _emitted(
    row: Mapping[str, object], inputs: Mapping[str, object]
) -> dict[str, object]:
    for key, value in row.items():
        if not _FIELD.fullmatch(key) or key in LOGIC:
            msg = f"emit: '{key}' is not a valid field name"
            raise ValueError(msg)
        if value is not None and _type_of(value) is None:
            msg = f"emit: {key}={value!r} is not a string, number, boolean or None"
            raise TypeError(msg)
        if key in inputs and not same(value, inputs[key]):
            msg = f"emit: {key}={value!r} differs from this run's input {inputs[key]!r}"
            raise ValueError(msg)
    return {**inputs, **row}


def _infer_fields(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    kinds: dict[str, set[str]] = {}
    for row in rows:
        for key, value in row.items():
            if key == "cite":
                continue
            found = kinds.setdefault(key, set())
            if value is not None:
                found.add(_type_of(value) or "?")
    fields: dict[str, str] = {}
    for key, found in kinds.items():
        if found == {"int", "float"}:
            fields[key] = "float"
        elif len(found) > 1 or "?" in found:
            raise DataError([(f"fields.{key}", f"mixes {', '.join(sorted(found))}")])
        else:
            fields[key] = next(iter(found), "str")
    return fields


def _ranges(numbers: Sequence[int]) -> list[list[int]]:
    spans: list[list[int]] = []
    for number in numbers:
        if spans and spans[-1][1] + 1 == number:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return spans


def _git_state(root: Path, cited: Sequence[str]) -> dict[str, object]:
    head = (git.output(root, "rev-parse", "HEAD") or "").strip() or None
    if head is None or not cited:
        return {"head": head, "dirty": []}
    changed = git.output(
        root, "diff", "--name-only", "--relative", "HEAD", "--", *cited
    )
    untracked = git.output(root, "ls-files", "--others", "--", *cited)
    dirty = set(git.lines(changed)) | set(git.lines(untracked))
    return {"head": head, "dirty": [f for f in cited if f in dirty]}


def _relative(root: Path, path: Path, option: str | None) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(root):
        relative = resolved.relative_to(root).as_posix()
        if option is None or resolved.is_file():
            return relative
    elif option is None:
        return str(resolved)
    raise DataError([(option, f"'{path}' is not a file inside the project")])


def _check_id(dataset_id: str) -> None:
    if not _ID.fullmatch(dataset_id):
        msg = "must start with a letter or digit and hold only letters, digits, _, -"
        raise DataError([("--id", f"'{dataset_id}' {msg}")])


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(root: Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Check `raw` by the load rules, then write it and summarise it."""
    dataset = _parse(raw, root, raw["id"])
    text = json.dumps(raw, indent=1, ensure_ascii=False) + "\n"
    if len(text.encode()) > MAX_BYTES:
        raise DataError([("", f"the dataset is over {MAX_BYTES} bytes")])
    path = data_path(root, raw["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "id": dataset.id,
        "path": str(path),
        "rows": len(dataset.rows),
        "runs": len(dataset.runs),
        "fields": dataset.fields,
        "cited_lines_run": dataset.source.get("cited_lines_run", 0),
    }


def from_trace(project_root: Path, trace_file: Path, dataset_id: str) -> dict[str, Any]:
    """Flatten a `trace record` file into rows `{seq, event, task, file, line, l_*}`."""
    root = project_root.resolve()
    _check_id(dataset_id)
    trace = trace_file.resolve()
    events = json.loads(trace.read_text(encoding="utf-8")).get("events", [])
    rows: list[dict[str, object]] = []
    lines: dict[str, set[int]] = {}
    for event in events:
        file = _project_file(root, event.get("file"))
        row: dict[str, object] = {
            "seq": event["seq"],
            "event": event["event"],
            "task": event.get("task"),
            "file": file,
            "line": event.get("line"),
        }
        for name, value in (event.get("locals") or {}).items():
            if _FIELD.fullmatch(f"l_{name}"):
                row[f"l_{name}"] = value if isinstance(value, str) else repr(value)
        rows.append(row)
        if event["event"] == "line" and file and event.get("line"):
            lines.setdefault(file, set()).add(event["line"])
    if not lines:
        raise DataError([("", f"{trace_file} has no line event inside the project")])
    count = sum(len(n) for n in lines.values())
    raw = {
        "format": FORMAT,
        "id": dataset_id,
        "fields": _infer_fields(rows),
        "rows": rows,
        "runs": [
            {
                "inputs": {},
                "rows": [0, len(rows)],
                "cited_lines": count,
                "lines_run": {f: _ranges(sorted(n)) for f, n in lines.items()},
            }
        ],
        "source": {
            "kind": "trace",
            "trace": _relative(root, trace, None),
            "trace_sha256": sha256(trace),
            **_git_state(root, sorted(lines)),
            "cited": [{"file": f, "sha256": sha256(root / f)} for f in sorted(lines)],
            "cited_lines_run": count,
        },
        "recorded_at": _now(),
    }
    return _write(root, raw)


def _project_file(root: Path, file: object) -> str | None:
    if not isinstance(file, str):
        return None
    resolved = (root / file).resolve()
    return (
        resolved.relative_to(root).as_posix()
        if resolved.is_relative_to(root) and resolved.is_file()
        else None
    )


def author(
    project_root: Path, dataset_id: str, rows_file: Path, reason: str
) -> dict[str, Any]:
    """Write rows typed by hand, each with a `cite` checked like a lines backing."""
    root = project_root.resolve()
    _check_id(dataset_id)
    rows = json.loads(rows_file.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise DataError([("--file", "must hold a JSON list of row objects")])
    if not reason.strip():
        raise DataError(
            [("--reason", "say why the rows are typed rather than recorded")]
        )
    files = sorted(
        {
            str(r["cite"]["file"])
            for r in rows
            if isinstance(r.get("cite"), dict) and "file" in r["cite"]
        }
    )
    raw = {
        "format": FORMAT,
        "id": dataset_id,
        "fields": _infer_fields(rows),
        "rows": rows,
        "runs": [],
        "source": {
            "kind": "authored",
            "reason": reason,
            "verdicts": {},
            **_git_state(root, files),
            "cited": [{"file": f, "sha256": _sha256_or_none(root / f)} for f in files],
        },
        "recorded_at": _now(),
    }
    return _write(root, raw)


def wrong(
    project_root: Path, of_id: str, out_id: str, edits: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    """Copy `of_id` to `out_id` with each `(selector, field=value)` edit applied."""
    root = project_root.resolve()
    _check_id(out_id)
    of = load(root, of_id)
    rows = [_plain(row) for row in of.rows]
    inputs = {name for run in of.runs for name in run.inputs}
    applied: list[dict[str, object]] = []
    for selector_text, assignment in edits:
        try:
            selector = json.loads(selector_text)
        except json.JSONDecodeError as error:
            raise DataError(
                [("--edit", f"'{selector_text}' is not JSON: {error}")]
            ) from error
        if found := problems(selector, of.fields):
            raise DataError([(f"--edit {p}".strip(), m) for p, m in found])
        hits = [index for index, row in enumerate(rows) if matches(selector, row)]
        if len(hits) != 1:
            raise DataError(
                [
                    (
                        "--edit",
                        f"{selector_text} matches {len(hits)} rows, not exactly one",
                    )
                ]
            )
        name, equals, text = assignment.partition("=")
        if not equals or name not in of.fields:
            raise DataError(
                [
                    (
                        "--edit",
                        f"'{assignment}' must be field=value with a field of '{of_id}'",
                    )
                ]
            )
        if name in inputs:
            raise DataError(
                [("--edit", f"'{name}' is a matrix input and cannot be edited")]
            )
        try:
            value: object = json.loads(text)
        except json.JSONDecodeError:
            value = text
        if not _fits(value, of.fields[name]):
            raise DataError([("--edit", f"{value!r} is not {of.fields[name]} or null")])
        row = rows[hits[0]]
        applied.append(
            {"row": hits[0], "field": name, "was": row.get(name), "now": value}
        )
        row[name] = value
    if not applied:
        raise DataError([("--edit", "give at least one edit")])
    original = json.loads(data_path(root, of_id).read_text(encoding="utf-8"))
    raw = {
        "format": FORMAT,
        "id": out_id,
        "fields": of.fields,
        "rows": rows,
        "runs": original.get("runs", []),
        "source": {
            "kind": "wrong",
            "of": of_id,
            "of_sha256": sha256(data_path(root, of_id)),
            "edits": applied,
        },
        "recorded_at": _now(),
    }
    return _write(root, raw)


def show(
    project_root: Path, dataset_id: str, where: Mapping[str, object], limit: int
) -> dict[str, Any]:
    """Summarise a dataset: counts, input values, sample `where` rows, staleness."""
    dataset = load(project_root, dataset_id)
    if found := problems(where, dataset.fields):
        raise DataError([(f"--where {p}".strip(), m) for p, m in found])
    picked = [_plain(row) for row in dataset.rows if matches(where, row)]
    names = list(dict.fromkeys(name for run in dataset.runs for name in run.inputs))
    return {
        "id": dataset.id,
        "kind": dataset.kind,
        "fields": dataset.fields,
        "rows": len(picked),
        "runs": len(dataset.runs),
        "inputs": {
            name: _distinct(run.inputs.get(name) for run in dataset.runs)
            for name in names
        },
        "sample": picked[:limit],
        "stale": stale(project_root, dataset),
    }
