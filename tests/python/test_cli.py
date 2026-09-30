"""The agent-facing CLI, driven as real subprocesses against a real detached server."""

import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"
PACKAGE_DIR = Path(__file__).parent.parent.parent / "skills" / "grokcheck" / "grokcheck"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_FINAL_ANSWERS = {
    "final-predict": "None",
    "final-predict-choice": 0,
    "final-order": [
        "Store the value under the key",
        "Move the key to the end",
        "Evict the oldest item if over capacity",
    ],
    "final-blank": "last=False",
    "final-why": {"text": "A read is a use.", "met": [True, True, True]},
}


def _grokcheck(project: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(PACKAGE_DIR), *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"grokcheck {args[0]} failed: {completed.stdout}{completed.stderr}")
    output: dict[str, object] = json.loads(completed.stdout)
    return output


class _Browser:
    """Plays the lesson page: calls the real API with the token from the URL."""

    def __init__(self, url: str) -> None:
        parts = urlsplit(url)
        self.base = f"{parts.scheme}://{parts.netloc}"
        self.token = parts.fragment.removeprefix("t=")

    def reply_texts(self) -> list[str]:
        request = urllib.request.Request(f"{self.base}/api/replies?after=0")  # noqa: S310
        replies: list[dict[str, str]] = self._send(request)["replies"]
        return [reply["markdown"] for reply in replies]

    def post(self, path: str, body: object) -> None:
        request = urllib.request.Request(  # noqa: S310
            f"{self.base}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        self._send(request)

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        request.add_header("X-Grokcheck-Token", self.token)
        with _NO_PROXY.open(request, timeout=10) as response:
            payload: dict[str, Any] = json.loads(response.read())
            return payload


def test_serve_wait_reply_submit_round_trip(tmp_path: Path) -> None:
    """An agent using only CLI commands answers a reader question, then sees submit."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson_file = tmp_path / "lesson.json"
    shutil.copy(FIXTURES / "lessons" / "valid_full.json", lesson_file)

    served = _grokcheck(project, "serve", str(lesson_file), "--no-open")
    lesson_id = str(served["lesson_id"])
    try:
        browser = _Browser(str(served["url"]))
        browser.post(
            "/api/question",
            {"section_id": "purpose", "text": "Why an OrderedDict here?"},
        )
        asked = _grokcheck(project, "wait", lesson_id, "--timeout", "30")
        _grokcheck(
            project,
            "reply",
            lesson_id,
            str(asked["question_id"]),
            "--text",
            "It keeps keys in use order.",
        )
        reply_texts = browser.reply_texts()
        for question_id, response in _FINAL_ANSWERS.items():
            browser.post(
                "/api/answer", {"question_id": question_id, "response": response}
            )
        browser.post("/api/submit", {})
        submitted = _grokcheck(project, "wait", lesson_id, "--timeout", "30")
    finally:
        _grokcheck(project, "stop", lesson_id)

    observed = (
        asked["type"],
        asked["text"],
        reply_texts,
        submitted["type"],
        Path(str(submitted.get("results_path"))).is_file(),
    )
    expected = (
        "question",
        "Why an OrderedDict here?",
        ["It keeps keys in use order."],
        "submitted",
        True,
    )
    if observed != expected:
        pytest.fail(
            "expected question event, the reply in /api/replies, then a submitted "
            f"event with an existing results file; got {asked}, {reply_texts}, "
            f"{submitted}"
        )
