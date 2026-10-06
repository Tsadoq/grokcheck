"""The localhost server's request guard, exercised over real HTTP."""

import dataclasses
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
from grokcheck.lesson import (
    DiffElement,
    DiffLine,
    FixTheBug,
    Lesson,
    LineAsk,
    Mutation,
    VideoElement,
    load_lesson,
)
from grokcheck.mutate import plant, remove
from grokcheck.run import LessonRun
from grokcheck.server import serve_forever

FIXTURES = Path(__file__).parent.parent / "fixtures"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(
    url: str, headers: dict[str, str], data: bytes | None = None
) -> urllib.request.Request:
    return urllib.request.Request(url, data=data, headers=headers)  # noqa: S310


def _status(request: urllib.request.Request) -> int:
    try:
        with _NO_PROXY.open(request, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


def _published_session(lesson_dir: Path) -> dict[str, object]:
    session_file = lesson_dir / "session.json"
    deadline = time.monotonic() + 5
    while not session_file.exists():
        if time.monotonic() > deadline:
            pytest.fail("the server never wrote session.json")
        time.sleep(0.02)
    session: dict[str, object] = json.loads(session_file.read_text(encoding="utf-8"))
    return session


def _fixture_lesson(tmp_path: Path) -> tuple[Lesson, Path]:
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    return load_lesson(FIXTURES / "lessons" / "valid_full.json", project), project


@contextmanager
def served_run(
    run: LessonRun, web_dir: Path
) -> Iterator[tuple[str, dict[str, str], dict[str, str]]]:
    """Serve `run`; yield its base URL, the Host header and the token header."""
    server = threading.Thread(target=serve_forever, args=(run, web_dir), daemon=True)
    server.start()
    session = _published_session(run.lesson_dir)
    base = f"http://127.0.0.1:{session['port']}"
    host_header = {"Host": f"127.0.0.1:{session['port']}"}
    token_header = {"X-Grokcheck-Token": str(session["token"])}
    try:
        yield base, host_header, token_header
    finally:
        _status(_request(f"{base}/api/stop", {**host_header, **token_header}, b""))
        server.join(timeout=5)


def test_foreign_host_or_missing_token_is_rejected_before_routing(
    tmp_path: Path,
) -> None:
    """A rebinding-style Host or a missing token cannot read or change the lesson."""
    lesson, project = _fixture_lesson(tmp_path)
    run = LessonRun.create(lesson, project)
    answer = json.dumps({"question_id": "cp-capacity", "response": 0}).encode()
    json_type = {"Content-Type": "application/json"}

    with served_run(run, tmp_path / "web") as (base, host_header, token_header):
        rebound = _status(
            _request(f"{base}/api/lesson", {"Host": "evil.example", **token_header})
        )
        tokenless = _status(
            _request(f"{base}/api/answer", {**host_header, **json_type}, answer)
        )
        allowed = _status(
            _request(
                f"{base}/api/answer",
                {**host_header, **json_type, **token_header},
                answer,
            )
        )

    if (rebound, tokenless, allowed) != (403, 403, 200):
        pytest.fail(
            "expected foreign Host 403, missing token 403, guarded answer 200; "
            f"got {rebound}, {tokenless}, {allowed}"
        )


def test_ask_line_returns_prepared_answer_and_lesson_view_hides_it(
    tmp_path: Path,
) -> None:
    """A line's prepared answer reaches the page only when asked, and is logged."""
    lesson, project = _fixture_lesson(tmp_path)
    diff = DiffElement(
        file="runs/stream.py",
        old_start=45,
        new_start=45,
        lines=(
            DiffLine(" ", "        yield event"),
            DiffLine("+", '        if event.kind == "finished":'),
            DiffLine("+", "            return"),
        ),
        asks=(
            LineAsk(
                47,
                "Does returning here leave the queue registered?",
                "Yes, as written. Nothing unsubscribes on return.",
            ),
        ),
    )
    first = lesson.sections[0]
    lesson = dataclasses.replace(
        lesson,
        sections=(
            dataclasses.replace(first, elements=(diff, *first.elements)),
            *lesson.sections[1:],
        ),
    )
    run = LessonRun.create(lesson, project)

    with served_run(run, tmp_path / "web") as (base, host_header, token_header):
        headers = {**host_header, **token_header}

        def call(path: str, body: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
            data = None if body is None else json.dumps(body).encode()
            json_type = {} if body is None else {"Content-Type": "application/json"}
            request = _request(f"{base}{path}", {**headers, **json_type}, data)
            with _NO_PROXY.open(request, timeout=5) as response:
                return json.loads(response.read())

        view = call("/api/lesson")
        asked = call("/api/ask-line", {"element_id": "d1", "line": 47})
        unprepared = call("/api/ask-line", {"element_id": "d1", "line": 46})

    shown = view["sections"][0]["elements"][0]["asks"][0]
    events = (run.lesson_dir / "events.jsonl").read_text(encoding="utf-8")
    last = json.loads(events.splitlines()[-1])["type"]
    if shown != {"line": 47, "question": diff.asks[0].question}:
        pytest.fail(f"the lesson view leaks or drops the ask: {shown}")
    if not asked.get("answer", "").startswith("Yes, as written"):
        pytest.fail(f"asking line 47 returned {asked}")
    if unprepared != {}:
        pytest.fail(f"a line without a prepared ask returned {unprepared}")
    if last != "ask_line":
        pytest.fail(f"the last logged event is '{last}', not 'ask_line'")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_api_run_executes_lesson_command_in_worktree_and_grades(
    tmp_path: Path,
) -> None:
    """The lesson's own test command grades the worktree; the request cannot swap it."""
    original = "    return a + b"
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "a.py").write_text(f"def add(a, b):\n{original}\n", encoding="utf-8")
    (repo / "tests" / "test_a.py").write_text(
        "from a import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    lesson_command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_a.py",
    )
    mutant = plant(repo, "a.py", 2, "    return a - b", list(lesson_command))
    question = FixTheBug(
        id="fix-add",
        prompt="Make `add` add again.",
        mutation=Mutation("a.py", 2, original),
        worktree=str(mutant.worktree),
        test_command=lesson_command,
    )
    lesson, project = _fixture_lesson(tmp_path)
    first = lesson.sections[0]
    lesson = dataclasses.replace(
        lesson,
        sections=(
            dataclasses.replace(first, checkpoints=(*first.checkpoints, question)),
            *lesson.sections[1:],
        ),
    )
    run = LessonRun.create(lesson, project)
    injected = [sys.executable, "-c", "print('injected')"]

    try:
        with served_run(run, tmp_path / "web") as (base, host_header, token_header):
            headers = {
                **host_header,
                **token_header,
                "Content-Type": "application/json",
            }

            def post_run() -> Any:  # noqa: ANN401
                body = {
                    "question_id": "fix-add",
                    "confidence": "sure",
                    "test_command": injected,
                }
                request = _request(
                    f"{base}/api/run", headers, json.dumps(body).encode()
                )
                with _NO_PROXY.open(request, timeout=60) as response:
                    return json.loads(response.read())["grade"]

            forged = _status(
                _request(
                    f"{base}/api/answer",
                    headers,
                    json.dumps(
                        {"question_id": "fix-add", "response": {"passed": True}}
                    ).encode(),
                )
            )
            broken = post_run()
            (mutant.worktree / "a.py").write_text(
                f"def add(a, b):\n{original}\n", encoding="utf-8"
            )
            fixed = post_run()
    finally:
        remove(mutant.worktree)

    if forged != HTTPStatus.BAD_REQUEST:
        pytest.fail(f"/api/answer accepted a posted fix_the_bug result: {forged}")
    if broken["outcome"] != "incorrect" or "FAILED" not in broken["reveal"]["log"]:
        pytest.fail(f"the mutant run should fail with its log, got {broken}")
    if "test_a.py" not in broken["reveal"]["log"]:
        pytest.fail(f"the lesson's command did not run: {broken['reveal']['log']}")
    if fixed["outcome"] != "correct":
        pytest.fail(f"the fixed worktree should pass, got {fixed}")


def test_commit_reveals_the_reader_first_options_table(tmp_path: Path) -> None:
    """Committing the reader's own options returns the table and unhides it."""
    lesson, project = _fixture_lesson(tmp_path)
    run = LessonRun.create(lesson, project)

    with served_run(run, tmp_path / "web") as (base, host_header, token_header):
        headers = {**host_header, **token_header, "Content-Type": "application/json"}

        def call(path: str, body: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
            data = None if body is None else json.dumps(body).encode()
            request = _request(f"{base}{path}", headers, data)
            with _NO_PROXY.open(request, timeout=5) as response:
                return json.loads(response.read())

        before = call("/api/lesson")["sections"][3]["elements"][0]
        revealed = call(
            "/api/commit", {"element_id": "opt-cache", "text": "keep it; ops cost"}
        )
        after = call("/api/lesson")["sections"][3]["elements"][0]

    if "options" in before:
        pytest.fail(f"options table served before the commit: {before}")
    names = [option["name"] for option in revealed.get("options", [])]
    if len(names) != 3 or after.get("options") != revealed["options"]:  # noqa: PLR2004
        pytest.fail(f"commit revealed {revealed}, lesson then shows {after}")


def _media(url: str, headers: dict[str, str]) -> tuple[int, str | None, bytes]:
    try:
        with _NO_PROXY.open(_request(url, headers), timeout=5) as response:
            return (
                response.status,
                response.headers.get("Content-Range"),
                response.read(),
            )
    except urllib.error.HTTPError as error:
        return error.code, None, b""


def test_api_media_streams_a_video_range_with_the_token_in_the_query(
    tmp_path: Path,
) -> None:
    """A `<video>` fetches byte ranges of the lesson's own video, token in the URL.

    The query token is accepted only on `/api/media`, since only a media
    element cannot send the token header.
    """
    lesson, project = _fixture_lesson(tmp_path)
    film = tmp_path / "film.mp4"
    film.write_bytes(bytes(range(256)) * 4)
    video = VideoElement(
        id="film",
        src=str(film),
        captions=str(tmp_path / "film.vtt"),
        duration=3.0,
        transcript="Narration.",
    )
    first = lesson.sections[0]
    lesson = dataclasses.replace(
        lesson,
        sections=(
            dataclasses.replace(first, elements=(*first.elements, video)),
            *lesson.sections[1:],
        ),
    )
    run = LessonRun.create(lesson, project)

    with served_run(run, tmp_path / "web") as (base, host_header, token_header):
        token = token_header["X-Grokcheck-Token"]
        ranged = _media(
            f"{base}/api/media?element=film&t={token}",
            {**host_header, "Range": "bytes=10-19"},
        )
        lesson_by_query = _media(f"{base}/api/lesson?t={token}", host_header)
        unknown = _media(f"{base}/api/media?element=nope&t={token}", host_header)

    if ranged != (206, "bytes 10-19/1024", bytes(range(10, 20))):
        pytest.fail(f"expected bytes 10-19 of 1024 as a 206, got {ranged[:2]}")
    if lesson_by_query[0] != HTTPStatus.FORBIDDEN:
        pytest.fail(f"/api/lesson accepted the query token: {lesson_by_query[0]}")
    if unknown[0] != HTTPStatus.NOT_FOUND:
        pytest.fail(f"an unknown video id returned {unknown[0]}, not 404")
