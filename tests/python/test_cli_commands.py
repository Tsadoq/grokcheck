"""The CLI's one-shot commands, run as real subprocesses in throwaway projects."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from test_cli import _FINAL_ANSWERS, FIXTURES, _grokcheck, _serve_and_submit

_LESSON_WRITTEN = datetime(2021, 1, 1, tzinfo=UTC).timestamp()


def _git(root: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ}
    if date is not None:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    completed = subprocess.run(  # noqa: S603
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
        text=True,
        timeout=10,
        env=env,
    )
    return completed.stdout.strip()


def _two_commit_repo(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "start")
    (root / "src" / "mod.py").write_text("a = 1\nb = 20\n", encoding="utf-8")
    (root / "notes.txt").write_text("unrelated\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "edit b")


def test_scope_reads_a_revision_as_that_commits_change(tmp_path: Path) -> None:
    """A short hash names a change covering exactly the files that commit touched."""
    _two_commit_repo(tmp_path)
    (tmp_path / "src" / "mod.py").write_text("a = 10\nb = 20\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "edit a")
    middle = _git(tmp_path, "rev-parse", "--short", "HEAD~1")

    guess = _grokcheck(tmp_path, "scope", middle)

    if (guess["subject"], guess["files"]) != ("change", ["notes.txt", "src/mod.py"]):
        pytest.fail(f"scope {middle} should be that commit's change, got {guess}")


def test_scope_reads_the_root_commit_as_the_files_it_added(tmp_path: Path) -> None:
    """The repository's first commit covers only the files it added."""
    _two_commit_repo(tmp_path)
    root = _git(tmp_path, "rev-parse", "--short", "HEAD~1")

    guess = _grokcheck(tmp_path, "scope", root)

    if (guess["subject"], guess["files"]) != ("change", ["src/mod.py"]):
        pytest.fail(f"scope {root} should be the root commit's change, got {guess}")


def test_scope_reads_an_existing_directory_as_an_area(tmp_path: Path) -> None:
    """A directory in the project becomes an area lesson over its tracked files."""
    _two_commit_repo(tmp_path)

    guess = _grokcheck(tmp_path, "scope", "src")

    if (guess["subject"], guess["files"]) != ("area", ["src/mod.py"]):
        pytest.fail(f"scope src should be an area over src/mod.py, got {guess}")


def test_scope_last_change_falls_back_to_the_last_commit(tmp_path: Path) -> None:
    """With a clean tree, `last change` covers the files of HEAD."""
    _two_commit_repo(tmp_path)

    guess = _grokcheck(tmp_path, "scope", "last", "change")

    if (guess["subject"], sorted(cast("list[str]", guess["files"]))) != (
        "change",
        ["notes.txt", "src/mod.py"],
    ):
        pytest.fail(f"scope last change should cover HEAD's files, got {guess}")


def test_scope_reads_a_pinned_dependency_as_a_library(tmp_path: Path) -> None:
    """A name the pyproject pins is a library lesson that carries the pinned version."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests==2.31.0"]\n',
        encoding="utf-8",
    )

    guess = _grokcheck(tmp_path, "scope", "requests")

    if (guess["subject"], guess["pins"]) != ("library", {"requests": "2.31.0"}):
        pytest.fail(f"scope requests should be a pinned library, got {guess}")


def test_diff_prints_one_element_per_hunk_between_two_commits(tmp_path: Path) -> None:
    """The edited line comes back as a diff element with its removed and added text."""
    _two_commit_repo(tmp_path)

    printed = _grokcheck(
        tmp_path, "diff", "src/mod.py", "--old", "HEAD~1", "--new", "HEAD"
    )

    elements = cast("list[dict[str, Any]]", printed["elements"])
    changed = [
        (line["op"], line["text"])
        for element in elements
        for line in element["lines"]
        if line["op"] != " "
    ]
    if [e["type"] for e in elements] != ["diff"] or changed != [
        ("-", "b = 2"),
        ("+", "b = 20"),
    ]:
        pytest.fail(f"diff should print one hunk replacing b, got {printed}")


def test_trace_record_writes_the_trace_under_grokcheck_traces(tmp_path: Path) -> None:
    """Without `--out` the trace lands in `.grokcheck/traces/<trace_id>.json`."""
    shutil.copy(FIXTURES / "trace" / "sync_cache.py", tmp_path / "sync_cache.py")

    recorded = _grokcheck(
        tmp_path, "trace", "record", "sync_cache.py", "--cite", "sync_cache.py"
    )

    trace_file = tmp_path / ".grokcheck" / "traces" / f"{recorded['trace_id']}.json"
    if not trace_file.is_file():
        pytest.fail(f"trace record printed {recorded} but wrote no {trace_file}")
    events = json.loads(trace_file.read_text(encoding="utf-8"))["events"]
    if len(events) != recorded["steps"] or not all(
        event["file"] == "sync_cache.py" for event in events
    ):
        pytest.fail(f"the trace holds {events}, expected {recorded['steps']} steps")


def test_diagram_check_passes_a_lesson_whose_diagram_renders(tmp_path: Path) -> None:
    """With `mmdc` installed, the fixture's flowchart renders and nothing is skipped."""
    if shutil.which("mmdc") is None:
        pytest.skip("mmdc is not on PATH, so diagrams cannot be rendered")
    project = FIXTURES / "project"
    lesson = FIXTURES / "lessons" / "valid_full.json"

    checked = _grokcheck(
        tmp_path, "diagram", "check", str(lesson), "--project", str(project)
    )

    if checked != {"ok": True, "warnings": []}:
        pytest.fail(f"diagram check should pass without skipping, got {checked}")


