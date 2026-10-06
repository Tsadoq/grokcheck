"""The `grokcheck` command line the agent drives; every command prints one JSON line.

`serve` and `retake` start a detached copy of this CLI running the internal
`_server` command, so the lesson outlives the agent's shell call. `wait`,
`reply` and `stop` then reach that server through `client.LessonClient`. Any
failure prints `{"ok": false, "error": ...}` and exits with status 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, get_args

from grokcheck import data, diagrams
from grokcheck.client import ApiError, LessonClient, ServerGoneError
from grokcheck.diffs import as_json, hunks
from grokcheck.export import render_anki, render_obsidian
from grokcheck.export_html import render_html
from grokcheck.grading import Outcome
from grokcheck.ground import VERDICTS, apply_verdicts, manifest, unsettled
from grokcheck.lesson import DiagramElement, LessonError, load_lesson
from grokcheck.lint import intro, item_flaws, media, prose
from grokcheck.mutate import MutationError, plant, remove
from grokcheck.refresh import report as refresh_report
from grokcheck.run import DEFAULT_RETAKE, LessonRun, RunError
from grokcheck.schedule import Schedule, local_today
from grokcheck.scope import infer
from grokcheck.server import serve_forever
from grokcheck.sources import (
    NeedsTranscription,
    ingest,
    manifest_entries,
    system_pdftotext,
)
from grokcheck.spikes import SpikeError, check_name, rerun, run, write
from grokcheck.trace import DEFAULT_MAX_EVENTS, record

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from grokcheck.lesson import Lesson
    from grokcheck.schedule import Entry

DEFAULT_WAIT_SECONDS = 1200.0
SESSION_WAIT_SECONDS = 10.0

_PACKAGE_DIR = Path(__file__).resolve().parent
_WEB_DIR = _PACKAGE_DIR.parent / "web"
_POLL_SECONDS = 300.0
_RETAKE_CHOICES = (*get_args(Outcome), "confident_wrong")


class CliError(Exception):
    """A failure reported to the agent as `{"ok": false, "error": message}`."""

    def __init__(self, message: str, **details: object) -> None:
        """Keep `details` to print alongside the message, such as `problems`."""
        self.details = details
        super().__init__(message)


class _Parser(argparse.ArgumentParser):
    """An argument parser that reports usage errors as JSON, like every failure."""

    def error(self, message: str) -> NoReturn:
        raise CliError(message)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return the process exit status."""
    try:
        args = _parser().parse_args(argv)
        command: Callable[[argparse.Namespace], dict[str, Any] | None] = args.command
        output = command(args)
    except CliError as error:
        _print({"ok": False, "error": str(error), **error.details})
        return 1
    except data.DataError as error:
        problems = [{"path": p, "message": m} for p, m in error.problems]
        _print({"ok": False, "error": "the dataset was refused", "problems": problems})
        return 1
    except (LessonError, RunError, ApiError, ServerGoneError, OSError) as error:
        _print({"ok": False, "error": str(error)})
        return 1
    if output is not None:
        _print(output)
    return 0


