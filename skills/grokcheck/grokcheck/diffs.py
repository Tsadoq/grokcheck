"""Diff elements cut from `git diff`, so hunk line numbers are never typed by hand."""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING, cast, get_args

from grokcheck import git
from grokcheck.lesson import DiffElement, DiffLine, DiffOp

if TYPE_CHECKING:
    from pathlib import Path

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def hunks(
    project_root: Path, path: str, old_rev: str, new_rev: str
) -> list[DiffElement]:
    """Return one diff element per hunk of `path` between two revisions.

    The elements carry no notes or asks yet. Raises
    `subprocess.CalledProcessError` when git rejects a revision or the path.
    """
    output = git.run(
        project_root,
        "diff",
        "-U3",
        "--no-color",
        "--no-ext-diff",
        old_rev,
        new_rev,
        "--",
        path,
    )
    elements: list[DiffElement] = []
    starts: tuple[int, int] | None = None
    lines: list[DiffLine] = []
    for raw in output.splitlines():
        header = _HUNK_HEADER.match(raw)
        if header:
            if starts:
                elements.append(_element(path, starts, lines))
            starts, lines = (int(header[1]), int(header[2])), []
        elif starts and raw[:1] in get_args(DiffOp):
            lines.append(DiffLine(cast("DiffOp", raw[0]), raw[1:]))
    if starts:
        elements.append(_element(path, starts, lines))
    return elements


def as_json(element: DiffElement) -> dict[str, object]:
    """Return `element` as it is written in a lesson's `elements` array."""
    return {"type": element.type_name, **dataclasses.asdict(element)}


def _element(path: str, starts: tuple[int, int], lines: list[DiffLine]) -> DiffElement:
    old_start, new_start = starts
    return DiffElement(
        file=path, old_start=old_start, new_start=new_start, lines=tuple(lines)
    )
