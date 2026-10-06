"""Guessing a lesson's subject from the shape of the user's argument."""

import json
import subprocess
from pathlib import Path

import pytest
from grokcheck.lesson import LessonError, Problem, load_lesson
from grokcheck.scope import infer

_UV_LOCK = """\
version = 1

[[package]]
name = "httpx"
version = "0.28.1"
source = { registry = "https://pypi.org/simple" }
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )


def test_infer_subject_from_input_shape(tmp_path: Path) -> None:
    """A range, a directory, a locked package and a `vs` phrase each pick a subject.

    The second commit touches only `pkg/core.py`, so the range's file list
    must be exactly that file.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "first")
    (tmp_path / "pkg" / "core.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "commit", "--quiet", "-am", "second")

    commit_range = infer(["HEAD~1..HEAD"], tmp_path)
    directory = infer(["pkg/"], tmp_path)
    package = infer(["httpx"], tmp_path)
    comparison = infer(["redis", "vs", "sqlite"], tmp_path)

    if (commit_range.subject, commit_range.files) != ("change", ("pkg/core.py",)):
        pytest.fail(f"commit range: {commit_range}")
    if directory.subject != "area":
        pytest.fail(f"directory: {directory}")
    if (package.subject, package.pins) != ("library", {"httpx": "0.28.1"}):
        pytest.fail(f"locked package: {package}")
    if comparison.subject != "options":
        pytest.fail(f"comparison: {comparison}")


def test_document_inputs_infer_document_subject_and_forbid_code_media(
    tmp_path: Path,
) -> None:
    """A paper or a URL is a document lesson, and a document lesson has no code media.

    The URL is only classified here, never fetched; `ingest` fetches it later.
    """
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    choice = {
        "id": "q",
        "type": "single_choice",
        "prompt": "Which?",
        "options": [{"text": "a", "why": "first"}, {"text": "b", "why": "second"}],
        "correct": 0,
    }
    raw = {
        "schema_version": 2,
        "title": "A paper",
        "plan": {
            "subject": "document",
            "time_budget": 15,
            "content": ["behaviour"],
            "media": ["trace"],
            "rejected": [],
            "rationale": ["A paper read for its claim."],
            "default_depth": "short",
        },
        "scope": {"summary": "One paper.", "files": []},
        "sections": [
            {
                "id": "claim",
                "title": "The claim",
                "body": "Lead prose.",
                "elements": [{"type": "prose", "markdown": "The result."}],
                "checkpoints": [{**choice, "id": "cp"}],
            }
        ],
        "final": [{**choice, "id": f"f-{n}"} for n in range(3)],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    guess = infer(["paper.pdf", "https://example.org/guide"], tmp_path)
    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, tmp_path)

    if (guess.subject, guess.sources) != (
        "document",
        ("paper.pdf", "https://example.org/guide"),
    ):
        pytest.fail(f"document guess: {guess}")
    expected = [
        Problem(
            "plan.media[0]",
            "'trace' is code-only and cannot appear in a document lesson",
        )
    ]
    if list(caught.value.problems) != expected:
        pytest.fail(f"problems are {caught.value.problems}")


def test_malformed_dependency_files_name_no_packages(tmp_path: Path) -> None:
    """A lockfile or pyproject that is not TOML is read as naming nothing."""
    for name in ("uv.lock", "poetry.lock", "pyproject.toml"):
        (tmp_path / name).write_text("[[package\nname = ", encoding="utf-8")

    guess = infer(["httpx"], tmp_path)

    if guess.pins != {}:
        pytest.fail(f"malformed files still named packages: {guess.pins}")
