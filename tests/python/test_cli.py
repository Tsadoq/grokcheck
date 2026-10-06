"""The agent-facing CLI, driven as real subprocesses against a real detached server."""

import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from grokcheck.schedule import local_today

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


def test_ground_prints_the_manifest_then_counts_applied_verdicts(
    tmp_path: Path,
) -> None:
    """The subagent's verdicts land in the lesson and come back counted by result."""
    lesson_file = tmp_path / "lesson.json"
    shutil.copy(FIXTURES / "lessons" / "valid_full.json", lesson_file)
    project = str(FIXTURES / "project")

    listed = _grokcheck(tmp_path, "ground", str(lesson_file), "--project", project)
    manifest = cast("list[dict[str, Any]]", listed["manifest"])
    verdicts = [
        {"claim_id": manifest[0]["claim_id"], "verdict": "supported", "note": ""},
        {
            "claim_id": manifest[1]["claim_id"],
            "verdict": "not_shown",
            "note": "too short",
        },
    ]
    verdicts_file = tmp_path / "verdicts.json"
    verdicts_file.write_text(json.dumps(verdicts), encoding="utf-8")
    applied = _grokcheck(
        tmp_path,
        "ground",
        str(lesson_file),
        "--verdicts",
        str(verdicts_file),
        "--project",
        project,
    )

    expected = {
        "ok": True,
        "supported": 1,
        "contradicted": 0,
        "unchecked": 1,
        "notes": [verdicts[1]],
    }
    if applied != expected:
        pytest.fail(f"ground --verdicts printed {applied}")


def test_validate_warns_of_item_flaws_and_strict_fails_on_them(tmp_path: Path) -> None:
    """A loadable lesson with a flawed question passes with a warning.

    `--strict` turns the same warning into a failure, for an agent that must
    not hand a reader a question a guess can answer.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text())
    raw["final"].append(
        {
            "id": "final-twins",
            "type": "single_choice",
            "prompt": "Which call evicts; put or get?",
            "options": [
                {"text": "put", "why": "A write can overflow the cache."},
                {"text": "put", "why": "The same option twice."},
            ],
            "correct": 0,
        }
    )
    lesson = tmp_path / "lesson.json"
    lesson.write_text(json.dumps(raw))
    project = FIXTURES / "project"

    lenient = _grokcheck(project, "validate", str(lesson), "--project", str(project))
    strict = subprocess.run(  # noqa: S603
        [sys.executable, str(PACKAGE_DIR), "validate", str(lesson), "--strict"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    warnings = cast("list[dict[str, str]]", lenient["warnings"])
    twins = f"final[{len(raw['final']) - 1}]"
    if {"path": twins, "rule": "duplicate_option_text"} not in [
        {"path": w["path"], "rule": w["rule"]} for w in warnings
    ]:
        pytest.fail(f"validate printed {lenient}")
    if {"path": f"{twins}.prompt", "rule": "semicolon"} not in [
        {"path": w["path"], "rule": w["rule"]} for w in warnings
    ]:
        pytest.fail(f"validate printed no prose warning: {lenient}")
    problems = json.loads(strict.stdout).get("problems", [])
    if strict.returncode != 1 or not any(
        p["rule"] == "duplicate_option_text" for p in problems
    ):
        pytest.fail(f"validate --strict printed {strict.stdout}{strict.stderr}")


def _serve_and_submit(
    project: Path, served: dict[str, object], answers: dict[str, object]
) -> None:
    try:
        browser = _Browser(str(served["url"]))
        for question_id, response in answers.items():
            browser.post(
                "/api/answer", {"question_id": question_id, "response": response}
            )
        browser.post("/api/submit", {})
    finally:
        _grokcheck(project, "stop", str(served["lesson_id"]))


def test_due_serves_a_missed_question_and_a_correct_retake_moves_it_on(
    tmp_path: Path,
) -> None:
    """A missed final comes back through `due --serve`; answering it right reschedules.

    Due dates are moved to today in `schedule.json` to stand in for the
    day that passes before the item comes due.
    """
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson_file = tmp_path / "lesson.json"
    shutil.copy(FIXTURES / "lessons" / "valid_full.json", lesson_file)
    served = _grokcheck(project, "serve", str(lesson_file), "--no-open")
    _serve_and_submit(project, served, {**_FINAL_ANSWERS, "final-blank": "wrong"})
    schedule_file = project / ".grokcheck" / "schedule.json"
    entries = json.loads(schedule_file.read_text(encoding="utf-8"))
    today = local_today().isoformat()
    schedule_file.write_text(
        json.dumps([{**entry, "due": today} for entry in entries]), encoding="utf-8"
    )

    listed = _grokcheck(project, "due")
    retake = _grokcheck(project, "due", "--serve", "--no-open")
    key = f"{served['lesson_id']}/final-blank"
    _serve_and_submit(project, retake, {key: _FINAL_ANSWERS["final-blank"]})
    after = json.loads(schedule_file.read_text(encoding="utf-8"))

    due_keys = [
        f"{entry['lesson_id']}/{entry['question_id']}"
        for entry in cast("list[dict[str, Any]]", listed["due"])
    ]
    if due_keys != [key] or listed["stale"] != []:
        pytest.fail(f"expected only {key} due, got {listed}")
    if [(e["question_id"], e["interval_index"]) for e in after] != [("final-blank", 1)]:
        pytest.fail(f"expected final-blank one step up the ladder, got {after}")
