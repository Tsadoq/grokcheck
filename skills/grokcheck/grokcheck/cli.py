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
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, get_args

from grokcheck.client import ApiError, LessonClient, ServerGoneError
from grokcheck.grading import Outcome
from grokcheck.lesson import LessonError, load_lesson
from grokcheck.run import DEFAULT_RETAKE, LessonRun, RunError
from grokcheck.server import serve_forever

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from grokcheck.lesson import Lesson

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
    except (LessonError, RunError, ApiError, ServerGoneError, OSError) as error:
        _print({"ok": False, "error": str(error)})
        return 1
    if output is not None:
        _print(output)
    return 0


def _parser() -> _Parser:
    parser = _Parser(prog="grokcheck", description=__doc__)
    commands = parser.add_subparsers(required=True, metavar="command")

    validate = commands.add_parser("validate", help="check a lesson file")
    validate.add_argument("lesson", type=Path)
    _add_project(validate)
    validate.set_defaults(command=_validate)

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

    hosted = commands.add_parser("_server")
    hosted.add_argument("lesson_dir", type=Path)
    _add_server_options(hosted)
    hosted.set_defaults(command=_host)
    return parser


def _add_project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())


def _add_server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    _load(args.lesson, args.project)
    return {"ok": True}


def _serve(args: argparse.Namespace) -> dict[str, Any]:
    run = LessonRun.create(_load(args.lesson, args.project), args.project)
    return _launch(run, args)


def _retake(args: argparse.Namespace) -> dict[str, Any]:
    lesson = LessonRun.retake_from(_lesson_dir(args), args.include)
    return _launch(LessonRun.create(lesson, args.project), args)


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
