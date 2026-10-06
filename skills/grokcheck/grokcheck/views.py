"""The `view` element and its questions: parsing, masking and public payloads.

Items come from `data.derive`; this module checks a view against its dataset,
builds the placeholders a gated view shows, and computes the answer keys of
`select_items` and `fill_table`.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

from grokcheck import data, selector
from grokcheck.lesson import (
    _LANGUAGES,
    _VIEW_OPTIONAL,
    KIND_COLOURS,
    LAYOUTS,
    DataBacking,
    DatasetUse,
    DecisionNode,
    FillTable,
    Kind,
    Layer,
    Layout,
    Mark,
    PredictState,
    SelectItems,
    Span,
    StepCode,
    TableBlank,
    ViewElement,
    ViewInput,
    ViewNote,
    ViewPreset,
    ViewStep,
    ViewTask,
    _at,
    _Checker,
    _ElementStem,
    _file_code,
    _index,
    _line_span,
    _objects,
    _read_project_file,
    _Stem,
    _without_secrets,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from grokcheck.lesson import Control, NodeKind, Place, Question, Section

MAX_ITEMS = 5000
MAX_VIEW_BYTES = 512 * 1024
MAX_LESSON_VIEW_BYTES = 4 * 1024 * 1024
MAX_STEP_LINES = 6
MAX_STEP_GAP = 40
STEP_CONTEXT = 2
TONES = ("ok", "warn", "bad", "accent", "muted")
CONTROLS = ("range", "toggle", "select")

_RESERVED = frozenset({"_id", "_cell", "_link", "_pane", "_differs", "_edited"})
_LAYOUT_RESERVED: dict[str, frozenset[str]] = {
    "lanes": frozenset({"_lost", "_dup", "_burst"}),
    "decision": frozenset({"_taken", "node", "kind", "answer"}),
}
_ENCODE: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "lanes": (
        frozenset({"lane", "x", "key", "label"}),
        frozenset({"class", "ref_lane", "lanes", "link"}),
    ),
    "table": (frozenset({"columns", "key"}), frozenset({"class", "link"})),
    "steps": (frozenset({"file", "line", "key"}), frozenset({"show", "link"})),
    "blocks": (
        frozenset({"group", "key", "label"}),
        frozenset({"class", "group_label", "link"}),
    ),
    "decision": (frozenset(), frozenset()),
}
_FIELD_KEYS: tuple[str, ...] = ("lane", "x", "key", "label", "class", "link")
_FIELD_KEYS += ("file", "line", "group", "group_label")
_NULLABLE = frozenset({"data", "split", "mask", "gate"})
_INLINE_FORBIDDEN = frozenset({"id", "gate", "inputs", "notes", "claims", "depth"})


def needs_v3(checker: _Checker, path: str, what: str) -> None:
    """Report `what` at `path` when the lesson is not schema version 3."""
    if checker.schema_version < 3:  # noqa: PLR2004
        checker.report(path, f"{what} needs schema_version 3")


def kinds(checker: _Checker, obj: dict[str, object]) -> tuple[Kind, ...]:
    """Parse the lesson's `kinds`, each with a colour from `KIND_COLOURS`."""
    if "kinds" not in obj:
        return ()
    needs_v3(checker, "kinds", "'kinds'")
    found: list[Kind] = []
    for where, fields in _objects(
        checker, checker.array(obj, "kinds", ""), "kinds", {"id", "label", "colour"}
    ):
        ident = checker.identifier(fields, "id", where)
        if ident in {kind.id for kind in found}:
            checker.report(_at(where, "id"), f"'{ident}' is already used")
        colour = fields.get("colour")
        if colour not in KIND_COLOURS:
            checker.report(
                _at(where, "colour"), f"must be one of {', '.join(KIND_COLOURS)}"
            )
        found.append(
            Kind(ident, checker.text(fields, "label", where), cast("str", colour))
        )
    checker.kinds = {kind.id for kind in found}
    return tuple(found)


def datasets(checker: _Checker, obj: dict[str, object]) -> tuple[DatasetUse, ...]:
    """Load every declared dataset and check its `held_out` selector."""
    if "datasets" not in obj:
        return ()
    needs_v3(checker, "datasets", "'datasets'")
    found: list[DatasetUse] = []
    for where, fields in _objects(
        checker, checker.array(obj, "datasets", ""), "datasets", {"id"}, {"held_out"}
    ):
        ident = checker.identifier(fields, "id", where)
        if not ident:
            continue
        if ident in checker.datasets or ident in {use.id for use in found}:
            checker.report(_at(where, "id"), f"'{ident}' is already declared")
            continue
        try:
            dataset = data.load(checker.project_root, ident)
        except data.DataError as error:
            for at, message in error.problems or [("", str(error))]:
                checker.report(_at(where, at) if at else where, message)
            found.append(DatasetUse(ident))
            continue
        held_out = fields.get("held_out")
        if held_out is not None:
            _check_selector(checker, held_out, _at(where, "held_out"), dataset.fields)
        checker.datasets[ident] = dataset
        source = str(dataset.source.get("kind", "run"))
        found.append(
            DatasetUse(
                id=ident,
                held_out=cast("dict[str, object] | None", held_out),
                source=source,
                provenance=None if source == "wrong" else data.provenance(dataset),
                stale=tuple(data.stale(checker.project_root, dataset)),
            )
        )
    checker.uses = {use.id: use for use in found}
    return tuple(found)


