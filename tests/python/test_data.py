"""Datasets: recording drivers, the refusal rules, and the items views draw."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from grokcheck import data
from grokcheck.data import DataError

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "project"
_MATRIX = {"version": ["old", "new"], "drop": [2], "lid": [2, 3, 4], "running": [0, 1]}
_ONE_ROW = json.dumps(
    {"version": "new", "drop": 2, "lid": 2, "running": 0, "lane": "client", "seq": 3}
)
_FIXTURE_RUNS = 24
_TERMS = 4
_LANES = {
    "lane": "lane",
    "x": "seq",
    "key": "event",
    "label": "text",
    "class": "kind",
    "ref_lane": "server",
    "lanes": [{"value": "server", "label": "has"}, {"value": "client", "label": "got"}],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Copy the fixture project's stream code and driver."""
    shutil.copytree(_FIXTURE / "src", tmp_path / "src")
    shutil.copytree(
        _FIXTURE / ".grokcheck" / "drivers", tmp_path / ".grokcheck" / "drivers"
    )
    return tmp_path


def _driver(project: Path, body: str) -> Path:
    path = project / "driver.py"
    path.write_text(body, encoding="utf-8")
    return path


def _record(project: Path, driver: Path, **options: Any) -> dict[str, Any]:  # noqa: ANN401
    options.setdefault("matrix", {})
    return data.record(project, driver, "d", ["src/stream.py"], **options)


def _refusal(error: pytest.ExceptionInfo[DataError]) -> str:
    return "\n".join(message for _, message in error.value.problems)


def test_record_runs_every_combination_with_its_inputs(project: Path) -> None:
    """Each combination is one run whose rows carry its inputs and cited lines."""
    summary = _record(
        project, project / ".grokcheck" / "drivers" / "reconnect.py", matrix=_MATRIX
    )

    dataset = data.load(project, "d")
    if (summary["runs"], len(dataset.runs)) != (12, 12):
        pytest.fail(f"expected 12 runs, got {summary}")
    for run in dataset.runs:
        rows = dataset.rows[run.rows[0] : run.rows[1]]
        if not rows or any(
            {k: row[k] for k in run.inputs} != run.inputs for row in rows
        ):
            pytest.fail(f"rows of {run.inputs} lack their inputs")
        if run.cited_lines == 0 or "src/stream.py" not in run.lines_run:
            pytest.fail(f"run {run.inputs} ran no cited line")
    source = dataset.source
    if (
        source["matrix"] != _MATRIX
        or source["script"] != ".grokcheck/drivers/reconnect.py"
    ):
        pytest.fail(f"source is {source}")
    if (
        source["cited"]
        != [{"file": "src/stream.py", "sha256": data.sha256(project / "src/stream.py")}]
        or not source["cited_lines_run"]
    ):
        pytest.fail(f"cited is {source['cited']}")


def test_follow_path_runs_lines_the_replay_path_does_not(project: Path) -> None:
    """Lines run are per run: `_follow` runs only for a running turn it reaches."""
    _record(
        project, project / ".grokcheck" / "drivers" / "reconnect.py", matrix=_MATRIX
    )
    dataset = data.load(project, "d")
    follow_line = 53
    for run in dataset.runs:
        ran = any(a <= follow_line <= b for a, b in run.lines_run["src/stream.py"])
        expected = bool(run.inputs["running"]) and (
            run.inputs["version"] == "old" or run.inputs["lid"] in {2, 3}
        )
        if ran != expected:
            pytest.fail(f"{run.inputs}: _follow ran is {ran}")


def test_each_run_is_a_fresh_process(project: Path) -> None:
    """Module state set by one run never reaches the next."""
    (project / "counter.py").write_text("count = 0\n", encoding="utf-8")
    driver = _driver(
        project,
        "import sys\nsys.path.insert(0, 'src')\nimport counter\n"
        "from stream import Log, read\n"
        "list(read(Log([('a', 'b')]), 0, running=False))\n"
        "counter.count += 1\nemit(count=counter.count)\n",
    )

    _record(project, driver, matrix={"n": [1, 2, 3]})

    counts = [row["count"] for row in data.load(project, "d").rows]
    if counts != [1, 1, 1]:
        pytest.fail(f"module state leaked between runs: {counts}")


