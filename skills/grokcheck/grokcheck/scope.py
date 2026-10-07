"""Guess what a lesson is about from the shape of the user's argument.

The guess is a starting point the agent states back to the user, who corrects
it; every guess carries a `reason` saying which rule picked it.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from grokcheck import git
from grokcheck.lesson import SUBJECTS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from grokcheck.lesson import Subject

_REVISION = re.compile(r"^[0-9a-f]{7,40}(\.\.\.?[0-9a-f]{7,40})?$|^HEAD(?:$|[~^]|\.\.)")
_SHA = re.compile(r"[0-9a-f]{7,40}")
_DECISION = re.compile(
    r"\badrs?\b|/decisions?/|/pull/\d+|/merge_requests/\d+|/\+/\d+", re.IGNORECASE
)
_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;#]*)"
)
_URL = re.compile(r"^https?://", re.IGNORECASE)
_DOCUMENT_SUFFIXES = frozenset({".pdf", ".md", ".txt", ".html"})


@dataclass(frozen=True)
class ScopeGuess:
    """A subject guess, the files it covers and the project state it was made at.

    `commit` is `None` outside a git repository. `pins` holds the named
    packages the project's dependency files mention, as `pins` reports them.
    `sources` lists the documents and URLs a `document` lesson ingests.
    """

    subject: Subject
    files: tuple[str, ...] = ()
    commit: str | None = None
    pins: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    sources: tuple[str, ...] = ()


def infer(args: Sequence[str], project_root: Path) -> ScopeGuess:
    """Guess the subject of `args`, the user's words split as the shell split them.

    A leading subject word, as in `decision <range>`, sets the subject and the
    rest of the words are guessed as usual.
    """
    words = " ".join(args).split()
    if len(words) > 1 and words[0].lower() in SUBJECTS:
        rest = _infer(words[1:], project_root)
        return dataclasses.replace(
            rest,
            subject=cast("Subject", words[0].lower()),
            reason=f"the first word names the subject; {rest.reason}",
        )
    return _infer(args, project_root)


def _infer(args: Sequence[str], project_root: Path) -> ScopeGuess:  # noqa: PLR0911
    commit = git.output(project_root, "rev-parse", "HEAD")
    commit = commit.strip() if commit else None
    text = " ".join(args)
    found = {
        name: version
        for name, version in pins(project_root, args).items()
        if version is not None
    }

    def guess(subject: Subject, reason: str, files: Iterable[str] = ()) -> ScopeGuess:
        return ScopeGuess(subject, tuple(files), commit, found, reason)

    if not args or text.lower() == "last change":
        return guess("change", *_last_change(project_root))
    revision = _revision(text.split(), project_root)
    if revision is not None:
        files = git.lines(_revision_files(revision, project_root))
        return guess("change", f"'{revision}' is a git revision", files)
    documents = _documents(args, project_root)
    if documents:
        reason = "every argument is a document or a URL"
        return ScopeGuess("document", (), commit, found, reason, tuple(documents))
    paths = _leading_paths(args, project_root)
    if paths:
        files = [file for path in paths for file in _files_under(path, project_root)]
        question = args[len(paths) :]
        if question and not any((project_root / path).is_dir() for path in paths):
            return guess("concept", "files followed by a question", files)
        return guess("area", "existing paths in the project", files)
    if len(found) == len(args):
        return guess("library", "every name is in the project's dependency files")
    padded = f" {text.lower()} "
    if any(cue in padded for cue in (" vs ", "should we", "option")):
        return guess("options", "the text compares alternatives")
    if _DECISION.search(text):
        return guess("decision", "the text names a merged change or decision record")
    return guess("concept", "no rule matched, so the text is taken as a concept")


def _revision(words: Sequence[str], project_root: Path) -> str | None:
    """Return the first word that is a revision: by shape when it leads, else by git."""
    for k, word in enumerate(words):
        if (k == 0 and _REVISION.match(word)) or _resolves(word, project_root):
            return word
    return None


def _resolves(word: str, project_root: Path) -> bool:
    """Say whether `word` is a commit range, or a commit hash, that git resolves."""
    head, dots, tail = word.partition("..")
    tail = tail.removeprefix(".")
    sides = [head, tail] if dots else [word]
    if not all(sides) or ".." in tail or (not dots and not _SHA.fullmatch(word)):
        return False
    return all(
        git.output(
            project_root, "rev-parse", "--verify", "--quiet", f"{side}^{{commit}}"
        )
        for side in sides
    )


def _revision_files(revision: str, project_root: Path) -> str | None:
    if ".." in revision:
        return git.output(project_root, "diff", "--name-only", revision)
    return git.output(
        project_root,
        "diff-tree",
        "--root",
        "-r",
        "--name-only",
        "--no-commit-id",
        revision,
    )


def pins(project_root: Path, names: Iterable[str]) -> dict[str, str | None]:
    """Map each name to what the project's dependency files say about it.

    Names compare after PEP 503 normalisation. The value is the exact version
    when one is pinned, otherwise the requirement's specifier (`""` for a bare
    name), and `None` when no file names the package. `uv.lock` wins over
    `poetry.lock`, which wins over `requirements*.txt`, then `pyproject.toml`.
    """
    known: dict[str, str] = {}
    for source in reversed(_dependency_sources(project_root)):
        known.update(source)
    return {name: known.get(normalise(name)) for name in names}


def normalise(name: str) -> str:
    """Return `name` normalised as PEP 503 compares package names."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _last_change(project_root: Path) -> tuple[str, list[str]]:
    """Return why and which files: uncommitted ones, else the last commit's."""
    uncommitted = git.lines(git.output(project_root, "diff", "--name-only", "HEAD"))
    uncommitted += git.lines(
        git.output(project_root, "ls-files", "--others", "--exclude-standard")
    )
    if uncommitted:
        return "uncommitted changes in the working tree", uncommitted
    listed = git.output(project_root, "show", "--name-only", "--format=", "HEAD")
    return "no uncommitted changes, so the last commit", git.lines(listed)