def data_backing(checker: _Checker, value: dict[str, object], path: str) -> DataBacking:
    """Parse a data backing; its `rows` must match a row of a declared dataset."""
    needs_v3(checker, path, "a data backing")
    obj = checker.fields(value, path, frozenset({"data", "rows"}))
    data_id = checker.identifier(obj, "data", path)
    rows = obj.get("rows", {})
    dataset = checker.datasets.get(data_id)
    if data_id and data_id not in checker.uses:
        checker.report(_at(path, "data"), f"'{data_id}' is not a declared dataset")
    elif dataset is not None:
        found = len(checker.problems)
        _check_selector(checker, rows, _at(path, "rows"), dataset.fields)
        if len(checker.problems) == found and not any(
            selector.matches(cast("dict[str, object]", rows), row)
            for row in dataset.rows
        ):
            checker.report(_at(path, "rows"), "matches no row of the dataset")
    return DataBacking(data_id, rows if isinstance(rows, dict) else {})


def parse_view(
    checker: _Checker,
    obj: dict[str, object],
    path: str,
    stem: _ElementStem,
) -> ViewElement:
    """Parse a section view; its items are derived from its dataset here."""
    needs_v3(checker, path, "a view")
    return _view(checker, obj, path, stem, inline=False)


def _view(
    checker: _Checker,
    raw: dict[str, object],
    path: str,
    stem: _ElementStem,
    *,
    inline: bool,
) -> ViewElement:
    obj = {
        key: value
        for key, value in raw.items()
        if not (key in _NULLABLE and value is None) and (key, value) != ("missing", "")
    }
    ident = "" if inline else checker.identifier(obj, "id", path)
    checker.claim_id("question", ident, path)
    layout = cast("Layout", obj.get("layout", "lanes"))
    if layout not in LAYOUTS:
        checker.report(_at(path, "layout"), f"must be one of {', '.join(LAYOUTS)}")
        layout = "lanes"
    data_id = checker.identifier(obj, "data", path) or None
    inline_steps = layout == "steps" and "steps" in obj
    dataset = _dataset(checker, data_id, path, inline=inline, optional=inline_steps)
    fields = frozenset(dataset.fields) if dataset else frozenset()
    item_fields = fields | _RESERVED | _LAYOUT_RESERVED.get(layout, frozenset())
    split = checker.text(obj, "split", path) or None
    if split and dataset and split not in fields:
        checker.report(_at(path, "split"), f"'{split}' is not a field of the dataset")
    inputs_from, raw_inputs = _inputs_spec(checker, obj, path)
    view = ViewElement(
        **vars(stem),
        id=ident,
        layout=layout,
        caption=checker.text(obj, "caption", path),
        data=data_id,
        where=_selector_field(checker, obj, "where", path, fields) or {},
        encode=_encode(checker, obj, layout, path, fields),
        split=split,
        inputs_from=inputs_from,
        tasks=_tasks(checker, obj, path, item_fields),
        missing=checker.text(obj, "missing", path),
        marks=_marks(checker, obj, path, item_fields),
        layers=_layers(checker, obj, path, fields, item_fields),
        notes=_notes(checker, obj, path, item_fields),
        mask=_selector_field(checker, obj, "mask", path, item_fields),
        fill=checker.texts(obj, "fill", path),
        gate=checker.identifier(obj, "gate", path) or None,
        nodes=_nodes(checker, obj, path, dataset) if layout == "decision" else (),
        matrix=tuple(matrix(dataset)) if dataset else (),
    )
    if dataset is not None and not checker.failed_since(path):
        view = dataclasses.replace(
            view, items=tuple(_derive(checker, dataset, view, path, inline=inline))
        )
    inputs = _inputs(checker, raw_inputs, path, list(view.items))
    view = dataclasses.replace(
        view, inputs=inputs, presets=_presets(checker, obj, path, inputs)
    )
    if inline_steps:
        view = dataclasses.replace(view, steps=_inline_steps(checker, obj, path))
    elif layout == "steps" and dataset is not None:
        view = dataclasses.replace(view, steps=_data_steps(checker, view, path))
    if checker.failed_since(path):
        return view
    for index, column in enumerate(view.fill):
        if column not in fields:
            checker.report(_index(_at(path, "fill"), index), "is not a dataset field")
    if dataset and view.fill:
        view = dataclasses.replace(
            view, choices={f: data.choices(dataset, f) for f in view.fill}
        )
    _check_view(checker, view, path, inline=inline)
    return view


def _tasks(
    checker: _Checker, obj: dict[str, object], path: str, fields: frozenset[str]
) -> tuple[ViewTask, ...]:
    return tuple(
        ViewTask(
            checker.text(spec, "text", at),
            _selector_field(checker, spec, "when", at, fields) or {},
        )
        for at, spec in _objects(
            checker,
            checker.array(obj, "tasks", path),
            _at(path, "tasks"),
            {"text", "when"},
        )
    )


def _dataset(
    checker: _Checker, data_id: str | None, path: str, *, inline: bool, optional: bool
) -> data.Dataset | None:
    if data_id is None:
        if not optional:
            checker.report(_at(path, "data"), "is required")
        return None
    use = checker.uses.get(data_id)
    if use is None:
        checker.report(_at(path, "data"), f"'{data_id}' is not a declared dataset")
        return None
    if use.source == "wrong" and not inline:
        checker.report(
            _at(path, "data"), "a wrong dataset may only be drawn by final questions"
        )
    return checker.datasets.get(data_id)


def _selector_field(
    checker: _Checker,
    obj: Mapping[str, object],
    key: str,
    path: str,
    fields: Iterable[str],
) -> dict[str, object] | None:
    if key not in obj or obj[key] is None:
        return None
    value = obj[key]
    _check_selector(checker, value, _at(path, key), fields)
    return value if isinstance(value, dict) else {}


