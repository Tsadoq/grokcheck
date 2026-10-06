"""The grounding pass: show each claim with its evidence, then record the verdicts.

A fresh-context subagent reads the manifest, judges every claim against its
evidence alone, and its verdicts are written back into the lesson's `verified`
fields. A claim is addressed by its authored JSON path, `Lesson.claims()`.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

from grokcheck.lesson import (
    LessonError,
    LinesBacking,
    Problem,
    SpikeBacking,
    parse_claim_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grokcheck.lesson import Claim, Lesson

VERDICTS = {
    "supported": "supported",
    "contradicted": "contradicted",
    "not_shown": "unchecked",
}
"""Each verdict the subagent may give, mapped to the claim's `verified` value."""


def manifest(lesson: Lesson, project_root: Path) -> list[dict[str, Any]]:
    """Return `{claim_id, text, backing, evidence}` for every claim in `lesson`.

    `evidence` is the cited lines for a lines backing, the recorded stdout for a
    spike, and `None` otherwise; a spike with no recorded result adds a `note`.
    """
    return [
        manifest_entry(claim_id, claim, project_root)
        for claim_id, claim in lesson.claims()
    ]


def manifest_entry(claim_id: str, claim: Claim, project_root: Path) -> dict[str, Any]:
    """Return the `manifest` entry of one `claim`, addressed as `claim_id`."""
    entry: dict[str, Any] = {
        "claim_id": claim_id,
        "text": claim.text,
        "backing": dataclasses.asdict(claim.backing),
        "evidence": None,
    }
    backing = claim.backing
    if isinstance(backing, LinesBacking):
        start, end = backing.lines
        cited = project_root / backing.file
        lines = cited.read_text(encoding="utf-8").splitlines()
        entry["evidence"] = "\n".join(lines[start - 1 : end])
    elif isinstance(backing, SpikeBacking):
        spike_dir = project_root / ".grokcheck" / "spikes" / backing.spike_id
        result = spike_dir / "result.json"
        if result.is_file():
            entry["evidence"] = json.loads(result.read_text("utf-8")).get("stdout")
        else:
            entry["note"] = f"no result.json for spike '{backing.spike_id}'"
    return entry


def apply_verdicts(lesson_path: Path, verdicts: Sequence[dict[str, Any]]) -> Path:
    """Write each verdict into its claim's `verified` field and return `lesson_path`.

    Raises `LessonError` and leaves the file untouched if any verdict names an
    unknown claim or verdict.
    """
    raw = json.loads(lesson_path.read_text(encoding="utf-8"))
    problems: list[Problem] = []
    updates: list[tuple[dict[str, Any], str]] = []
    for index, verdict in enumerate(verdicts):
        where = f"verdicts[{index}]"
        claim_id = verdict.get("claim_id")
        claim = _claim_at(raw, claim_id) if isinstance(claim_id, str) else None
        if claim is None:
            problems.append(Problem(where, f"no claim at {claim_id!r}"))
        elif verdict.get("verdict") not in VERDICTS:
            problems.append(
                Problem(where, f"verdict must be one of {', '.join(VERDICTS)}")
            )
        else:
            updates.append((claim, VERDICTS[verdict["verdict"]]))
    if problems:
        raise LessonError(problems)
    for claim, verified in updates:
        claim["verified"] = verified
    lesson_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return lesson_path


def _claim_at(raw: dict[str, Any], claim_id: str) -> dict[str, Any] | None:
    parsed = parse_claim_id(claim_id)
    if parsed is None:
        return None
    i, j, kind, k = parsed
    try:
        claim = raw["sections"][i]["elements"][j][kind][k]
    except (KeyError, IndexError, TypeError):
        return None
    return claim if isinstance(claim, dict) else None
