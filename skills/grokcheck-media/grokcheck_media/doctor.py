"""Find the optional external tools the media commands need."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Iterable

_EXECUTABLES = {
    "ffmpeg": ("ffmpeg",),
    "mmdc": ("mmdc",),
    "manim": ("manim",),
    "hyperframes": ("hyperframes",),
    "node": ("node",),
    "chrome": ("google-chrome", "chromium"),
}
_MODULES = ("playwright", "kokoro_onnx", "faster_whisper")
_INSTALL_HINTS = {
    "ffmpeg": "install ffmpeg with the system package manager",
    "mmdc": "npm install -g @mermaid-js/mermaid-cli",
    "manim": "pip install manim 'av>=15,<17'",
    "hyperframes": "npm install -g hyperframes",
    "node": "install Node.js 20 or later",
    "chrome": "install Google Chrome or Chromium",
    "playwright": "pip install playwright && playwright install chromium",
    "kokoro_onnx": "pip install kokoro-onnx",
    "faster_whisper": "pip install faster-whisper 'av>=15,<17'",
}


def probe() -> dict[str, str | None]:
    """Map each known tool to where it was found, or None when it is missing."""
    found: dict[str, str | None] = {
        tool: next(filter(None, map(shutil.which, names)), None)
        for tool, names in _EXECUTABLES.items()
    }
    for module in _MODULES:
        spec = importlib.util.find_spec(module)
        found[module] = spec.origin if spec else None
    return found


def require(tools: Iterable[str]) -> None:
    """Exit with a JSON skip message naming the first of `tools` not installed."""
    found = probe()
    for tool in tools:
        if tool not in found:
            msg = f"unknown tool {tool!r}; known tools are {sorted(found)}"
            raise ValueError(msg)
        if found[tool] is None:
            _skip(f"{tool} not installed; {_INSTALL_HINTS[tool]}")


def _skip(reason: str) -> NoReturn:
    print(json.dumps({"ok": False, "skipped": reason}))  # noqa: T201
    sys.exit(1)