def _submitted_project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson_file = tmp_path / "lesson.json"
    shutil.copy(FIXTURES / "lessons" / "valid_full.json", lesson_file)
    served = _grokcheck(project, "serve", str(lesson_file), "--no-open")
    _serve_and_submit(project, served, {**_FINAL_ANSWERS, "final-blank": "wrong"})
    return project, str(served["lesson_id"])


def test_export_anki_writes_a_card_for_the_missed_question(tmp_path: Path) -> None:
    """One wrong final answer becomes one card in `anki.txt` beside the results."""
    project, lesson_id = _submitted_project(tmp_path)

    exported = _grokcheck(project, "export", lesson_id, "--format", "anki")

    anki = project / ".grokcheck" / "lessons" / lesson_id / "anki.txt"
    if (exported["path"], exported["cards"]) != (str(anki), 1) or not anki.is_file():
        pytest.fail(f"export anki should write one card to {anki}, got {exported}")


def test_export_obsidian_writes_a_note_into_the_vault(tmp_path: Path) -> None:
    """The note lands in `--vault` and names the lesson's title."""
    project, lesson_id = _submitted_project(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()

    exported = _grokcheck(
        project, "export", lesson_id, "--format", "obsidian", "--vault", str(vault)
    )

    note = Path(str(exported["path"]))
    title = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text())["title"]
    if note.parent != vault or not note.is_file() or title not in note.name:
        pytest.fail(f"export obsidian should write a note in {vault}, got {exported}")


def test_refresh_names_the_claim_whose_cited_line_changed(tmp_path: Path) -> None:
    """Editing a cited line after the lesson was written makes its claim stale."""
    (tmp_path / "mod.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "mod.py")
    _git(tmp_path, "commit", "-q", "-m", "start", date="2020-01-01T00:00:00+00:00")
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text())
    raw["scope"]["files"] = ["mod.py"]
    raw["sections"] = [
        {
            **raw["sections"][0],
            "elements": [
                {
                    "type": "prose",
                    "markdown": "Claims.",
                    "claims": [
                        {
                            "text": "a is one.",
                            "backing": {"file": "mod.py", "lines": [1, 1]},
                        },
                        {
                            "text": "b is two.",
                            "backing": {"file": "mod.py", "lines": [2, 2]},
                        },
                    ],
                }
            ],
        }
    ]
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(lesson_file, (_LESSON_WRITTEN, _LESSON_WRITTEN))
    (tmp_path / "mod.py").write_text("a = 1\nb = 20\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "edit b")

    refreshed = _grokcheck(tmp_path, "refresh", str(lesson_file))

    if (refreshed["stale_claims"], refreshed["moved_files"]) != (
        ["sections[0].elements[0].claims[1]"],
        ["mod.py"],
    ):
        pytest.fail(f"refresh should name only the claim on line 2, got {refreshed}")


def test_ingest_copies_a_markdown_file_into_sources(tmp_path: Path) -> None:
    """The copy under `.grokcheck/sources/` holds the original text."""
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n\nCaches evict the oldest key.\n", encoding="utf-8")

    ingested = _grokcheck(tmp_path, "ingest", str(notes))

    copy = tmp_path / str(ingested["path"])
    if ingested["kind"] != "markdown" or copy.read_text(encoding="utf-8") != (
        notes.read_text(encoding="utf-8")
    ):
        pytest.fail(f"ingest should copy notes.md verbatim, got {ingested}")


def test_sources_lists_an_ingested_file(tmp_path: Path) -> None:
    """After one ingest, `sources` lists exactly that file's record."""
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n", encoding="utf-8")
    ingested = _grokcheck(tmp_path, "ingest", str(notes))

    listed = _grokcheck(tmp_path, "sources")

    if listed != {"sources": [ingested]}:
        pytest.fail(f"sources should list only {ingested}, got {listed}")