def _parser() -> _Parser:  # noqa: PLR0915
    parser = _Parser(prog="grokcheck", description=__doc__)
    commands = parser.add_subparsers(required=True, metavar="command")

    validate = commands.add_parser("validate", help="check a lesson file")
    validate.add_argument("lesson", type=Path)
    validate.add_argument("--strict", action="store_true")
    _add_project(validate)
    validate.set_defaults(command=_validate)

    ground = commands.add_parser("ground", help="list claims or record verdicts")
    ground.add_argument("lesson", type=Path)
    ground.add_argument("--verdicts", type=Path)
    ground.add_argument("--all", action="store_true")
    _add_project(ground)
    ground.set_defaults(command=_ground)

    serve = commands.add_parser("serve", help="start a lesson in the browser")
    serve.add_argument("lesson", type=Path)
    _add_project(serve)
    _add_server_options(serve)
    serve.set_defaults(command=_serve)

    wait = commands.add_parser("wait", help="print the next reader event")
    wait.add_argument("lesson_id")
    wait.add_argument("--timeout", type=float, default=DEFAULT_WAIT_SECONDS)
    _add_project(wait)
    wait.set_defaults(command=_wait)

    reply = commands.add_parser("reply", help="answer a reader question")
    reply.add_argument("lesson_id")
    reply.add_argument("question_id")
    body = reply.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--file", type=Path)
    _add_project(reply)
    reply.set_defaults(command=_reply)

    stop = commands.add_parser("stop", help="shut down a lesson's server")
    stop.add_argument("lesson_id")
    _add_project(stop)
    stop.set_defaults(command=_stop)

    retake = commands.add_parser("retake", help="serve a retake of a submitted lesson")
    retake.add_argument("lesson_id")
    retake.add_argument(
        "--include", nargs="+", choices=_RETAKE_CHOICES, default=sorted(DEFAULT_RETAKE)
    )
    _add_project(retake)
    _add_server_options(retake)
    retake.set_defaults(command=_retake)

    due = commands.add_parser("due", help="list or serve the re-tests now due")
    due.add_argument("--serve", action="store_true")
    _add_project(due)
    _add_server_options(due)
    due.set_defaults(command=_due)

    scope = commands.add_parser("scope", help="guess a lesson's subject and files")
    scope.add_argument("words", nargs="*")
    _add_project(scope)
    scope.set_defaults(command=_scope)

    ingest_parser = commands.add_parser("ingest", help="copy documents into sources")
    ingest_parser.add_argument("origins", nargs="+")
    _add_project(ingest_parser)
    ingest_parser.set_defaults(command=_ingest)

    sources = commands.add_parser("sources", help="list the ingested sources")
    _add_project(sources)
    sources.set_defaults(command=_sources)

    _add_trace(commands)

    _add_data(commands)

    _add_spike(commands)

    _add_mutate(commands)

    _add_export(commands)

    refresh = commands.add_parser("refresh", help="list claims that no longer hold")
    refresh.add_argument("lesson", type=Path)
    _add_project(refresh)
    refresh.set_defaults(command=_reporting_spike_errors(_refresh))

    _add_diagram(commands)

    diff = commands.add_parser("diff", help="print a file's hunks as diff elements")
    diff.add_argument("path")
    diff.add_argument("--old", required=True)
    diff.add_argument("--new", required=True)
    _add_project(diff)
    diff.set_defaults(command=_diff_hunks)

    hosted = commands.add_parser("_server")
    hosted.add_argument("lesson_dir", type=Path)
    _add_server_options(hosted)
    hosted.set_defaults(command=_host)
    return parser


def _add_trace(commands: argparse._SubParsersAction[_Parser]) -> None:
    trace = commands.add_parser("trace", help="record traces of real runs")
    trace_commands = trace.add_subparsers(required=True, metavar="command")
    trace_record = trace_commands.add_parser("record", help="record a script's run")
    trace_record.add_argument("script", type=Path)
    trace_record.add_argument("--cite", nargs="+", type=Path, required=True)
    trace_record.add_argument("--entry")
    trace_record.add_argument("--out", type=Path)
    trace_record.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS)
    _add_project(trace_record)
    trace_record.set_defaults(command=_trace_record)


def _add_data(commands: argparse._SubParsersAction[_Parser]) -> None:
    rec = commands.add_parser("record", help="record a driver's rows as a dataset")
    rec.add_argument("driver", type=Path, nargs="?")
    rec.add_argument("--from-trace", type=Path)
    rec.add_argument("--id", required=True)
    rec.add_argument("--cite", nargs="+", default=[])
    rec.add_argument(
        "--matrix", nargs="+", action="extend", default=[], metavar="NAME=VALUES"
    )
    rec.add_argument("--entry")
    rec.add_argument("--timeout", type=float, default=data.DEFAULT_TIMEOUT)
    rec.add_argument("--python", metavar="INTERPRETER")
    _add_project(rec)
    rec.set_defaults(command=_record)

    data_parser = commands.add_parser("data", help="show, author or corrupt datasets")
    data_commands = data_parser.add_subparsers(required=True, metavar="command")
    show = data_commands.add_parser("show", help="summarise a dataset")
    show.add_argument("dataset_id")
    show.add_argument("--where", type=_json_object, default={})
    show.add_argument("--limit", type=int, default=5)
    _add_project(show)
    show.set_defaults(
        command=lambda a: data.show(a.project, a.dataset_id, a.where, a.limit)
    )
    author = data_commands.add_parser("author", help="write rows typed by hand")
    author.add_argument("dataset_id")
    author.add_argument("--file", type=Path, required=True)
    author.add_argument("--reason", required=True)
    _add_project(author)
    author.set_defaults(
        command=lambda a: data.author(a.project, a.dataset_id, a.file, a.reason)
    )
    wrong = data_commands.add_parser("wrong", help="copy a dataset with edits")
    wrong.add_argument("dataset_id")
    wrong.add_argument("--out", required=True)
    wrong.add_argument(
        "--edit", nargs=2, action="append", required=True, metavar=("SELECTOR", "F=V")
    )
    _add_project(wrong)
    wrong.set_defaults(
        command=lambda a: data.wrong(a.project, a.dataset_id, a.out, a.edit)
    )


