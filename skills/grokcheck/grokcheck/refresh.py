"""Find what an authored lesson says that the code or its spikes no longer back.

The lesson counts as written against the last commit before the lesson file
was last modified. The authored JSON is read without validation, since a
lesson whose cited lines moved may no longer validate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grokcheck import git
from grokcheck.lesson import format_claim_id
from grokcheck.schedule import Entry, stale
from grokcheck.sources import manifest_entries
from grokcheck.spikes import Change, rerun

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ChangedSpike:
    """A spike whose rerun differs from its record; `cited_at` holds lesson paths."""

    spike_id: str
    cited_at: tuple[str, ...]
    change: Change


@dataclass(frozen=True)
class Report:
    """What to re-author: claim paths, spikes, and scope files changed since `since`.

    `since` is `None` outside git, and then no claim or file counts as moved.
    """

    since: str | None
    stale_claims: list[str]
    changed_spikes: list[ChangedSpike]
    moved_files: list[str]


def report(lesson_path: Path, project_root: Path) -> Report:
    """Rerun the lesson's spikes and check its cited lines against HEAD."""
    raw: dict[str, Any] = json.loads(lesson_path.read_text(encoding="utf-8"))
    since = _lesson_commit(lesson_path, project_root)
    edited = _edited_sources(project_root)
    stale_claims: list[str] = []
    spike_citations: dict[str, list[str]] = {}
    for i, section in enumerate(raw.get("sections", [])):
        for j, element in enumerate(section.get("elements", [])):
            if element.get("type") == "spike":
                spike_citations.setdefault(element["spike_id"], []).append(
                    f"sections[{i}].elements[{j}]"
                )
            for k, claim in enumerate(element.get("claims", [])):
                path = format_claim_id(i, j, "claims", k)
                backing = claim.get("backing", {})
                if "spike_id" in backing:
                    spike_citations.setdefault(backing["spike_id"], []).append(path)
                elif "file" in backing and (
                    backing["file"] in edited
                    or _lines_moved(project_root, since, backing)
                ):
                    stale_claims.append(path)
    changed_spikes = [
        ChangedSpike(change.spike_id, tuple(spike_citations[change.spike_id]), change)
        for change in rerun(
            project_root / ".grokcheck" / "spikes", project_root, only=spike_citations
        )
    ]
    files = raw.get("scope", {}).get("files", [])
    moved = (
        git.output(project_root, "diff", "--name-only", since, "HEAD", "--", *files)
        if since and files
        else None
    )
    return Report(since, stale_claims, changed_spikes, git.lines(moved))


def _lesson_commit(lesson_path: Path, project_root: Path) -> str | None:
    written = int(lesson_path.stat().st_mtime)
    output = git.output(project_root, "rev-list", "-1", f"--before=@{written}", "HEAD")
    commit = (output or "").strip()
    return commit or None


def _lines_moved(
    project_root: Path, since: str | None, backing: dict[str, Any]
) -> bool:
    start, end = backing["lines"]
    entry = Entry("", "", since, (backing["file"],), ((start, end),), "")
    return stale(project_root, entry)


def _edited_sources(project_root: Path) -> set[str]:
    """Return the ingested copies whose content no longer matches the manifest."""
    edited: set[str] = set()
    for entry in manifest_entries(project_root):
        copy = project_root / entry["path"]
        if (
            not copy.is_file()
            or hashlib.sha256(copy.read_bytes()).hexdigest() != entry["sha256"]
        ):
            edited.add(entry["path"])
    return edited
