"""Mutations: one line of the project changed in a scratch worktree, caught by a test.

`plant` records the run that a `mutation_quiz` or `fix_the_bug` question uses
as its answer key. The project's own tree is never edited.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from grokcheck import git

TEST_TIMEOUT_SECONDS = 600
LOG_TAIL_CHARS = 4096

_NO_STALE_BYTECODE = {"PYTHONDONTWRITEBYTECODE": "1"}
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_FAILED = re.compile(r"^FAILED (\S+::\S+)", re.MULTILINE)


class MutationError(Exception):
    """A mutation could not be planted, or no test told it from the original."""


@dataclass(frozen=True)
class MutationResult:
    """A planted mutant: the worktree holding it and the run that caught it.

    `failing_tests` are pytest node ids of the mutant run; `log` is that run's
    output tail.
    """

    worktree: Path
    passed_original: bool
    failed_mutant: bool
    failing_tests: list[str]
    log: str


def plant(
    project_root: Path,
    file: str,
    line: int,
    replacement: str | None,
    test_command: list[str],
) -> MutationResult:
    """Check out HEAD in a scratch worktree and replace 1-based `line` of `file`.

    `replacement` None deletes the line. Raises `MutationError`, removing the
    worktree, unless `test_command` passes on the original and fails on the mutant.
    """
    worktree = Path(tempfile.mkdtemp(prefix="grokcheck-mutant-"))
    try:
        _git(project_root, "worktree", "add", "--detach", str(worktree), "HEAD")
    except MutationError:
        worktree.rmdir()
        raise
    try:
        output = _caught_mutant(worktree, file, line, replacement, test_command)
    except BaseException:
        remove(worktree)
        raise
    return MutationResult(
        worktree=worktree,
        passed_original=True,
        failed_mutant=True,
        failing_tests=_FAILED.findall(output),
        log=output[-LOG_TAIL_CHARS:],
    )


def _caught_mutant(
    worktree: Path,
    file: str,
    line: int,
    replacement: str | None,
    test_command: list[str],
) -> str:
    """Apply the mutation in `worktree` and return the output of the failing run."""
    target = (worktree / file).resolve()
    if not target.is_relative_to(worktree.resolve()):
        msg = f"'{file}' resolves outside the project"
        raise MutationError(msg)
    if run_tests(test_command, worktree).returncode != 0:
        msg = "the tests fail on the original code, so they cannot judge a mutant"
        raise MutationError(msg)
    _edit_line(target, line, replacement)
    mutant = run_tests(test_command, worktree)
    if mutant.returncode == 0:
        msg = f"the mutant passed: no test catches a change to {file}:{line}"
        raise MutationError(msg)
    return ANSI_ESCAPE.sub("", mutant.stdout + mutant.stderr)


def remove(worktree: Path) -> None:
    """Delete a worktree made by `plant`, discarding any edits in it."""
    _git(worktree, "worktree", "remove", "--force", str(worktree))


def _edit_line(path: Path, line: int, replacement: str | None) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as error:
        msg = f"cannot read '{path.name}' as UTF-8 text: {error}"
        raise MutationError(msg) from error
    if not 1 <= line <= len(lines):
        msg = f"line {line} is outside '{path.name}' (1 to {len(lines)})"
        raise MutationError(msg)
    old = lines[line - 1]
    ending = old[len(old.rstrip("\r\n")) :]
    lines[line - 1] = "" if replacement is None else replacement + ending
    path.write_text("".join(lines), encoding="utf-8")


def run_tests(
    test_command: list[str], worktree: Path
) -> subprocess.CompletedProcess[str]:
    """Run `test_command` in `worktree`; raise `MutationError` if it cannot finish."""
    try:
        return subprocess.run(  # noqa: S603
            test_command,
            cwd=worktree,
            env={**os.environ, **_NO_STALE_BYTECODE},
            timeout=TEST_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"the tests ran longer than {TEST_TIMEOUT_SECONDS} seconds"
        raise MutationError(msg) from error
    except OSError as error:
        msg = f"cannot run the test command: {error}"
        raise MutationError(msg) from error


def _git(cwd: Path, *args: str) -> None:
    try:
        git.run(cwd, *args, timeout=60)
    except (subprocess.CalledProcessError, OSError) as error:
        detail = getattr(error, "stderr", "") or error
        msg = f"git {args[0]} {args[1]} failed: {str(detail).strip()}"
        raise MutationError(msg) from error