def _check_selector(
    checker: _Checker, value: object, path: str, fields: Iterable[str]
) -> None:
    for at, message in selector.problems(value, frozenset(fields)):
        checker.report(_at(path, at) if at else path, message)


def _encode(
    checker: _Checker,
    obj: dict[str, object],
    layout: str,
    path: str,
    fields: frozenset[str],
) -> dict[str, object]:
    where = _at(path, "encode")
    if (layout == "steps" and "steps" in obj) or layout == "decision":
        if "encode" in obj:
            checker.report(where, f"a {layout} view without data takes no encode")
        return {}
    required, optional = _ENCODE[layout]
    encode = checker.fields(obj.get("encode", {}), where, required, optional)
    named: list[tuple[str, object]] = [
        (key, encode[key]) for key in _FIELD_KEYS if key in encode
    ]
    for index, column in enumerate(checker.array(encode, "columns", where, 1)):
        at = _index(_at(where, "columns"), index)
        spec = checker.fields(column, at, frozenset({"field", "label"}))
        checker.text(spec, "label", at)
        named.append((f"columns[{index}].field", spec.get("field")))
    named.extend(
        (f"show[{index}]", item)
        for index, item in enumerate(checker.array(encode, "show", where))
    )
    for index, lane in enumerate(checker.array(encode, "lanes", where)):
        at = _index(_at(where, "lanes"), index)
        checker.text(
            checker.fields(lane, at, frozenset({"value", "label"})), "label", at
        )
    for key, value in named:
        if not isinstance(value, str) or (fields and value not in fields):
            checker.report(_at(where, key), f"{value!r} is not a field of the dataset")
    return encode