def _documents(args: Sequence[str], project_root: Path) -> list[str] | None:
    """Return what to ingest when every arg is a document, else `None`.

    A document is an http(s) URL, a document file git does not track, or a
    folder whose files are mostly documents, which contributes those files.
    """
    found: list[str] = []
    for arg in args:
        if _URL.match(arg):
            found.append(arg)
        elif (project_root / arg).is_dir():
            files = _files_under(arg, project_root)
            documents = [file for file in files if _is_document(file)]
            if len(documents) * 2 <= len(files):
                return None
            found.extend(documents)
        elif _is_document(arg) and not git.output(project_root, "ls-files", "--", arg):
            found.append(arg)
        else:
            return None
    return found


def _is_document(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _DOCUMENT_SUFFIXES


def _leading_paths(args: Sequence[str], project_root: Path) -> list[str]:
    paths: list[str] = []
    for arg in args:
        if not (project_root / arg).exists():
            break
        paths.append(arg)
    return paths


def _files_under(path: str, project_root: Path) -> list[str]:
    target = project_root / path
    if target.is_file():
        return [path]
    tracked = git.lines(git.output(project_root, "ls-files", "--", path))
    if tracked:
        return tracked
    return sorted(
        str(file.relative_to(project_root))
        for file in target.rglob("*")
        if file.is_file()
    )


def _dependency_sources(project_root: Path) -> list[dict[str, str]]:
    """Return each dependency file's packages, highest precedence first."""
    sources = [
        _lock_packages(project_root / "uv.lock"),
        _lock_packages(project_root / "poetry.lock"),
    ]
    for requirements in sorted(project_root.glob("requirements*.txt")):
        lines = requirements.read_text(encoding="utf-8").splitlines()
        sources.append(_requirements(lines))
    pyproject = project_root / "pyproject.toml"
    if pyproject.is_file():
        sources.append(_requirements(_pyproject_requirements(pyproject)))
    return sources


def _lock_packages(lockfile: Path) -> dict[str, str]:
    packages = _toml(lockfile).get("package", [])
    return {
        normalise(package["name"]): str(package.get("version", ""))
        for package in packages
        if "name" in package
    }


def _pyproject_requirements(pyproject: Path) -> list[str]:
    data = _toml(pyproject)
    project = data.get("project", {})
    groups = [
        project.get("dependencies", []),
        *project.get("optional-dependencies", {}).values(),
        *data.get("dependency-groups", {}).values(),
    ]
    return [entry for group in groups for entry in group if isinstance(entry, str)]


def _toml(path: Path) -> dict[str, Any]:
    """Return the TOML at `path`, or `{}` when it is missing or unreadable."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _requirements(lines: Iterable[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        match = _REQUIREMENT.match(line)
        if match is None:
            continue
        name, specifier = match[1], match[2].strip()
        exact = specifier.startswith("==") and "," not in specifier
        found[normalise(name)] = specifier[2:].strip() if exact else specifier
    return found