def _json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        msg = f"'{text}' is not JSON: {error}"
        raise argparse.ArgumentTypeError(msg) from error
    if not isinstance(value, dict):
        msg = f"'{text}' must be a JSON object"
        raise argparse.ArgumentTypeError(msg)
    return value


def _record(args: argparse.Namespace) -> dict[str, Any]:
    """Record a driver over the matrix, or flatten `--from-trace` into rows."""
    if (args.driver is None) == (args.from_trace is None):
        msg = "record needs either <driver.py> or --from-trace <trace.json>"
        raise CliError(msg)
    if args.from_trace is not None:
        return data.from_trace(args.project, args.from_trace, args.id)
    return data.record(
        args.project,
        args.driver,
        args.id,
        args.cite,
        matrix=data.parse_matrix(args.matrix),
        entry=args.entry,
        timeout=args.timeout,
        python=args.python,
    )


def _add_spike(commands: argparse._SubParsersAction[_Parser]) -> None:
    spike = commands.add_parser("spike", help="run one-question experiments")
    spike_commands = spike.add_subparsers(required=True, metavar="command")

    check = spike_commands.add_parser("check", help="ask PyPI whether a name exists")
    check.add_argument("name")
    check.set_defaults(command=_reporting_spike_errors(_spike_check))

    new = spike_commands.add_parser("new", help="write a spike script")
    new.add_argument("spike_id")
    new.add_argument("--hypothesis", required=True)
    new.add_argument("--dep", action="append", default=[], metavar="NAME==VERSION")
    new.add_argument("--exclude-newer", required=True)
    code = new.add_mutually_exclusive_group(required=True)
    code.add_argument("--code")
    code.add_argument("--file", type=Path)
    _add_project(new)
    new.set_defaults(command=_reporting_spike_errors(_spike_new))

    run_spike = spike_commands.add_parser("run", help="run a spike and record it")
    run_spike.add_argument("spike_id")
    run_spike.add_argument("--container", action="store_true")
    run_spike.add_argument("--allow", action="append", default=[], metavar="NAME")
    _add_project(run_spike)
    run_spike.set_defaults(command=_reporting_spike_errors(_spike_run))

    rerun_spikes = spike_commands.add_parser("rerun", help="list spikes that changed")
    _add_project(rerun_spikes)
    rerun_spikes.set_defaults(command=_reporting_spike_errors(_spike_rerun))


def _add_diagram(commands: argparse._SubParsersAction[_Parser]) -> None:
    diagram = commands.add_parser("diagram", help="check a lesson's diagrams")
    diagram_commands = diagram.add_subparsers(required=True, metavar="command")
    check = diagram_commands.add_parser("check", help="warn on and render diagrams")
    check.add_argument("lesson", type=Path)
    _add_project(check)
    check.set_defaults(command=_diagram_check)


def _diagram_check(args: argparse.Namespace) -> dict[str, Any]:
    """Warn on each diagram, then render it with `mmdc` when that is installed."""
    lesson = _load(args.lesson, args.project)
    found = [
        (f"sections[{i}].elements[{j - section.code_sugar}].mermaid", element.mermaid)
        for i, section in enumerate(lesson.sections)
        for j, element in enumerate(section.elements)
        if isinstance(element, DiagramElement)
    ]
    warnings = [
        {"path": path, "message": message}
        for path, text in found
        for message in diagrams.warnings(text, args.project)
    ]
    if shutil.which("mmdc") is None:
        return {"ok": True, "warnings": warnings, "skipped": "mmdc not installed"}
    failures = [
        {"path": path, "message": error}
        for path, text in found
        if (error := diagrams.render_error(text))
    ]
    if failures:
        msg = "a diagram does not render"
        raise CliError(msg, problems=failures, warnings=warnings)
    return {"ok": True, "warnings": warnings}


