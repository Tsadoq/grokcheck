"""The media venv: its state on disk and the steps `setup` plans."""

from pathlib import Path
from typing import Any

import pytest
from grokcheck_media import env

EXPECTED: dict[str, Any] = {"version": "0.2.0", "python": "3.12", "requirements": []}
OLDER: dict[str, Any] = {**EXPECTED, "version": "0.1.0"}


@pytest.mark.parametrize(
    ("has_venv", "recorded", "models", "answer", "status"),
    [
        (True, EXPECTED, True, "yes", "ready"),
        (True, OLDER, True, "yes", "out_of_date"),
        (True, EXPECTED, False, "yes", "out_of_date"),
        (False, None, False, None, "missing"),
        (False, None, False, "no", "declined"),
        (True, None, True, "yes", "missing"),
    ],
)
def test_classify_tells_states_apart(
    *,
    has_venv: bool,
    recorded: dict[str, Any] | None,
    models: bool,
    answer: str | None,
    status: str,
) -> None:
    """Only a matching stamp with the voice files present is ready."""
    got = env.classify(
        has_venv=has_venv,
        recorded=recorded,
        expected=EXPECTED,
        models=models,
        answer=answer,
    )
    if got != status:
        pytest.fail(f"expected {status}, got {got}")


def test_plan_on_a_fresh_machine_does_everything() -> None:
    """No venv means create, install, fetch every model, then finish."""
    steps = env.plan(has_venv=False, current=False, missing_models=env.KOKORO_FILES)

    expected = [
        "create venv",
        "install packages",
        "install chromium",
        "fetch whisper model",
        "download kokoro-v1.0.onnx",
        "download voices-v1.0.bin",
        "link ffmpeg",
        "check espeak path",
        "write stamp",
    ]
    if steps != expected:
        pytest.fail(f"got {steps}")


def test_plan_on_a_current_venv_only_fills_gaps() -> None:
    """A current venv skips installing; an outdated one installs again."""
    current = env.plan(has_venv=True, current=True, missing_models=[])
    outdated = env.plan(has_venv=True, current=False, missing_models=[])

    if current != ["link ffmpeg", "check espeak path", "write stamp"]:
        pytest.fail(f"current venv planned {current}")
    if "create venv" in outdated or "install packages" not in outdated:
        pytest.fail(f"outdated venv planned {outdated}")


def test_install_command_pins_pyav_for_manim_and_faster_whisper() -> None:
    """The install keeps PyAV in the range both libraries accept."""
    command = env.install_command(Path("/v/bin/python"))

    if command[:5] != ["uv", "pip", "install", "--python", "/v/bin/python"]:
        pytest.fail(f"got {command}")
    if "av>=15,<17" not in command:
        pytest.fail(f"PyAV is not pinned in {command}")


def test_home_follows_the_plugin_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inside a plugin the venv lives in its data dir, else in the user cache."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/data/grokcheck")
    inside = env.venv()
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")

    if inside != Path("/data/grokcheck/media/media-venv"):
        pytest.fail(f"got {inside}")
    if env.home() != Path.home() / ".cache" / "grokcheck":
        pytest.fail(f"got {env.home()}")
