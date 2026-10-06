"""Run git in a project and read what it prints."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

TIMEOUT_SECONDS = 10


def run(cwd: Path, *args: str, timeout: float = TIMEOUT_SECONDS) -> str:
    """Return git's stdout; raise `CalledProcessError`, `OSError` or a timeout."""
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout


def output(cwd: Path, *args: str) -> str | None:
    """Return git's stdout, or `None` when git fails, is missing or times out."""
    try:
        return run(cwd, *args)
    except (OSError, subprocess.SubprocessError):
        return None


def lines(stdout: str | None) -> list[str]:
    """Split git's stdout into lines; `None` gives none."""
    return stdout.splitlines() if stdout else []
