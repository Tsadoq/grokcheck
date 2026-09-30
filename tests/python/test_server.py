"""The localhost server's request guard, exercised over real HTTP."""

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from grokcheck.lesson import load_lesson
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


def test_foreign_host_or_missing_token_is_rejected_before_routing(
    tmp_path: Path,
) -> None:
    """A rebinding-style Host or a missing token cannot read or change the lesson."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_full.json", project)
    run = LessonRun.create(lesson, project)
    server = threading.Thread(
        target=serve_forever, args=(run, tmp_path / "web"), daemon=True
    )
    server.start()
    session = _published_session(run.lesson_dir)
    base = f"http://127.0.0.1:{session['port']}"
    token_header = {"X-Grokcheck-Token": str(session["token"])}
    host_header = {"Host": f"127.0.0.1:{session['port']}"}
    answer = json.dumps({"question_id": "cp-capacity", "response": 0}).encode()
    json_type = {"Content-Type": "application/json"}

    try:
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
    finally:
        _status(_request(f"{base}/api/stop", {**host_header, **token_header}, b""))
        server.join(timeout=5)

    if (rebound, tokenless, allowed) != (403, 403, 200):
        pytest.fail(
            "expected foreign Host 403, missing token 403, guarded answer 200; "
            f"got {rebound}, {tokenless}, {allowed}"
        )