def _inputs_spec(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[str | None, list[object]]:
    value = obj.get("inputs", [])
    if isinstance(value, dict):
        fields = checker.fields(value, _at(path, "inputs"), frozenset({"from"}))
        return checker.identifier(fields, "from", _at(path, "inputs")) or None, []
    return None, checker.array(obj, "inputs", path)


def _inputs(
    checker: _Checker, raw: list[object], path: str, items: list[dict[str, object]]
) -> tuple[ViewInput, ...]:
    found: list[ViewInput] = []
    where = _at(path, "inputs")
    for at, fields in _objects(
        checker,
        raw,
        where,
        {"field", "label", "control", "default"},
        {"follows", "hint"},
    ):
        name = checker.text(fields, "field", at)
        control = fields.get("control")
        if control not in CONTROLS:
            checker.report(_at(at, "control"), f"must be one of {', '.join(CONTROLS)}")
            control = "select"
        values = _sorted_values(item.get(name) for item in items)
        if items and not any(name in item for item in items):
            checker.report(_at(at, "field"), f"'{name}' is not a field of the items")
        if control == "range" and not all(_number(v) for v in values):
            checker.report(_at(at, "control"), "a range needs numeric values")
        if control == "toggle" and len(values) != 2:  # noqa: PLR2004
            checker.report(
                _at(at, "control"),
                f"a toggle needs exactly 2 values, has {len(values)}",
            )
        default = fields.get("default")
        if items and not any(selector.same(default, v) for v in values):
            checker.report(
                _at(at, "default"), f"{default!r} is not a value of '{name}'"
            )
        follows = checker.text(fields, "follows", at) or None
        found.append(
            ViewInput(
                field=name,
                label=checker.text(fields, "label", at),
                control=cast("Control", control),
                default=default,
                follows=follows,
                hint=checker.text(fields, "hint", at),
                values=tuple(values),
            )
        )
    names = [item.field for item in found]
    for index, item in enumerate(found):
        if item.follows is not None and item.follows not in names:
            checker.report(
                _at(_index(where, index), "follows"), "must name another input field"
            )
        if item.field in names[:index]:
            checker.report(_at(_index(where, index), "field"), "is already an input")
    return tuple(found)


def _presets(
    checker: _Checker, obj: dict[str, object], path: str, inputs: tuple[ViewInput, ...]
) -> tuple[ViewPreset, ...]:
    by_field = {item.field: item for item in inputs}
    found: list[ViewPreset] = []
    for at, fields in _objects(
        checker,
        checker.array(obj, "presets", path),
        _at(path, "presets"),
        {"label", "values"},
    ):
        values = checker.fields(
            fields.get("values", {}),
            _at(at, "values"),
            frozenset(),
            frozenset(by_field),
        )
        for name, value in values.items():
            item = by_field.get(name)
            if item and not any(selector.same(value, v) for v in item.values):
                checker.report(
                    _at(_at(at, "values"), name), f"{value!r} is not a value of it"
                )
        found.append(ViewPreset(checker.text(fields, "label", at), dict(values)))
    return tuple(found)


def _marks(
    checker: _Checker, obj: dict[str, object], path: str, fields: frozenset[str]
) -> tuple[Mark, ...]:
    found: list[Mark] = []
    for at, spec in _objects(
        checker,
        checker.array(obj, "marks", path),
        _at(path, "marks"),
        {"where", "text"},
        {"tone", "place"},
    ):
        tone = spec.get("tone", "accent")
        if tone not in TONES:
            checker.report(_at(at, "tone"), f"must be one of {', '.join(TONES)}")
        place = spec.get("place", "column")
        if place not in ("column", "status"):
            checker.report(_at(at, "place"), "must be 'column' or 'status'")
        found.append(
            Mark(
                _selector_field(checker, spec, "where", at, fields) or {},
                checker.text(spec, "text", at),
                cast("str", tone),
                cast("Place", place),
            )
        )
    return tuple(found)


def _layers(
    checker: _Checker,
    obj: dict[str, object],
    path: str,
    fields: frozenset[str],
    item_fields: frozenset[str],
) -> tuple[Layer, ...]:
    found: list[Layer] = []
    for at, spec in _objects(
        checker,
        checker.array(obj, "layers", path),
        _at(path, "layers"),
        {"id", "label", "fields"},
        {"where", "on"},
    ):
        shown = checker.texts(spec, "fields", at, minimum=1)
        for index, name in enumerate(shown):
            if fields and name not in fields:
                checker.report(
                    _index(_at(at, "fields"), index), "is not a dataset field"
                )
        found.append(
            Layer(
                checker.identifier(spec, "id", at),
                checker.text(spec, "label", at),
                shown,
                _selector_field(checker, spec, "where", at, item_fields) or {},
                checker.flag(spec, "on", at, default=False),
            )
        )
    return tuple(found)


def _notes(
    checker: _Checker, obj: dict[str, object], path: str, fields: frozenset[str]
) -> tuple[ViewNote, ...]:
    from grokcheck.lesson import _verdict  # noqa: PLC0415

    found: list[ViewNote] = []
    for at, spec in _objects(
        checker,
        checker.array(obj, "notes", path),
        _at(path, "notes"),
        {"where", "text"},
        {"verified"},
    ):
        found.append(
            ViewNote(
                _selector_field(checker, spec, "where", at, fields) or {},
                checker.text(spec, "text", at),
                _verdict(checker, spec, at, "note"),
            )
        )
    return tuple(found)


def _nodes(
    checker: _Checker, obj: dict[str, object], path: str, dataset: data.Dataset | None
) -> tuple[DecisionNode, ...]:
    from grokcheck.lesson import _verdict  # noqa: PLC0415

    cited = _cited_files(dataset) if dataset else None
    found: list[DecisionNode] = []
    for at, spec in _objects(
        checker,
        checker.array(obj, "nodes", path, 1),
        _at(path, "nodes"),
        {"id", "label", "kind", "code", "note"},
        {"yes", "tone", "example", "verified"},
    ):
        ident = checker.identifier(spec, "id", at)
        if ident in {node.id for node in found}:
            checker.report(_at(at, "id"), f"'{ident}' is already used")
        kind = spec.get("kind")
        if kind not in ("check", "outcome"):
            checker.report(_at(at, "kind"), "must be 'check' or 'outcome'")
        raw_code = spec.get("code")
        code = (
            _file_code(checker, raw_code, _at(at, "code"))
            if isinstance(raw_code, dict)
            else None
        )
        if code is None:
            checker.report(_at(at, "code"), "must cite a file and lines")
            continue
        lines = (code.start_line, code.start_line + len(code.text.splitlines()) - 1)
        if cited is not None and code.file not in cited:
            checker.report(
                _at(_at(at, "code"), "file"),
                f"'{code.file}' is not cited by the dataset",
            )
        yes = None
        if "yes" in spec:
            yes_spec = checker.fields(spec["yes"], _at(at, "yes"), frozenset({"lines"}))
            yes = _line_span(checker, yes_spec, _at(at, "yes"))
            if kind != "check":
                checker.report(_at(at, "yes"), "only a check has a yes branch")
        tone = checker.text(spec, "tone", at) or None
        if tone is not None and tone not in TONES:
            checker.report(_at(at, "tone"), f"must be one of {', '.join(TONES)}")
        example = spec.get("example")
        if example is not None and dataset is not None:
            _check_example(checker, example, _at(at, "example"), dataset)
        found.append(
            DecisionNode(
                id=ident,
                label=checker.text(spec, "label", at),
                kind=cast("NodeKind", kind),
                code=code,
                lines=lines,
                note=checker.text(spec, "note", at),
                yes=yes,
                tone=tone,
                example=cast("dict[str, object] | None", example),
                verified=_verdict(checker, spec, at, "note"),
            )
        )
    return tuple(found)


def _check_example(
    checker: _Checker, example: object, path: str, dataset: data.Dataset
) -> None:
    if not isinstance(example, dict):
        checker.report(path, "must be an object of input values")
        return
    if not any(
        all(selector.same(run.inputs.get(k), v) for k, v in example.items())
        for run in dataset.runs
    ):
        checker.report(path, "no recorded run has these input values")


def _cited_files(dataset: data.Dataset) -> set[str]:
    cited = dataset.source.get("cited", [])
    files = set()
    for entry in cited if isinstance(cited, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("file"), str):
            files.add(entry["file"])
        elif isinstance(entry, str):
            files.add(entry)
    return files


def _derive(
    checker: _Checker,
    dataset: data.Dataset,
    view: ViewElement,
    path: str,
    *,
    inline: bool,
) -> list[dict[str, object]]:
    held_out = None if inline else checker.uses[dataset.id].held_out
    try:
        items = data.derive(
            dataset,
            view.layout,
            view.encode,
            where=view.where,
            inputs=[name for name in matrix(dataset) if name != view.split],
            split=view.split,
            exclude=held_out,
            nodes=[
                {
                    "id": node.id,
                    "kind": node.kind,
                    "label": node.label,
                    "code": {"file": node.code.file, "lines": list(node.lines)},
                    "yes": {"lines": list(node.yes)} if node.yes else None,
                }
                for node in view.nodes
            ],
        )
    except data.DataError as error:
        for at, message in error.problems or [("", str(error))]:
            checker.report(_at(path, at) if at else path, message)
        return []
    if not items and view.layout != "decision":
        rows = [row for row in dataset.rows if selector.matches(view.where, row)]
        message = (
            "every row it selects is held out"
            if rows and held_out is not None
            else "selects no rows"
        )
        checker.report(_at(path, "where"), message)
    if len(items) > MAX_ITEMS:
        checker.report(path, f"has {len(items)} items, at most {MAX_ITEMS} allowed")
    return items


def matrix(dataset: data.Dataset) -> list[str]:
    """Return the dataset's matrix input fields, in recorded order."""
    return list(dataset.runs[0].inputs) if dataset.runs else []


def _inline_steps(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[ViewStep, ...]:
    found: list[ViewStep] = []
    for at, spec in _objects(
        checker,
        checker.array(obj, "steps", path, 1),
        _at(path, "steps"),
        {"code", "note"},
        {"link", "verified"},
    ):
        from grokcheck.lesson import _verdict  # noqa: PLC0415

        code = _step_code(checker, spec.get("code"), _at(at, "code"))
        if code is None:
            continue
        found.append(
            ViewStep(
                code=code,
                note=checker.text(spec, "note", at),
                link=checker.texts(spec, "link", at),
                verified=_verdict(checker, spec, at, "note"),
            )
        )
    return tuple(found)


def _step_code(checker: _Checker, value: object, path: str) -> StepCode | None:
    spec = checker.fields(value, path, frozenset({"file", "lines"}))
    relative = checker.text(spec, "file", path)
    raw = spec.get("lines")
    spans_raw = (
        raw if isinstance(raw, list) and raw and isinstance(raw[0], list) else [raw]
    )
    spans: list[tuple[int, int]] = []
    for index, item in enumerate(spans_raw):
        span = _line_span(
            checker,
            {"lines": item},
            _index(path, index) if len(spans_raw) > 1 else path,
        )
        if span:
            spans.append(span)
    if not relative or len(spans) != len(spans_raw):
        return None
    spans.sort()
    shown = sum(end - start + 1 for start, end in spans)
    if shown > MAX_STEP_LINES:
        checker.report(
            _at(path, "lines"), f"shows {shown} lines, at most {MAX_STEP_LINES} allowed"
        )
    for (_, end), (start, _) in itertools.pairwise(spans):
        if start <= end:
            checker.report(_at(path, "lines"), "spans overlap")
        elif start - end > MAX_STEP_GAP:
            checker.report(
                _at(path, "lines"), f"spans are more than {MAX_STEP_GAP} lines apart"
            )
    read = _read_project_file(checker, relative, _at(path, "file"))
    if read is None:
        return None
    resolved, lines = read
    if spans[-1][1] > len(lines):
        checker.report(
            _at(path, "lines"),
            f"ends at line {spans[-1][1]} but '{relative}' has {len(lines)} lines",
        )
        return None
    file = resolved.relative_to(checker.project_root).as_posix()
    return StepCode(
        file=file,
        language=_language(file),
        spans=tuple(
            Span(start, "\n".join(lines[start - 1 : end])) for start, end in spans
        ),
    )


def _language(file: str) -> str:
    return _LANGUAGES.get(PurePosixPath(file).suffix.lower(), "plaintext")


def _data_steps(
    checker: _Checker, view: ViewElement, path: str
) -> tuple[ViewStep, ...]:
    file_field = str(view.encode.get("file"))
    line_field = str(view.encode.get("line"))
    show = [str(name) for name in cast("list[object]", view.encode.get("show", []))]
    files: dict[str, list[str] | None] = {}
    steps: list[ViewStep] = []
    for index, item in enumerate(view.items):
        file, line = item.get(file_field), item.get(line_field)
        if not isinstance(file, str) or not _number(line):
            checker.report(
                _index(_at(path, "items"), index), "has no file and line to show"
            )
            continue
        if file not in files:
            read = _read_project_file(checker, file, _at(path, "data"))
            files[file] = read[1] if read else None
        lines = files[file]
        if lines is None:
            continue
        number = int(cast("float", line))
        start = max(1, number - STEP_CONTEXT)
        end = min(len(lines), number + STEP_CONTEXT)
        relative = (
            (checker.project_root / file)
            .resolve()
            .relative_to(checker.project_root)
            .as_posix()
        )
        steps.append(
            ViewStep(
                code=StepCode(
                    relative,
                    _language(relative),
                    (Span(start, "\n".join(lines[start - 1 : end])),),
                ),
                link=(str(item.get("_link", "")),),
                show={name: item.get(name) for name in show},
                line=number,
            )
        )
    return tuple(steps)


def _check_view(
    checker: _Checker, view: ViewElement, path: str, *, inline: bool
) -> None:
    klass = view.encode.get("class")
    if isinstance(klass, str):
        for value in dict.fromkeys(item.get(klass) for item in view.items):
            if value is not None and value not in checker.kinds:
                checker.report(
                    _at(_at(path, "encode"), "class"),
                    f"{value!r} is not a declared kind",
                )
    if not inline:
        _check_notes(checker, view, path)
    _check_mask(checker, view, path, inline=inline)
    if (view.gate or inline) and len(scenarios(view)) > 1:
        checker.report(
            _at(path, "where"), "a gated or question view must select one scenario"
        )
    if view.gate and (view.inputs or view.inputs_from):
        checker.report(_at(path, "inputs"), "a gated view cannot have inputs")
    if view.inputs and _missing_combination(view) and not view.missing:
        checker.report(
            _at(path, "missing"), "is required: some input combination has no rows"
        )
    size = len(json.dumps(public(view, withhold=True)))
    size += len(json.dumps(gated_payload(view)))
    if size > MAX_VIEW_BYTES:
        checker.report(path, f"is {size} bytes as JSON, over {MAX_VIEW_BYTES}")
    checker.view_bytes += size
    if checker.view_bytes > MAX_LESSON_VIEW_BYTES:
        checker.report(
            path, f"all views pass {MAX_LESSON_VIEW_BYTES} bytes as JSON with this one"
        )


def _check_notes(checker: _Checker, view: ViewElement, path: str) -> None:
    for index, note in enumerate(view.notes):
        where = _at(_index(_at(path, "notes"), index), "where")
        if not any(selector.matches(note.where, item) for item in view.items):
            checker.report(where, "matches no item")
        elif view.data and not _backing_rows(checker, view, note):
            checker.report(where, "its dataset fields match no row")


def _check_mask(
    checker: _Checker, view: ViewElement, path: str, *, inline: bool
) -> None:
    if view.mask is not None:
        if view.layout == "lanes":
            _check_lane_mask(checker, view, path)
        elif view.layout != "table":
            checker.report(_at(path, "mask"), f"a {view.layout} view cannot be masked")
        if not view.gate and not inline:
            checker.report(_at(path, "mask"), "a mask needs a gate")
    if view.fill and view.layout != "table":
        checker.report(_at(path, "fill"), "only a table view has fill cells")
    if view.fill and not view.gate and not inline:
        checker.report(_at(path, "fill"), "fill cells need a gate")


def _check_lane_mask(checker: _Checker, view: ViewElement, path: str) -> None:
    lane = str(view.encode.get("lane"))
    if "ref_lane" not in view.encode:
        checker.report(_at(path, "encode"), "a masked lanes view needs ref_lane")
    mask = view.mask or {}
    touched = {
        _group(item, lane) for item in view.items if selector.matches(mask, item)
    }
    if not touched:
        checker.report(_at(path, "mask"), "matches no item")
    for item in view.items:
        if _group(item, lane) in touched and not selector.matches(mask, item):
            checker.report(_at(path, "mask"), "must cover whole lanes")
            return
    if any(group[1] == view.encode.get("ref_lane") for group in touched):
        checker.report(_at(path, "mask"), "cannot mask the reference lane")


def _group(item: Mapping[str, object], lane: str) -> tuple[object, object]:
    return item.get("_pane"), item.get(lane)


def scenarios(view: ViewElement) -> set[tuple[object, ...]]:
    """Return the distinct input combinations of `view`'s items, split excluded."""
    names = [name for name in view.matrix if name != view.split]
    return {tuple(_key(item.get(name)) for name in names) for item in view.items}


def _key(value: object) -> object:
    return (type(value).__name__ if isinstance(value, bool) else "", value)


def _missing_combination(view: ViewElement) -> bool:
    present = {
        tuple(_key(item.get(i.field)) for i in view.inputs) for item in view.items
    }
    return any(
        tuple(_key(v) for v in combination) not in present
        for combination in itertools.product(*(i.values for i in view.inputs))
    )


def _backing_rows(
    checker: _Checker, view: ViewElement, note: ViewNote
) -> list[dict[str, object]]:
    dataset = checker.datasets.get(view.data or "")
    if dataset is None:
        return []
    rows = note_rows(view, note)
    return [row for row in dataset.rows if selector.matches(rows, row)]


def note_rows(view: ViewElement, note: ViewNote) -> dict[str, object]:
    """Return the dataset-row selector backing `note`: its clauses on dataset fields."""
    if view.layout == "decision":
        fields = set(view.matrix)
    else:
        fields = {k for item in view.items[:1] for k in item if not k.startswith("_")}
    return {"and": [view.where, _dataset_clauses(note.where, fields)]}


def _dataset_clauses(
    where: Mapping[str, object], fields: set[str]
) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in where.items():
        if key in ("and", "or") and isinstance(value, list):
            inner = [_dataset_clauses(s, fields) for s in value if isinstance(s, dict)]
            inner = [s for s in inner if s]
            if inner and (key == "and" or len(inner) == len(value)):
                kept[key] = inner
        elif key == "not" and isinstance(value, dict):
            inner_not = _dataset_clauses(value, fields)
            if inner_not and inner_not == value:
                kept[key] = inner_not
        elif key in fields:
            kept[key] = value
    return kept


def _sorted_values(values: Iterable[object]) -> list[object]:
    unique: list[object] = []
    for value in values:
        if not any(selector.same(value, seen) for seen in unique):
            unique.append(value)
    return sorted(unique, key=sort_key)


def sort_key(value: object) -> tuple[int, object]:
    """Order `null` before booleans before numbers before strings."""
    if value is None:
        return 0, 0
    if isinstance(value, bool):
        return 1, value
    if _number(value):
        return 2, value
    return 3, str(value)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def public_items(
    view: ViewElement, *, withhold: bool, step: int | None = None
) -> list[dict[str, object]]:
    """Return `view`'s items as the page may see them, masked while `withhold`."""
    items = [_public_item(item) for item in view.items]
    if not withhold:
        return items
    if view.split and (view.mask is not None or view.fill):
        items = [{k: v for k, v in i.items() if k != "_differs"} for i in items]
    if view.layout == "lanes" and view.mask is not None:
        return _masked_lanes(view, items)
    if view.layout == "table" and view.fill:
        mask = view.mask or {}
        return [
            {**item, **dict.fromkeys(view.fill), "_masked": True}
            if selector.matches(mask, full)
            else item
            for item, full in zip(items, view.items, strict=True)
        ]
    if view.layout == "decision" and view.gate is not None:
        return [
            {k: v for k, v in item.items() if k not in ("_taken", "answer")}
            | {"_masked": True}
            for item in items
        ]
    if view.layout == "steps" and view.gate is not None and step is not None:
        show = [str(name) for name in cast("list[object]", view.encode.get("show", []))]
        return [
            item
            if index < step
            else {k: v for k, v in item.items() if k not in show} | {"_masked": True}
            for index, item in enumerate(items)
        ]
    return items


def _public_item(item: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in item.items() if key != "_edited"}


def _masked_lanes(
    view: ViewElement, items: list[dict[str, object]]
) -> list[dict[str, object]]:
    lane = str(view.encode.get("lane"))
    x = str(view.encode.get("x"))
    key = str(view.encode.get("key"))
    ref = view.encode.get("ref_lane")
    mask = view.mask or {}
    touched = {
        _group(full, lane) for full in view.items if selector.matches(mask, full)
    }
    inputs = view.matrix
    shown: list[dict[str, object]] = []
    emitted: set[tuple[object, object]] = set()
    for item in items:
        group = _group(item, lane)
        if group not in touched:
            shown.append(item)
            continue
        if group in emitted:
            continue
        emitted.add(group)
        pane, lane_value = group
        refs = [
            r
            for r in items
            if r.get("_pane") == pane
            and r.get(lane) == ref
            and not r.get("_lost")
            and not r.get("_dup")
        ]
        for r in refs:
            cell = next(
                (
                    str(i["_cell"])
                    for i in items
                    if _group(i, lane) == group
                    and selector.same(i.get(key), r.get(key))
                ),
                None,
            )
            if cell is None:
                continue
            placeholder: dict[str, object] = {
                "_cell": cell,
                "_id": cell,
                "_masked": True,
                lane: lane_value,
                x: r.get(x),
            }
            placeholder.update({name: r.get(name) for name in inputs})
            if view.split:
                placeholder["_pane"] = pane
            shown.append(placeholder)
    return shown


def candidates(view: ViewElement, *, withhold: bool) -> tuple[str, ...]:
    """Return the cells a reader may pick on `view`."""
    items = public_items(view, withhold=withhold)
    if view.layout == "lanes" and view.mask is not None and withhold:
        items = [item for item in items if item.get("_masked")]
    return tuple(dict.fromkeys(str(item["_cell"]) for item in items))


def public(
    view: ViewElement, *, withhold: bool, step: int | None = None
) -> dict[str, object]:
    """Return `view` as JSON-ready data, withholding its gated payload if `withhold`."""
    out: dict[str, object] = {"type": view.type_name}
    for name in (
        "id",
        "layout",
        "caption",
        "data",
        "where",
        "encode",
        "split",
        "missing",
        "mask",
        "gate",
        "depth",
    ):
        value = getattr(view, name)
        if value not in (None, ""):
            out[name] = value
    out["inputs"] = (
        {"from": view.inputs_from}
        if view.inputs_from
        else [dataclasses.asdict(item) for item in view.inputs]
    )
    for name in ("presets", "tasks", "marks", "layers", "notes", "nodes"):
        out[name] = [
            {k: v for k, v in dataclasses.asdict(item).items() if v is not None}
            for item in getattr(view, name)
        ]
    out["fill"] = list(view.fill)
    out["choices"] = {name: list(values) for name, values in view.choices.items()}
    out["claims"] = [_without_secrets(dataclasses.asdict(c)) for c in view.claims]
    steps = [dataclasses.asdict(s) for s in view.steps]
    if (
        withhold
        and view.layout == "steps"
        and view.gate is not None
        and step is not None
    ):
        for masked in steps[step:]:
            del masked["note"], masked["show"]
            masked["_masked"] = True
    out["steps"] = [{k: v for k, v in s.items() if v is not None} for s in steps]
    out["items"] = public_items(view, withhold=withhold, step=step)
    return cast("dict[str, object]", json.loads(json.dumps(out)))


def gated_payload(view: ViewElement) -> dict[str, object]:
    """Return what a gated `view` withholds: its full items, and steps if any."""
    payload: dict[str, object] = {
        "view": view.id,
        "items": public_items(view, withhold=False),
    }
    if view.layout == "steps":
        payload["steps"] = [
            {k: v for k, v in dataclasses.asdict(s).items() if v is not None}
            for s in view.steps
        ]
    return payload


def parse_select_items(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    """Parse `select_items`; an inline view is resolved into its `frame` here."""
    needs_v3(checker, path, "select_items")
    view_id, frame_view = _question_view(checker, obj, path)
    answer = obj.get("answer", {})
    question = SelectItems(
        **dataclasses.asdict(stem),
        view=view_id,
        answer=answer if isinstance(answer, dict) else {},
    )
    if frame_view is None:
        if not isinstance(answer, dict):
            checker.report(_at(path, "answer"), "must be a selector object")
        return question
    return resolve_select(checker, question, frame_view, path, frame=True)


def parse_fill_table(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    """Parse `fill_table`; an inline view is resolved into its `frame` here."""
    needs_v3(checker, path, "fill_table")
    view_id, frame_view = _question_view(checker, obj, path)
    question = FillTable(**dataclasses.asdict(stem), view=view_id)
    if frame_view is None:
        return question
    return resolve_fill(checker, question, frame_view, path, frame=True)


def _question_view(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[str | None, ViewElement | None]:
    value = obj.get("view")
    in_section = path.startswith("sections")
    if isinstance(value, str):
        if not in_section:
            checker.report(
                _at(path, "view"), "a final or probe question needs an inline view"
            )
        return value, None
    if in_section:
        checker.report(_at(path, "view"), "must name a view of this section by id")
        return None, None
    where = _at(path, "view")
    if not isinstance(value, dict):
        checker.report(where, "must be a view id or an inline view object")
        return None, None
    for key in sorted(_INLINE_FORBIDDEN & value.keys()):
        checker.report(_at(where, key), "is not allowed in an inline view")
    allowed = {k: v for k, v in value.items() if k not in _INLINE_FORBIDDEN}
    fields = checker.fields(
        allowed,
        where,
        frozenset({"type", "layout", "caption"}),
        _VIEW_OPTIONAL - {"inputs", "notes", "gate"},
    )
    if fields.get("type") != "view":
        checker.report(_at(where, "type"), "must be 'view'")
    view = _view(checker, fields, where, _ElementStem("short", ()), inline=True)
    return None, view


def resolve_select(
    checker: _Checker,
    question: SelectItems,
    view: ViewElement,
    path: str,
    *,
    frame: bool,
) -> SelectItems:
    """Fill in `candidates` and `answer_cells` of `question` from `view`.

    With `frame`, `view` is the question's inline view and stays masked.
    """
    if not view.items:
        return question
    withhold = frame or view.gate == question.id
    fields = (
        {name for item in view.items for name in item}
        | _RESERVED
        | _LAYOUT_RESERVED.get(view.layout, frozenset())
    )
    _check_selector(checker, question.answer, _at(path, "answer"), fields)
    picked = candidates(view, withhold=withhold)
    cells = tuple(
        sorted(
            {
                str(item["_cell"])
                for item in view.items
                if selector.matches(question.answer, item)
            }
        )
    )
    where = _at(path, "answer")
    if not cells:
        checker.report(where, "matches no item")
    elif set(cells) >= set(picked):
        checker.report(where, "matches every candidate, so picking all is right")
    elif not set(cells) <= set(picked):
        checker.report(where, "matches items that are not candidates")
    return dataclasses.replace(
        question,
        candidates=picked,
        answer_cells=cells,
        frame=public(view, withhold=True) if frame else None,
    )


def resolve_fill(
    checker: _Checker,
    question: FillTable,
    view: ViewElement,
    path: str,
    *,
    frame: bool,
) -> FillTable:
    """Fill in `blanks` and `cells` of `question` from the table `view`."""
    if view.layout != "table" or not view.fill:
        checker.report(_at(path, "view"), "must be a table view with fill fields")
        return question
    shown = public_items(view, withhold=True)
    blanks: list[TableBlank] = []
    cells: dict[str, dict[str, object]] = {}
    for item, full in zip(shown, view.items, strict=True):
        if not item.get("_masked"):
            continue
        cell = str(full["_cell"])
        for name in view.fill:
            blanks.append(TableBlank(cell, name))
            cells.setdefault(cell, {})[name] = full.get(name)
    if not blanks:
        checker.report(_at(path, "view"), "its mask hides no cell")
    return dataclasses.replace(
        question,
        blanks=tuple(blanks),
        cells=cells,
        frame=public(view, withhold=True) if frame else None,
    )


def check_section(checker: _Checker, section: Section, path: str) -> Section:
    """Check the views of `section` against its checkpoints and resolve their keys."""
    views = {
        e.id: (index - section.code_sugar, e)
        for index, e in enumerate(section.elements)
        if isinstance(e, ViewElement)
    }
    checkpoints = tuple(
        _resolve_checkpoint(
            checker, question, views, _index(_at(path, "checkpoints"), index)
        )
        for index, question in enumerate(section.checkpoints)
    )
    named = {str(getattr(q, "view", None)) for q in checkpoints}
    by_id = {q.id: q for q in checkpoints}
    for view_id, (index, view) in views.items():
        where = _index(_at(path, "elements"), index)
        if view.inputs_from is not None:
            source = views.get(view.inputs_from)
            if source is None or not source[1].inputs:
                checker.report(
                    _at(where, "inputs"),
                    f"'{view.inputs_from}' is not a view with inputs in this section",
                )
        if view.gate is not None:
            _check_gate(checker, view, by_id.get(view.gate), where)
        if view.layout != "blocks" and not (
            view.gate or view.tasks or view_id in named
        ):
            checker.report(
                where, "is never asked about: give it a gate, a checkpoint or tasks"
            )
    return dataclasses.replace(section, checkpoints=checkpoints)


def _resolve_checkpoint(
    checker: _Checker,
    question: Question,
    views: Mapping[str, tuple[int, ViewElement]],
    where: str,
) -> Question:
    view_id = getattr(question, "view", None)
    if not isinstance(question, (SelectItems, FillTable, PredictState)) or not view_id:
        return question
    found = views.get(view_id)
    if found is None:
        checker.report(_at(where, "view"), f"no view '{view_id}' in this section")
        return question
    view = found[1]
    if view.inputs or view.inputs_from:
        checker.report(_at(where, "view"), "names a view with inputs")
    if isinstance(question, SelectItems):
        return resolve_select(checker, question, view, where, frame=False)
    if isinstance(question, FillTable):
        if view.gate != question.id:
            checker.report(_at(where, "view"), "the table's gate must be this question")
        return resolve_fill(checker, question, view, where, frame=False)
    if view.layout != "steps":
        checker.report(_at(where, "view"), "predict_state needs a steps view")
    elif not 0 <= question.step < len(view.steps):
        checker.report(
            _at(where, "step"),
            f"step {question.step} is out of range for {len(view.steps)} step(s)",
        )
    return question


def _check_gate(
    checker: _Checker, view: ViewElement, gate: Question | None, where: str
) -> None:
    checker.claim_id("gate", view.gate or "", _at(where, "gate"))
    if getattr(gate, "view", None) != view.id:
        checker.report(
            _at(where, "gate"),
            "must name a checkpoint of this section that names this view",
        )
    if view.layout == "blocks":
        checker.report(_at(where, "gate"), "a blocks view is never gated")
    if view.layout == "lanes" and view.mask is None:
        checker.report(_at(where, "mask"), "a gated lanes view needs a mask")
    if view.layout == "steps" and not isinstance(gate, PredictState):
        checker.report(_at(where, "gate"), "a steps view is gated by predict_state")
