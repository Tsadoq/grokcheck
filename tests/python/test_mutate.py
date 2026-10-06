"""Planting a mutation in a scratch worktree, against a real git repo and pytest."""

import subprocess
import sys
from pathlib import Path

import pytest
from grokcheck.mutate import MutationError, plant, remove

SOURCE = "def add(a, b):\n    return a + b\n\n\ndef unused():\n    return 1\n"
TEST = "from a import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
ADD_BODY_LINE = 2
UNCOVERED_LINE = 6


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_plant_proves_mutant_fails_and_original_passes_in_scratch_worktree(
    tmp_path: Path,
) -> None:
    """A caught mutation names its failing test; an uncaught one is refused.

    The mutant lives in a scratch worktree, so the reader's project is never
    edited.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "a.py").write_text(SOURCE, encoding="utf-8")
    (repo / "tests" / "test_a.py").write_text(TEST, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")

    result = plant(repo, "a.py", ADD_BODY_LINE, "    return a - b", PYTEST)
    try:
        observed = (result.failed_mutant, result.failing_tests)
    finally:
        remove(result.worktree)

    if observed != (True, ["tests/test_a.py::test_add"]):
        pytest.fail(f"expected the mutant to fail test_add, got {observed}")
    if (repo / "a.py").read_text(encoding="utf-8") != SOURCE:
        pytest.fail("planting edited the project tree instead of the scratch worktree")
    with pytest.raises(MutationError, match="mutant passed"):
        plant(repo, "a.py", UNCOVERED_LINE, "    return 2", PYTEST)