@pytest.mark.parametrize(
    "body",
    [
        "emit(event='1', lane='client')\n",
        "import sys\nsys.path.insert(0, 'src')\nimport stream\nemit(event='1')\n",
    ],
)
def test_rows_without_the_cited_code_running_are_refused(
    project: Path, body: str
) -> None:
    """The zero-cited-lines rule: printing rows or importing is not running code."""
    driver = _driver(project, body)

    with pytest.raises(DataError) as error:
        _record(project, driver)

    if "no cited line ran" not in _refusal(error):
        pytest.fail(_refusal(error))
    if data.data_path(project, "d").exists():
        pytest.fail("a refused dataset was written")


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("emit(version='x')", "differs from this run's input"),
        ("emit(**{'not': 1})", "not a valid field name"),
        ("emit(rows=[1])", "is not a string, number, boolean or None"),
        ("raise RuntimeError('boom')", "RuntimeError('boom')"),
    ],
)
def test_driver_errors_refuse_the_record(
    project: Path, call: str, expected: str
) -> None:
    """A bad emit or any driver exception refuses with the inputs and traceback."""
    driver = _driver(project, f"{call}\n")

    with pytest.raises(DataError) as error:
        _record(project, driver, matrix={"version": ["new"]})

    message = _refusal(error)
    if expected not in message or "{'version': 'new'}" not in message:
        pytest.fail(message)
    if 'driver.py", line 1' not in message:
        pytest.fail(f"no traceback tail in {message}")


def test_a_driver_past_its_timeout_is_refused(project: Path) -> None:
    """`timeout` bounds every run."""
    driver = _driver(project, "import time\ntime.sleep(30)\n")

    with pytest.raises(DataError) as error:
        _record(project, driver, timeout=0.5)

    if "ran past 0.5s" not in _refusal(error):
        pytest.fail(_refusal(error))


