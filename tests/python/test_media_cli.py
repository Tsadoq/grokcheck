"""The media CLI's argument handling for `video frames` and `video chapter --avoid`."""

import json
from pathlib import Path
from typing import Any

import pytest
from grokcheck_media import checks, cli, concept_video, doctor


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record what `frames` and `build_chapter` were called with, rendering nothing."""
    seen: list[dict[str, Any]] = []

    def frames(video: Path, out_dir: Path, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        seen.append({"video": video, "out_dir": out_dir, **kwargs})
        return []

    def build(script: dict[str, Any], *_: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        seen.append({"script": script, **kwargs})
        return {}

    monkeypatch.setattr(doctor, "require", lambda _tools: None)
    monkeypatch.setattr(checks, "frames", frames)
    monkeypatch.setattr(concept_video, "build_chapter", build)
    return seen


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    cli.main(argv)
    printed: dict[str, Any] = json.loads(capsys.readouterr().out)
    return printed


@pytest.fixture
def video(tmp_path: Path) -> Path:
    """Write an empty file standing in for a rendered chapter."""
    path = tmp_path / "video.mp4"
    path.write_bytes(b"")
    return path


def test_frames_takes_times_or_an_interval(
    calls: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    video: Path,
    tmp_path: Path,
) -> None:
    """`--at` takes several seconds, `--every` one interval."""
    frames = ["video", "frames", str(video), "--out-dir", str(tmp_path / "frames")]
    at = _run(capsys, *frames, "--at", "1", "2.5")
    every = _run(capsys, *frames, "--every", "3")
    if not (at["ok"] and every["ok"]):
        pytest.fail(f"{at} {every}")
    if [(c["at"], c["every"]) for c in calls] != [([1.0, 2.5], None), ((), 3.0)]:
        pytest.fail(str(calls))


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], "one of the arguments --at --every is required"),
        (["--at", "1", "--every", "2"], "not allowed with argument"),
        (["--at", "-1"], "--at needs seconds of 0 or more"),
        (["--every", "0"], "--every a positive number"),
    ],
)
def test_frames_refuses_bad_arguments(
    calls: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    video: Path,
    args: list[str],
    expected: str,
) -> None:
    """Exactly one of `--at` and `--every`, with sensible seconds."""
    printed = _run(
        capsys, "video", "frames", str(video), *args, "--out-dir", str(video.parent)
    )
    if printed["ok"] or expected not in printed["error"] or calls:
        pytest.fail(f"{printed} {calls}")


def test_frames_refuses_a_missing_video(
    calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A path that is not a file is named in the error."""
    missing = tmp_path / "nope.mp4"
    printed = _run(
        capsys, "video", "frames", str(missing), "--at", "1", "--out-dir", str(tmp_path)
    )
    if printed["ok"] or "is not a file" not in printed["error"] or calls:
        pytest.fail(f"{printed} {calls}")


def test_chapter_passes_every_avoid_text(
    calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`--avoid` repeats and reaches `build_chapter` in order."""
    script = tmp_path / "s1.json"
    script.write_text("{}", encoding="utf-8")
    argv = ["video", "chapter", str(script), "--renderer", "manim"]
    argv += ["--out-dir", str(tmp_path), "--avoid", "204", "--avoid", "replay"]
    printed = _run(capsys, *argv)
    if not printed["ok"] or calls[0]["avoid"] != ["204", "replay"]:
        pytest.fail(f"{printed} {calls}")


def test_build_chapter_refuses_a_leak_before_rendering(tmp_path: Path) -> None:
    """A script that says an avoided answer fails lint, so nothing renders."""
    script = {
        "concept_id": "c",
        "chapter_id": "ch01",
        "title": "T",
        "word_budget": 150,
        "beats": [{"say": "It answers with a 204, no content.", "show": "a"}],
    }
    with pytest.raises(ValueError, match="gives away '204'"):
        concept_video.build_chapter(
            script, "manim", tmp_path, project_root=tmp_path, avoid=["204"]
        )