def _add_export(commands: argparse._SubParsersAction[_Parser]) -> None:
    export = commands.add_parser("export", help="write a run's misses as Anki cards")
    export.add_argument("lesson_id")
    export.add_argument(
        "--format", choices=("anki", "obsidian", "html"), default="anki"
    )
    export.add_argument("--out", type=Path)
    export.add_argument("--fragment", action="store_true")
    export.add_argument("--vault", type=Path)
    export.add_argument("--template", type=Path)
    _add_project(export)
    export.set_defaults(command=_export)


def _export(args: argparse.Namespace) -> dict[str, Any]:
    """Write the Anki file, by default next to `results.json`, and count its cards."""
    lesson_dir = _lesson_dir(args)
    if args.format == "html":
        return _export_html(args, lesson_dir)
    results_path = lesson_dir / "results.json"
    if not results_path.exists():
        msg = "the lesson has not been submitted yet"
        raise CliError(msg)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    head = subprocess.run(  # noqa: S603
        ["git", "-C", str(args.project), "rev-parse", "--short", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    commit = head.stdout.strip() if head.returncode == 0 else "unknown"
    lesson = LessonRun.open(lesson_dir).lesson
    if args.format == "obsidian":
        return _export_obsidian(args, lesson, results, commit)
    text = render_anki(lesson, results, args.project.resolve().name, commit)
    out: Path = args.out or lesson_dir / "anki.txt"
    out.write_text(text, encoding="utf-8")
    cards = sum(1 for line in text.splitlines() if not line.startswith("#"))
    return {"ok": True, "path": str(out), "cards": cards}


def _export_html(args: argparse.Namespace, lesson_dir: Path) -> dict[str, Any]:
    """Write the lesson as one offline HTML page, by default `lesson.html`."""
    page = render_html(LessonRun.open(lesson_dir), _WEB_DIR, fragment=args.fragment)
    out: Path = args.out or lesson_dir / "lesson.html"
    out.write_text(page.text, encoding="utf-8")
    output: dict[str, Any] = {
        "ok": True,
        "path": str(out),
        "bytes": out.stat().st_size,
        "shrunk": page.shrunk,
        "dropped": page.dropped,
    }
    if page.shrunk:
        output["note"] = "videos re-encoded at 720p to keep the file small"
    if page.dropped:
        output["note"] = "videos left out to keep the file small; transcripts stay"
    return output


def _export_obsidian(
    args: argparse.Namespace, lesson: Lesson, results: dict[str, Any], commit: str
) -> dict[str, Any]:
    if args.vault is None:
        msg = "--format obsidian needs --vault <folder>"
        raise CliError(msg)
    template = args.template.read_text(encoding="utf-8") if args.template else None
    repo = args.project.resolve().name
    try:
        text = render_obsidian(lesson, results, repo, commit, template)
    except (KeyError, IndexError, ValueError) as error:
        msg = f"the template does not format: {error!r}"
        raise CliError(msg) from error
    return {"ok": True, "path": str(write_note(args.vault, lesson.title, text))}


def write_note(vault: Path, title: str, text: str) -> Path:
    """Write `text` as a new note `grokcheck - <title> - <date>.md` in `vault`.

    Raises `CliError` rather than overwrite a note that already exists.
    """
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", " ".join(title.split()))
    path = vault / f"grokcheck - {name} - {local_today().isoformat()}.md"
    try:
        with path.open("x", encoding="utf-8") as note:
            note.write(text)
    except FileExistsError as error:
        msg = f"{path} already exists"
        raise CliError(msg) from error
    return path


def _add_mutate(commands: argparse._SubParsersAction[_Parser]) -> None:
    mutate = commands.add_parser("mutate", help="plant a mutant that a test catches")
    mutate.add_argument("file", nargs="?")
    mutate.add_argument("line", type=int, nargs="?")
    mutate.add_argument("--replace")
    mutate.add_argument("--remove", type=Path, metavar="WORKTREE")
    mutate.add_argument("--test", nargs=argparse.REMAINDER, default=[])
    _add_project(mutate)
    mutate.set_defaults(command=_mutate)


def _mutate(args: argparse.Namespace) -> dict[str, Any]:
    """Plant a mutant and print its run, or `--remove` a planted worktree.

    `--test` takes the rest of the command line, so it comes last.
    """
    planting = args.file is not None and args.line is not None and args.test
    if args.remove is None and not planting:
        msg = "mutate needs <file> <line> --test <command...>, or --remove <worktree>"
        raise CliError(msg)
    try:
        if args.remove is not None:
            remove(args.remove)
            return {"ok": True}
        result = plant(args.project, args.file, args.line, args.replace, args.test)
    except MutationError as error:
        raise CliError(str(error)) from error
    return {"ok": True, **asdict(result), "worktree": str(result.worktree)}


def _add_project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())


def _add_server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    """Load and lint the lesson; `--strict` fails on any warning."""
    lesson = _load(args.lesson, args.project)
    warnings = [
        asdict(w) for lint in (item_flaws, prose, intro, media) for w in lint(lesson)
    ]
    if args.strict and warnings:
        msg = "the lesson has lint warnings"
        raise CliError(msg, problems=warnings)
    return {"ok": True, "warnings": warnings}


def _ground(args: argparse.Namespace) -> dict[str, Any]:
    """Print the claims still to judge, or apply `--verdicts` and count them.

    The manifest leaves out claims whose verdict still matches their hash,
    unless `--all`. `notes` keeps every non-empty verdict note, since the
    lesson has no place for one.
    """
    if args.verdicts is None:
        lesson = _load(args.lesson, args.project)
        entries = manifest(lesson, args.project)
        if not args.all:
            entries = unsettled(entries, args.lesson, args.project)
        return {"ok": True, "manifest": entries}
    try:
        verdicts: list[dict[str, Any]] = json.loads(args.verdicts.read_text("utf-8"))
    except json.JSONDecodeError as error:
        msg = f"the verdicts file is not valid JSON: {error}"
        raise CliError(msg) from error
    if not isinstance(verdicts, list) or not all(isinstance(v, dict) for v in verdicts):
        msg = "the verdicts file must hold a list of objects"
        raise CliError(msg)
    apply_verdicts(args.lesson, verdicts, args.project)
    counts = dict.fromkeys(("supported", "contradicted", "unchecked"), 0)
    for verdict in verdicts:
        counts[VERDICTS[verdict["verdict"]]] += 1
    notes = [
        {key: verdict[key] for key in ("claim_id", "verdict", "note")}
        for verdict in verdicts
        if verdict.get("note")
    ]
    return {"ok": True, **counts, "notes": notes}


def _serve(args: argparse.Namespace) -> dict[str, Any]:
    run = LessonRun.create(_load(args.lesson, args.project), args.project)
    return _launch(run, args)


def _retake(args: argparse.Namespace) -> dict[str, Any]:
    lesson = LessonRun.retake_from(_lesson_dir(args), args.include)
    return _launch(LessonRun.create(lesson, args.project), args)


def _due(args: argparse.Namespace) -> dict[str, Any]:
    """Print the due and the stale entries, or with `--serve` launch the due ones."""
    due, stale = Schedule.load(args.project).partition(local_today())
    if not args.serve:
        return {"due": [asdict(e) for e in due], "stale": [asdict(e) for e in stale]}
    return _launch(LessonRun.create(_due_lesson(args.project, due), args.project), args)


def _due_lesson(project: Path, due: Sequence[Entry]) -> Lesson:
    """Merge the due final questions of every source lesson into one retake.

    Each question takes its schedule key as id, so its submit counts as a
    review. Due gate questions need their trace and are only listed.
    """
    keys = {(entry.lesson_id, entry.question_id): entry.key for entry in due}
    lessons_dir = project / ".grokcheck" / "lessons"
    retakes = [
        (lesson_id, LessonRun.retake_from(lessons_dir / lesson_id, _RETAKE_CHOICES))
        for lesson_id in dict.fromkeys(entry.lesson_id for entry in due)
    ]
    final = tuple(
        replace(question, id=keys[lesson_id, question.id])
        for lesson_id, lesson in retakes
        for question in lesson.final
        if (lesson_id, question.id) in keys
    )
    if not final:
        msg = "no final question is due"
        raise CliError(msg)
    first = retakes[0][1]
    files = dict.fromkeys(f for _, lesson in retakes for f in lesson.scope.files)
    return replace(
        first,
        title="Retake: questions due for review",
        scope=replace(
            first.scope, summary="Questions due for review.", files=tuple(files)
        ),
        final=final,
    )


def _scope(args: argparse.Namespace) -> dict[str, Any]:
    return asdict(infer(args.words, args.project))


def _ingest(args: argparse.Namespace) -> None:
    """Print one line per origin, as each is ingested."""
    pdftotext = system_pdftotext()
    for origin in args.origins:
        result = ingest(origin, args.project, pdftotext=pdftotext)
        if isinstance(result, NeedsTranscription):
            _print(
                {
                    "needs_transcription": True,
                    "target": str(result.target_path),
                    "origin": origin,
                    "page_count_hint": result.page_count_hint,
                }
            )
        else:
            _print(result.as_json(args.project))


def _sources(args: argparse.Namespace) -> dict[str, Any]:
    return {"sources": manifest_entries(args.project)}


def _trace_record(args: argparse.Namespace) -> dict[str, Any]:
    """Record `script` and write the trace under `.grokcheck/traces/` or `--out`."""
    try:
        trace = record(args.script, set(args.cite), args.entry, args.max_events)
    except Exception as error:
        msg = f"the traced script raised {error!r}"
        raise CliError(msg) from error
    out: Path | None = args.out
    if out is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        trace_id = f"{stamp}-{secrets.token_hex(3)}"
        out = args.project / ".grokcheck" / "traces" / f"{trace_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trace.to_json()), encoding="utf-8")
    return {
        "ok": True,
        "trace_id": out.stem,
        "steps": len(trace.steps),
        "truncated": trace.truncated,
    }


def _refresh(args: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, **asdict(refresh_report(args.lesson, args.project))}


def _diff_hunks(args: argparse.Namespace) -> dict[str, Any]:
    try:
        elements = hunks(args.project, args.path, args.old, args.new)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        msg = f"git diff failed: {str(error.stderr or error).strip()}"
        raise CliError(msg) from error
    return {"ok": True, "elements": [as_json(element) for element in elements]}


def _wait(args: argparse.Namespace) -> dict[str, Any]:
    """Return the first `question` or `submitted` event past the saved cursor.

    The cursor moves past an event only after it is returned, so a `wait`
    killed mid-poll loses nothing and the next one resumes where it left off.
    """
    lesson_dir = _lesson_dir(args)
    cursor_file = lesson_dir / "wait.cursor"
    cursor = int(cursor_file.read_text(encoding="utf-8")) if cursor_file.exists() else 0
    deadline = time.monotonic() + args.timeout
    try:
        client = LessonClient(lesson_dir)
        events: list[dict[str, Any]] = []
        while not events and (remaining := deadline - time.monotonic()) > 0:
            events = client.events_after(cursor, timeout=min(remaining, _POLL_SECONDS))
    except ServerGoneError:
        logged = LessonRun.open(lesson_dir).events_after(cursor, timeout=0)
        events = [event.as_json() for event in logged]
        if not events:
            return {"type": "closed"}
    if not events:
        return {"type": "timeout"}
    event = events[0]
    cursor_file.write_text(str(event["seq"]), encoding="utf-8")
    return event


def _reply(args: argparse.Namespace) -> dict[str, Any]:
    markdown = args.text if args.file is None else args.file.read_text(encoding="utf-8")
    reply = LessonClient(_lesson_dir(args)).reply(args.question_id, markdown)
    return {"ok": True, "question_id": reply["question_id"], "seq": reply["seq"]}


def _stop(args: argparse.Namespace) -> dict[str, Any]:
    """Stop the server; stopping one that is already gone also succeeds."""
    try:
        LessonClient(_lesson_dir(args)).stop()
    except ServerGoneError:
        return {"ok": True, "was_running": False}
    return {"ok": True, "was_running": True}


def _host(args: argparse.Namespace) -> None:
    """Serve the run in `lesson_dir` until it stops; the detached child runs this."""
    run = LessonRun.open(args.lesson_dir)
    if not args.no_open and _has_display():
        threading.Thread(
            target=_open_browser_when_published, args=(run.lesson_dir,), daemon=True
        ).start()
    serve_forever(run, _WEB_DIR, args.port)


def _load(lesson_file: Path, project: Path) -> Lesson:
    try:
        return load_lesson(lesson_file, project)
    except LessonError as error:
        problems = [
            {"path": problem.path, "message": problem.message}
            for problem in error.problems
        ]
        msg = "the lesson is invalid"
        raise CliError(msg, problems=problems) from error


def _lesson_dir(args: argparse.Namespace) -> Path:
    lesson_id: str = args.lesson_id
    project: Path = args.project
    lesson_dir = project / ".grokcheck" / "lessons" / lesson_id
    if Path(lesson_id).name != lesson_id:
        msg = f"lesson id '{lesson_id}' must be a directory name, not a path"
        raise CliError(msg)
    if not lesson_dir.is_dir():
        msg = f"no lesson '{lesson_id}' in {project}"
        raise CliError(msg)
    return lesson_dir


def _reporting_spike_errors(
    handler: Callable[[argparse.Namespace], dict[str, Any]],
) -> Callable[[argparse.Namespace], dict[str, Any]]:
    def command(args: argparse.Namespace) -> dict[str, Any]:
        try:
            return handler(args)
        except SpikeError as error:
            raise CliError(str(error)) from error

    return command


def _spike_check(args: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, **asdict(check_name(args.name))}


def _spike_new(args: argparse.Namespace) -> dict[str, Any]:
    """Write the spike once every `--dep` is an exact pin of a name PyPI knows."""
    deps: dict[str, str] = {}
    for dep in args.dep:
        name, separator, version = dep.partition("==")
        if not (name and separator and version):
            msg = f"dependency '{dep}' must be pinned as NAME==VERSION"
            raise CliError(msg)
        if not check_name(name).exists:
            msg = f"'{name}' is not on PyPI"
            raise CliError(msg)
        deps[name] = version
    code = args.code if args.file is None else args.file.read_text(encoding="utf-8")
    script = write(_spike_dir(args), args.hypothesis, code, deps, args.exclude_newer)
    return {"ok": True, "spike_id": args.spike_id, "script": str(script)}


def _spike_run(args: argparse.Namespace) -> dict[str, Any]:
    result = run(
        _spike_dir(args), args.project, container=args.container, allow=args.allow
    )
    return {"ok": True, **asdict(result)}


def _spike_rerun(args: argparse.Namespace) -> dict[str, Any]:
    changes = rerun(args.project / ".grokcheck" / "spikes", args.project)
    return {"ok": True, "changed": [asdict(change) for change in changes]}


def _spike_dir(args: argparse.Namespace) -> Path:
    spike_id: str = args.spike_id
    if Path(spike_id).name != spike_id:
        msg = f"spike id '{spike_id}' must be a directory name, not a path"
        raise CliError(msg)
    project: Path = args.project
    return project / ".grokcheck" / "spikes" / spike_id


def _launch(run: LessonRun, args: argparse.Namespace) -> dict[str, Any]:
    """Start the detached `_server` for `run` and return where the reader goes."""
    command = [sys.executable, str(_PACKAGE_DIR), "_server", str(run.lesson_dir)]
    command += ["--port", str(args.port)]
    if args.no_open:
        command.append("--no-open")
    with (run.lesson_dir / "server.log").open("ab") as log:
        child = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **_detached(),
        )
    url = _published_url(run.lesson_dir, alive=lambda: child.poll() is None)
    if url is None:
        child.kill()
        msg = f"the lesson server did not start; see {run.lesson_dir / 'server.log'}"
        raise CliError(msg)
    return {
        "lesson_id": run.lesson_id,
        "url": url,
        "lesson_dir": str(run.lesson_dir),
    }


def _detached() -> dict[str, Any]:
    """Popen options that keep the server alive after the agent's shell exits."""
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        }
    return {"start_new_session": True}


def _published_url(lesson_dir: Path, *, alive: Callable[[], bool]) -> str | None:
    """Wait for the server to publish `session.json` and return the reader's URL.

    Returns `None` once `alive` turns false or `SESSION_WAIT_SECONDS` pass.
    """
    session_file = lesson_dir / "session.json"
    deadline = time.monotonic() + SESSION_WAIT_SECONDS
    while not session_file.exists():
        if not alive() or time.monotonic() > deadline:
            return None
        time.sleep(0.05)
    url: str = json.loads(session_file.read_text(encoding="utf-8"))["url"]
    return url


def _has_display() -> bool:
    if sys.platform != "linux":
        return True
    return any(
        os.environ.get(name) for name in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER")
    )


def _open_browser_when_published(lesson_dir: Path) -> None:
    url = _published_url(lesson_dir, alive=lambda: True)
    if url is not None:
        webbrowser.open(url)


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value), flush=True)  # noqa: T201
