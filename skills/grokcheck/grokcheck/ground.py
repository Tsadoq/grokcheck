"""The grounding pass: show each claim with its evidence, then record the verdicts.

A fresh-context subagent reads the manifest, judges every claim against its
evidence alone, and its verdicts are written back into the lesson's `verified`
fields. A claim is addressed by its authored JSON path, `Lesson.claims()`.
A row of an authored dataset is a claim `data:<id>.rows[<k>]`, and its verdict
goes into that data file's `source.verdicts`.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING, Any

from grokcheck import data
from grokcheck.lesson import (
    DataBacking,
    LessonError,
    LinesBacking,
    Problem,
    SpikeBacking,
    ViewElement,
    parse_claim_id,
)
from grokcheck.selector import matches

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

MAX_EVIDENCE_ROWS = 20
_ROW_CLAIM = re.compile(r"data:([A-Za-z0-9][A-Za-z0-9_-]*)\.rows\[(\d+)\]")


def manifest(lesson: Lesson, project_root: Path) -> list[dict[str, Any]]:
    """Return `{claim_id, text, backing, evidence}` for every claim in `lesson`.

    `evidence` is the cited lines for a lines backing, the recorded stdout for a
    spike, the matched rows for a data backing, and `None` otherwise; a spike
    with no recorded result adds a `note`. Every row of an authored dataset the
    lesson declares follows, backed by its `cite`.
    """
    entries = [
        manifest_entry(claim_id, claim, project_root, lesson)
        for claim_id, claim in lesson.claims()
    ]
    for use in lesson.datasets:
        if use.source == "authored":
            entries += _authored_entries(project_root, use.id)
    return entries


def manifest_entry(
    claim_id: str, claim: Claim, project_root: Path, lesson: Lesson | None = None
) -> dict[str, Any]:
    """Return the `manifest` entry of one `claim`, addressed as `claim_id`.

    `lesson` lets a view note list the view's matched items, reserved fields
    included, instead of the dataset rows behind them.
    """
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
    elif isinstance(backing, DataBacking):
        try:
            dataset = data.load(project_root, backing.data)
        except data.DataError as error:
            entry["note"] = str(error)
            return entry
        rows = _note_items(lesson, claim_id) if lesson else None
        if rows is None:
            rows = [row for row in dataset.rows if matches(backing.rows, row)]
        provenance = data.provenance(dataset)
        entry["evidence"] = {
            "source": {
                key: provenance[key]
                for key in (
                    "kind",
                    "script",
                    "trace",
                    "head",
                    "cited",
                    "cited_lines_run",
                )
                if provenance[key] is not None
            },
            "matched": len(rows),
            "rows": [
                {k: v for k, v in row.items() if k != "_edited"}
                for row in rows[:MAX_EVIDENCE_ROWS]
            ],
        }
    return entry


def _note_items(lesson: Lesson, claim_id: str) -> list[dict[str, object]] | None:
    parsed = parse_claim_id(claim_id)
    if parsed is None or parsed[2] != "notes":
        return None
    i, j, _, k = parsed
    section = lesson.sections[i]
    view = section.elements[j + section.code_sugar]
    if not isinstance(view, ViewElement):
        return None
    where = view.notes[k].where
    return [item for item in view.items if matches(where, item)]


def _authored_entries(project_root: Path, dataset_id: str) -> list[dict[str, Any]]:
    raw = json.loads(data.data_path(project_root, dataset_id).read_text("utf-8"))
    entries: list[dict[str, Any]] = []
    for k, row in enumerate(raw["rows"]):
        cite = row["cite"]
        start, end = cite["lines"]
        lines = (project_root / cite["file"]).read_text(encoding="utf-8").splitlines()
        text = ", ".join(
            f"{key}={value}" for key, value in row.items() if key != "cite"
        )
        entries.append(
            {
                "claim_id": f"data:{dataset_id}.rows[{k}]",
                "text": text,
                "backing": cite,
                "evidence": "\n".join(lines[start - 1 : end]),
            }
        )
    return entries


def apply_verdicts(
    lesson_path: Path,
    verdicts: Sequence[dict[str, Any]],
    project_root: Path | None = None,
) -> Path:
    """Write each verdict into its claim's `verified` field and return `lesson_path`.

    A `data:<id>.rows[<k>]` verdict goes into that dataset's `source.verdicts`
    under `project_root`. Raises `LessonError` and leaves every file untouched
    if any verdict names an unknown claim or verdict.
    """
    raw = json.loads(lesson_path.read_text(encoding="utf-8"))
    files: dict[Path, dict[str, Any]] = {lesson_path: raw}
    problems: list[Problem] = []
    updates: list[tuple[dict[str, Any], str, str]] = []
    for index, verdict in enumerate(verdicts):
        where = f"verdicts[{index}]"
        claim_id = verdict.get("claim_id")
        target = (
            _target(raw, claim_id, project_root, files)
            if isinstance(claim_id, str)
            else None
        )
        if target is None:
            problems.append(Problem(where, f"no claim at {claim_id!r}"))
        elif verdict.get("verdict") not in VERDICTS:
            problems.append(
                Problem(where, f"verdict must be one of {', '.join(VERDICTS)}")
            )
        else:
            updates.append((*target, VERDICTS[verdict["verdict"]]))
    if problems:
        raise LessonError(problems)
    for holder, key, verified in updates:
        holder[key] = verified
    lesson_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for path, content in files.items():
        if path != lesson_path:
            path.write_text(
                json.dumps(content, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    return lesson_path


def _target(
    raw: dict[str, Any],
    claim_id: str,
    project_root: Path | None,
    files: dict[Path, dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """Return the object and key that hold the claim's verdict."""
    match = _ROW_CLAIM.fullmatch(claim_id)
    if match is None:
        claim = _claim_at(raw, claim_id)
        return None if claim is None else (claim, "verified")
    if project_root is None:
        return None
    path = data.data_path(project_root, match.group(1))
    if path not in files:
        if not path.is_file():
            return None
        files[path] = json.loads(path.read_text(encoding="utf-8"))
    content = files[path]
    row = int(match.group(2))
    if content["source"].get("kind") != "authored" or row >= len(content["rows"]):
        return None
    return content["source"].setdefault("verdicts", {}), str(row)


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