def test_a_second_interpreter_runs_the_driver(project: Path) -> None:
    """`python` runs the child under another interpreter without grokcheck in it."""
    found = subprocess.run(
        ["uv", "python", "find", "3.12"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if found.returncode != 0:
        pytest.skip("uv finds no Python 3.12")
    python = found.stdout.strip()

    _record(
        project,
        project / ".grokcheck" / "drivers" / "reconnect.py",
        matrix={"version": ["new"], "drop": [2], "lid": [3], "running": [1]},
        python=python,
    )

    dataset = data.load(project, "d")
    if not str(dataset.source["python"]).startswith("3.12"):
        pytest.fail(f"recorded under {dataset.source['python']}")
    if not dataset.rows:
        pytest.fail("no rows from the second interpreter")


@pytest.mark.parametrize(
    ("specs", "expected"),
    [
        (["drop=2..4"], {"drop": [2, 3, 4]}),
        (["lid=1,3"], {"lid": [1, 3]}),
        (["version=old,new"], {"version": ["old", "new"]}),
        (["mix=1,a"], {"mix": ["1", "a"]}),
        (["a=1", "b=x"], {"a": [1], "b": ["x"]}),
    ],
)
def test_parse_matrix(specs: list[str], expected: dict[str, list[object]]) -> None:
    """Ranges are inclusive ints; lists are ints only when every item parses."""
    if data.parse_matrix(specs) != expected:
        pytest.fail(f"{specs} gave {data.parse_matrix(specs)}")


@pytest.mark.parametrize("specs", [["a=1", "a=2"], ["not=1"], ["a=x..2"], ["a="]])
def test_parse_matrix_refuses(specs: list[str]) -> None:
    """Duplicate names, reserved names, bad bounds and empty lists are refused."""
    with pytest.raises(DataError):
        data.parse_matrix(specs)


def test_more_than_200_combinations_is_refused(project: Path) -> None:
    """The matrix is capped before any run starts."""
    with pytest.raises(DataError) as error:
        _record(
            project,
            project / ".grokcheck" / "drivers" / "reconnect.py",
            matrix={"a": list(range(15)), "b": list(range(15))},
        )

    if "225 combinations" not in _refusal(error):
        pytest.fail(_refusal(error))


def test_fixture_datasets_load_and_are_fresh() -> None:
    """The shared fixture datasets load, are not stale, and keep their shape."""
    reconnect = data.load(_FIXTURE, "reconnect")
    if len(reconnect.runs) != _FIXTURE_RUNS or {
        r.get("lane") for r in reconnect.rows
    } != {
        "server",
        "client",
    }:
        pytest.fail("reconnect lost its 24 runs or its two lanes")
    edited = [
        r for r in data.load(_FIXTURE, "reconnect-wrong").rows if r.get("_edited")
    ]
    if len(edited) != 1:
        pytest.fail(f"reconnect-wrong has {len(edited)} edited rows")
    if len(data.load(_FIXTURE, "terms").rows) != _TERMS:
        pytest.fail("terms does not hold 4 rows")
    for dataset_id in ("reconnect", "reconnect-wrong", "terms", "producer"):
        if stale := data.stale(_FIXTURE, data.load(_FIXTURE, dataset_id)):
            pytest.fail(f"{dataset_id} is stale: {stale}; re-record it")


def _copy_data(project: Path, *ids: str) -> None:
    (project / ".grokcheck" / "data").mkdir(parents=True, exist_ok=True)
    for dataset_id in ids:
        shutil.copy(
            data.data_path(_FIXTURE, dataset_id), data.data_path(project, dataset_id)
        )


def test_from_trace_flattens_events_with_locals(project: Path) -> None:
    """Rows are trace events; recorded locals become `l_` string fields."""
    trace = _FIXTURE / ".grokcheck" / "traces" / "producer.json"
    (project / "trace.json").write_bytes(trace.read_bytes())

    summary = data.from_trace(project, project / "trace.json", "p")

    dataset = data.load(project, "p")
    lines = [r for r in dataset.rows if r["event"] == "line"]
    if not lines or {r["file"] for r in lines} != {"src/producer.py"}:
        pytest.fail(f"line rows are {lines[:3]}")
    if not any(r.get("l_items") == "[1]" for r in dataset.rows):
        pytest.fail("no row shows the local items as '[1]'")
    if summary["cited_lines_run"] == 0 or dataset.source["kind"] != "trace":
        pytest.fail(f"summary is {summary}")

    (project / "trace.json").write_text('{"events": []}', encoding="utf-8")
    with pytest.raises(DataError, match="changed since the record"):
        data.load(project, "p")


def _rows_file(project: Path, rows: list[dict[str, Any]]) -> Path:
    path = project / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_author_checks_every_cite(project: Path) -> None:
    """Authored rows need a cite whose quote is in the cited lines."""
    good = {"term": "Beyond", "cite": {"file": "src/stream.py", "lines": [32, 33]}}
    bad = {
        "term": "x",
        "cite": {"file": "src/stream.py", "lines": [1, 1], "quote": "nope"},
    }

    data.author(project, "t", _rows_file(project, [good]), "names, not behaviour")
    if data.load(project, "t").rows != ({"term": "Beyond"},):
        pytest.fail("the cite reached the loaded rows")
    with pytest.raises(DataError, match="quote 'nope' is not in lines 1-1"):
        data.author(project, "t2", _rows_file(project, [good, bad]), "names")
    with pytest.raises(DataError, match="cite: is required"):
        data.author(project, "t3", _rows_file(project, [{"term": "y"}]), "names")


def test_a_contradicted_authored_row_is_refused(project: Path) -> None:
    """A row the grounding pass contradicted stops the dataset loading."""
    row = {"term": "Beyond", "cite": {"file": "src/stream.py", "lines": [32, 33]}}
    data.author(project, "t", _rows_file(project, [row]), "names")
    path = data.data_path(project, "t")
    raw = json.loads(path.read_text("utf-8"))
    raw["source"]["verdicts"] = {"0": "contradicted"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(DataError, match="contradicted"):
        data.load(project, "t")


def test_wrong_copies_edit_one_row(project: Path) -> None:
    """`wrong` edits exactly the selected row and marks it `_edited`."""
    _copy_data(project, "reconnect")
    data.wrong(project, "reconnect", "w", [(_ONE_ROW, 'event="4"')])

    copy = data.load(project, "w")
    edited = [r for r in copy.rows if r.get("_edited")]
    if len(edited) != 1 or edited[0]["event"] != "4":
        pytest.fail(f"edited rows are {edited}")
    row = copy.rows.index(edited[0])
    if copy.source["edits"] != [{"row": row, "field": "event", "was": "3", "now": "4"}]:
        pytest.fail(f"edits are {copy.source['edits']}")
    if len(copy.runs) != len(data.load(project, "reconnect").runs):
        pytest.fail("the copy did not inherit the runs")


@pytest.mark.parametrize(
    ("where", "assignment", "expected"),
    [
        ('{"lane": "client"}', "event=1", "matches"),
        (
            _ONE_ROW,
            "drop=3",
            "matrix input",
        ),
        (
            _ONE_ROW,
            "seq=x",
            "is not int",
        ),
        ('{"nope": 1}', "event=1", "'nope' is not a field"),
    ],
)
def test_wrong_refuses_bad_edits(
    project: Path, where: str, assignment: str, expected: str
) -> None:
    """An edit must hit one row, a non-input field, with a value of its type."""
    _copy_data(project, "reconnect")

    with pytest.raises(DataError) as error:
        data.wrong(project, "reconnect", "w", [(where, assignment)])

    if expected not in _refusal(error):
        pytest.fail(_refusal(error))


def test_a_wrong_copy_of_a_changed_original_is_refused(project: Path) -> None:
    """The copy pins the original's sha256."""
    _copy_data(project, "reconnect", "reconnect-wrong")
    path = data.data_path(project, "reconnect")
    path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")

    with pytest.raises(DataError, match="changed since this copy"):
        data.load(project, "reconnect-wrong")


def test_show_and_stale(project: Path) -> None:
    """`show` counts the selected rows; editing a cited file makes it stale."""
    _copy_data(project, "reconnect")

    shown = data.show(project, "reconnect", {"lane": "client", "event": "409"}, 1)

    if (shown["rows"], shown["runs"], len(shown["sample"])) != (2, 24, 1):
        pytest.fail(f"show gave {shown}")
    if shown["inputs"]["lid"] != [2, 3, 4] or shown["stale"]:
        pytest.fail(f"show gave {shown}")
    (project / "src" / "stream.py").write_text("changed\n", encoding="utf-8")
    if data.show(project, "reconnect", {}, 0)["stale"] != ["src/stream.py"]:
        pytest.fail("an edited cited file is not stale")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda raw: raw.update(format=2), "format"),
        (lambda raw: raw.update(id="other"), "the file name"),
        (lambda raw: raw["rows"][0].update(extra=1), "is not in fields"),
        (lambda raw: raw["rows"][0].update(seq="1"), "is not int"),
        (lambda raw: raw["rows"][0].update(seq=[1]), "is not int"),
        (lambda raw: raw["source"].update(cited_lines_run=0), "no cited line ran"),
        (lambda raw: raw["runs"][0].update(cited_lines=0), "emitted rows without"),
        (lambda raw: raw.update(rows=raw["rows"] * 20), "at most 5000"),
    ],
)
def test_load_refusals(project: Path, change: Any, expected: str) -> None:  # noqa: ANN401
    """Each refusal rule stops the load with its reason."""
    _copy_data(project, "reconnect")
    path = data.data_path(project, "reconnect")
    raw = json.loads(path.read_text("utf-8"))
    change(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(DataError) as error:
        data.load(project, "reconnect")

    if expected not in str(error.value):
        pytest.fail(str(error.value))


def test_a_file_over_2_mb_is_refused(project: Path) -> None:
    """Size is checked before parsing."""
    _copy_data(project, "reconnect")
    path = data.data_path(project, "reconnect")
    path.write_text(path.read_text("utf-8") + " " * data.MAX_BYTES, encoding="utf-8")

    with pytest.raises(DataError, match="bytes"):
        data.load(project, "reconnect")


def _lanes(
    where: dict[str, object], split: str | None = None
) -> list[dict[str, object]]:
    return data.derive(
        data.load(_FIXTURE, "reconnect"),
        "lanes",
        _LANES,
        where=where,
        inputs=[],
        split=split,
        exclude=None,
    )


def test_lanes_items_mark_lost_cells() -> None:
    """A key on the reference lane and missing from another is a lost item there."""
    items = _lanes({"version": "new", "drop": 2, "lid": 3, "running": 0})

    lost = [i["_id"] for i in items if i["_lost"]]
    if lost != ["client|3"]:
        pytest.fail(f"lost items are {lost}")
    order = [i["_id"] for i in items]
    if order[:5] != [f"server|{n}" for n in range(1, 6)] or order[7] != "client|3":
        pytest.fail(f"items are not in lane then x order: {order}")


def test_lanes_items_mark_repeats_bursts_and_split_differences() -> None:
    """The old reader repeats events; with split only those cells differ."""
    items = _lanes({"drop": 2, "lid": 2, "running": 0}, split="version")

    repeats = [i["_id"] for i in items if i["_dup"]]
    if repeats != ["old|client|1#2", "old|client|2#2"]:
        pytest.fail(f"repeats are {repeats}")
    bursts = {i["_id"] for i in items if i["_burst"]}
    if bursts != {"old|client|1", "old|client|1#2", "old|client|2", "old|client|2#2"}:
        pytest.fail(f"bursts are {bursts}")
    differs = {i["_id"] for i in items if i["_differs"]}
    if differs != {"old|client|1#2", "old|client|2#2"}:
        pytest.fail(f"differing items are {differs}")


def test_held_out_rows_are_excluded() -> None:
    """`exclude` drops held-out rows before items are built."""
    items = data.derive(
        data.load(_FIXTURE, "reconnect"),
        "lanes",
        _LANES,
        where={"version": "new"},
        inputs=["drop", "lid", "running"],
        split=None,
        exclude={"drop": 3, "lid": 4},
    )

    if any(i["drop"] == 3 and i["lid"] == 4 for i in items):  # noqa: PLR2004
        pytest.fail("a held-out row became an item")


def test_table_refuses_a_duplicate_cell() -> None:
    """Two rows with one key in a table group is an error."""
    with pytest.raises(DataError, match="share the cell"):
        data.derive(
            data.load(_FIXTURE, "reconnect"),
            "table",
            {"columns": [{"field": "kind", "label": "Kind"}], "key": "kind"},
            where={"version": "new", "drop": 2, "lid": 2, "running": 0},
            inputs=[],
            split=None,
            exclude=None,
        )


def test_decision_nodes_follow_the_lines_each_run_executed() -> None:
    """A node is taken when its lines ran; a check answers by its `yes` lines."""
    nodes = [
        {
            "id": "beyond",
            "kind": "check",
            "code": {"file": "src/stream.py", "lines": [32, 32]},
            "yes": {"lines": [33, 33]},
        },
        {
            "id": "409",
            "kind": "outcome",
            "code": {"file": "src/stream.py", "lines": [33, 33]},
        },
    ]

    items = data.derive(
        data.load(_FIXTURE, "reconnect"),
        "decision",
        {},
        where={"version": "new", "drop": 2},
        inputs=["lid", "running"],
        split=None,
        exclude=None,
        nodes=nodes,
    )

    taken = {
        (i["lid"], i["running"]) for i in items if i["node"] == "409" and i["_taken"]
    }
    if taken != {(4, 1)}:
        pytest.fail(f"409 taken for {taken}")
    answers = {i["answer"] for i in items if i["node"] == "beyond"}
    if answers != {"yes", "no"}:
        pytest.fail(f"check answers are {answers}")
    with pytest.raises(DataError, match="not a cited file"):
        data.derive(
            data.load(_FIXTURE, "reconnect"),
            "decision",
            {},
            where={},
            inputs=[],
            split=None,
            exclude=None,
            nodes=[
                {
                    "id": "x",
                    "kind": "outcome",
                    "code": {"file": "src/cache.py", "lines": [1, 1]},
                }
            ],
        )


def test_choices_sort_null_booleans_numbers_strings() -> None:
    """Distinct values sort null first, then booleans, numbers and strings."""
    dataset = data.Dataset(
        "x",
        {"v": "str"},
        (
            {"v": "b"},
            {"v": None},
            {"v": 2},
            {"v": True},
            {"v": 1.0},
            {"v": 1},
            {"v": "a"},
        ),
        (),
        {"kind": "authored"},
        "",
    )

    if data.choices(dataset, "v") != [None, True, 1.0, 2, "a", "b"]:
        pytest.fail(f"choices are {data.choices(dataset, 'v')}")
