"""Recording real runs as task events with cited line events nested under them."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from grokcheck.trace import Event, Trace, record

_REPO = Path(__file__).resolve().parents[2]
_PACKAGE = _REPO / "skills" / "grokcheck" / "grokcheck"
_FIXTURES = Path("tests/fixtures/trace")


def test_async_run_records_task_spine_and_cited_lines_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks A and B interleave, and line events come only from the cited file."""
    monkeypatch.chdir(_REPO)
    producer = _FIXTURES / "async_producer.py"

    trace = record(producer, {producer})

    spine = [e.event for e in trace.events if e.task == "A"]
    expected = ["created", "resume", "suspend", "resume", "suspend", "resume", "done"]
    if spine != expected:
        pytest.fail(f"task A events {spine}, expected {expected}")
    first_b = next(e.seq for e in trace.events if e.task == "B" and e.event == "resume")
    second_a = [e.seq for e in trace.events if e.task == "A" and e.event == "resume"][1]
    if not first_b < second_a:
        pytest.fail("B did not run between A's first and second resume")
    line_files = {e.file for e in trace.events if e.event == "line"}
    if line_files != {str(producer)}:
        pytest.fail(f"line events came from {line_files}, not only the cited file")

    cache = _FIXTURES / "sync_cache.py"
    sync = record(cache, {cache})

    if any(e.event != "line" for e in sync.events):
        pytest.fail(f"a sync run produced {[e.event for e in sync.events]}")
    if not any(e.locals.get("key") == "'a'" for e in sync.events):
        pytest.fail("no line event shows the local key as 'a'")


def test_cli_trace_record_writes_a_trace_and_reports_its_steps(tmp_path: Path) -> None:
    """`trace record --out` writes a loadable trace and reports its step count."""
    cache = _REPO / _FIXTURES / "sync_cache.py"
    out = tmp_path / "trace.json"

    completed = subprocess.run(  # noqa: S603
        [
            *(sys.executable, str(_PACKAGE), "trace", "record", str(cache)),
            *("--cite", str(cache), "--out", str(out), "--project", str(tmp_path)),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    if completed.returncode != 0:
        pytest.fail(f"trace record failed: {completed.stdout}{completed.stderr}")
    report = json.loads(completed.stdout)
    written = Trace.from_json(json.loads(out.read_text(encoding="utf-8")))
    if report["steps"] != len(written.steps) or not written.events:
        pytest.fail(f"report {report} does not match the written trace")


def test_steps_nest_line_events_under_the_preceding_task_event() -> None:
    """Lines after a task event join it; a leading line is a step of its own."""
    trace = Trace(
        [
            Event(0, "line"),
            Event(1, "resume", task="A"),
            Event(2, "line"),
            Event(3, "line"),
            Event(4, "suspend", task="A"),
        ]
    )

    shape = [(step.event.seq, [ln.seq for ln in step.lines]) for step in trace.steps]

    if shape != [(0, []), (1, [2, 3]), (4, [])]:
        pytest.fail(f"unexpected steps {shape}")


def test_trace_survives_a_json_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """`from_json(to_json())` through real JSON gives back an equal trace."""
    monkeypatch.chdir(_REPO)
    cache = _FIXTURES / "sync_cache.py"
    trace = record(cache, {cache})

    again = Trace.from_json(json.loads(json.dumps(trace.to_json())))

    if again != trace:
        pytest.fail("the trace changed in a JSON round trip")


def test_recording_stops_at_max_events_and_marks_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with more events than `max_events` keeps the first ones only."""
    monkeypatch.chdir(_REPO)
    cache = _FIXTURES / "sync_cache.py"

    trace = record(cache, {cache}, max_events=3)

    if not trace.truncated or [e.seq for e in trace.events] != [0, 1, 2]:
        pytest.fail(f"expected 3 events and truncated, got {trace}")


def test_cancelled_task_records_cancelled_not_done(tmp_path: Path) -> None:
    """A task cancelled before it finishes ends with a `cancelled` event."""
    script = tmp_path / "cancel.py"
    script.write_text(
        "import asyncio\n"
        "async def main():\n"
        "    task = asyncio.create_task(asyncio.sleep(10), name='S')\n"
        "    await asyncio.sleep(0)\n"
        "    task.cancel()\n"
        "    await asyncio.gather(task, return_exceptions=True)\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )

    trace = record(script, {script})

    ends = {e.task: e.event for e in trace.events if e.event in {"done", "cancelled"}}
    if ends.get("S") != "cancelled":
        pytest.fail(f"task S did not end cancelled: {ends}")


def test_init_globals_reach_the_script_and_lines_are_not_capped(
    tmp_path: Path,
) -> None:
    """`init_globals` seed the script; `lines_run` keeps function lines, uncapped."""
    script = tmp_path / "script.py"
    script.write_text(
        "def total(size):\n    result = 0\n    for n in range(size):\n"
        "        result += n\n    return result\nseen.append(total(SIZE))\n"
    )
    seen: list[int] = []

    trace = record(
        script, {script}, max_events=1, init_globals={"SIZE": 4, "seen": seen}
    )

    if seen != [6]:
        pytest.fail(f"the script saw {seen}")
    if trace.lines_run != {str(script): {2, 3, 4, 5}}:
        pytest.fail(f"lines run are {trace.lines_run}")
    if len(trace.events) != 1 or not trace.truncated:
        pytest.fail("max_events did not cap the stored events")
