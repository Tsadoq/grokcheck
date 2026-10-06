"""The media venv: where it lives, whether it is ready, and how `setup` builds it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

Status = Literal["ready", "missing", "out_of_date", "declined"]

PYTHON = "3.12"
PINS = (
    "manim==0.21.0",
    "kokoro-onnx==0.6.1",
    "faster-whisper==1.2.1",
    "playwright==1.63.0",
    "imageio-ffmpeg==0.6.0",
    "av>=15,<17",
)
KOKORO_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
)
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
WHISPER_MODEL = "small.en"
ESPEAK_MAX_PATH = 110
_PLUGIN_JSON = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
_DOWNLOAD_SECONDS = 600.0


def home() -> Path:
    """`$CLAUDE_PLUGIN_DATA/media`, or `~/.cache/grokcheck` outside a plugin."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(data) / "media" if data else Path.home() / ".cache" / "grokcheck"


def venv() -> Path:
    """Return the media venv directory."""
    return home() / "media-venv"


def kokoro_dir() -> Path:
    """Where the Kokoro model files live."""
    return home() / "kokoro"


def espeak_copy() -> Path:
    """Return where espeak-ng-data is copied when the venv path is too long."""
    return Path.home() / ".cache" / "grokcheck" / "espeak-ng-data"


def stamp() -> dict[str, Any]:
    """Return what a ready venv records: plugin version and pinned requirements."""
    version = "unknown"
    if _PLUGIN_JSON.is_file():
        version = json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    return {"version": version, "python": PYTHON, "requirements": list(PINS)}


def status() -> Status:
    """Read the media dir's state from disk."""
    return classify(
        has_venv=(venv() / "bin" / "python").is_file(),
        recorded=_read_json(home() / "stamp.json"),
        expected=stamp(),
        models=all((kokoro_dir() / name).is_file() for name in KOKORO_FILES),
        answer=(_read_json(home() / "answer.json") or {}).get("answer"),
    )


def classify(
    *,
    has_venv: bool,
    recorded: dict[str, Any] | None,
    expected: dict[str, Any],
    models: bool,
    answer: str | None,
) -> Status:
    """Ready, out of date after an update, declined by the user, or missing."""
    if has_venv and recorded == expected and models:
        return "ready"
    if has_venv and recorded is not None:
        return "out_of_date"
    return "declined" if answer == "no" else "missing"


def plan(*, has_venv: bool, current: bool, missing_models: Sequence[str]) -> list[str]:
    """Name the steps `setup` takes, in order; a current venv only fills gaps."""
    steps = [] if has_venv else ["create venv"]
    if not (has_venv and current):
        steps += ["install packages", "install chromium", "fetch whisper model"]
    steps += [f"download {name}" for name in missing_models]
    return [*steps, "link ffmpeg", "check espeak path", "write stamp"]


def install_command(python: Path) -> list[str]:
    """Return the `uv pip install` call that brings the venv to the pins."""
    return ["uv", "pip", "install", "--python", str(python), *PINS]


def record_answer(answer: Literal["yes", "no"]) -> None:
    """Remember whether the user wants the video tools, so they are asked once."""
    home().mkdir(parents=True, exist_ok=True)
    (home() / "answer.json").write_text(json.dumps({"answer": answer}), "utf-8")


def setup() -> list[str]:
    """Create or update the media venv and its model files; return the steps run."""
    if shutil.which("uv") is None:
        msg = "uv is not installed; see https://docs.astral.sh/uv/"
        raise ValueError(msg)
    record_answer("yes")
    python = venv() / "bin" / "python"
    steps = plan(
        has_venv=python.is_file(),
        current=_read_json(home() / "stamp.json") == stamp(),
        missing_models=[n for n in KOKORO_FILES if not (kokoro_dir() / n).is_file()],
    )
    return [step for step in steps if _STEPS[step.split()[0]](step, python)]


def reexec() -> None:
    """Run this process under the media venv's python, its bin first on PATH."""
    python = venv() / "bin" / "python"
    if not python.is_file():
        return
    bin_dir = str(python.parent)
    path = os.environ.get("PATH", "")
    if path.split(os.pathsep)[0] != bin_dir:
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}"
    if Path(sys.prefix).resolve() != venv().resolve():
        os.execv(python, [str(python), *sys.argv])  # noqa: S606


def _create(_: str, python: Path) -> bool:
    _run(["uv", "venv", "--clear", "--python", PYTHON, str(python.parent.parent)])
    return True


def _install(step: str, python: Path) -> bool:
    if step == "install packages":
        _run(install_command(python))
    else:
        _run([str(python), "-m", "playwright", "install", "chromium"])
    return True


def _fetch(_: str, python: Path) -> bool:
    _call(python, "faster_whisper", f"WhisperModel({WHISPER_MODEL!r}, device='cpu')")
    return True


def _download(step: str, _: Path) -> bool:
    name = step.split()[1]
    kokoro_dir().mkdir(parents=True, exist_ok=True)
    part = kokoro_dir() / f"{name}.part"
    url = KOKORO_URL + name
    with (
        urllib.request.urlopen(url, timeout=_DOWNLOAD_SECONDS) as response,  # noqa: S310
        part.open("wb") as out,
    ):
        shutil.copyfileobj(response, out)
    part.replace(kokoro_dir() / name)
    return True


def _link_ffmpeg(_: str, python: Path) -> bool:
    exe = _call(python, "imageio_ffmpeg", "get_ffmpeg_exe()")
    link = python.parent / "ffmpeg"
    if link.is_symlink() and str(link.readlink()) == exe:
        return False
    link.unlink(missing_ok=True)
    link.symlink_to(exe)
    return True


def _check_espeak(_: str, python: Path) -> bool:
    data = _call(python, "espeakng_loader", "get_data_path()")
    if len(data) <= ESPEAK_MAX_PATH or espeak_copy().is_dir():
        return False
    shutil.copytree(data, espeak_copy())
    return True


def _write_stamp(_: str, __: Path) -> bool:
    (home() / "stamp.json").write_text(json.dumps(stamp()), encoding="utf-8")
    return False


_STEPS = {
    "create": _create,
    "install": _install,
    "fetch": _fetch,
    "download": _download,
    "link": _link_ffmpeg,
    "check": _check_espeak,
    "write": _write_stamp,
}


def _call(python: Path, module: str, call: str) -> str:
    return _output([str(python), "-c", f"import {module}; print({module}.{call})"])


def _run(command: list[str]) -> None:
    _output(command)


def _output(command: list[str]) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603
    if result.returncode:
        msg = f"{' '.join(command[:4])} failed: {result.stderr.strip()[-2000:]}"
        raise ValueError(msg)
    return result.stdout.strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None
