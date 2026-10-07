"""Refresh: which claims and spikes of a lesson no longer hold."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from grokcheck.refresh import report
from grokcheck.spikes import write

_LESSON_WRITTEN = datetime(2021, 1, 1, tzinfo=UTC).timestamp()


def _git(root: Path, *args: str, date: str | None = None) -> None:
    env = {**os.environ}
    if date is not None:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
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
        env=env,
    )


def _choice(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "type": "single_choice",
        "prompt": "Which?",
        "options": [{"text": "a", "why": "first"}, {"text": "b", "why": "second"}],
        "correct": 0,
    }


def test_refresh_names_claims_whose_lines_or_spike_output_changed(
    tmp_path: Path,
) -> None:
    """Only the edited cited line and the spike whose output moved are reported."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH, so the spike cannot be rerun")
    module = tmp_path / "mod.py"
    module.write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "mod.py")
    _git(tmp_path, "commit", "-q", "-m", "start", date="2020-01-01T00:00:00+00:00")
    spike_dir = tmp_path / ".grokcheck" / "spikes" / "sum"
    write(spike_dir, "one plus two is four", "print(1 + 2)", {}, "2026-01-01T00:00:00Z")
    recorded = {"stdout": "4\n", "stderr": "", "returncode": 0, "where": "host"}
    (spike_dir / "result.json").write_text(json.dumps(recorded), encoding="utf-8")
    claims = [
        {"text": "b is two.", "backing": {"file": "mod.py", "lines": [1, 2]}},
        {"text": "d is four.", "backing": {"file": "mod.py", "lines": [4, 4]}},
        {"text": "The sum is four.", "backing": {"spike_id": "sum"}},
    ]
    raw = {
        "schema_version": 2,
        "title": "Moving",
        "scope": {"summary": "One module.", "files": ["mod.py"]},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "elements": [
                    {"type": "prose", "markdown": "Claims.", "claims": claims}
                ],
                "checkpoints": [_choice("cp-1")],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(lesson_file, (_LESSON_WRITTEN, _LESSON_WRITTEN))
    module.write_text("a = 1\nb = 20\nc = 3\nd = 4\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "edit line 2")

    result = report(lesson_file, tmp_path)

    if result.stale_claims != ["sections[0].elements[0].claims[0]"]:
        pytest.fail(f"stale claims are {result.stale_claims}")
    if [c.spike_id for c in result.changed_spikes] != ["sum"]:
        pytest.fail(f"changed spikes are {result.changed_spikes}")
    if result.moved_files != ["mod.py"]:
        pytest.fail(f"moved files are {result.moved_files}")


def test_refresh_names_datasets_to_record_again(tmp_path: Path) -> None:
    """A dataset whose cited file changed is listed with that file."""
    fixtures = Path(__file__).parents[1] / "fixtures"
    project = tmp_path / "project"
    shutil.copytree(fixtures / "project", project)
    lesson_file = project / "lesson.json"
    shutil.copy(fixtures / "lessons" / "valid_views.json", lesson_file)
    stream = project / "src" / "stream.py"
    stream.write_text(stream.read_text("utf-8") + "\n", encoding="utf-8")

    stale = report(lesson_file, project).stale_data

    if stale != {"reconnect": ["src/stream.py"], "terms": ["src/stream.py"]}:
        pytest.fail(f"stale datasets are {stale}")
