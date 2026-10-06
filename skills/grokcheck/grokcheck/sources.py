"""Turn documents into line-addressable text copies a lesson can cite.

Each copy lives at `.grokcheck/sources/<slug>.md` and is listed with its
sha256 in `.grokcheck/sources/sources.json`. A PDF copy marks the start of
each page with `<!-- page N -->`, so a cited line resolves to a page.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCES_DIR = PurePosixPath(".grokcheck/sources")
FETCH_TIMEOUT_SECONDS = 20
PDFTOTEXT_TIMEOUT_SECONDS = 120

_PAGE_MARKER = re.compile(r"^<!-- page (\d+) -->$")
_HEADINGS = {f"h{level}": "#" * level + " " for level in range(1, 7)}
_BLOCKS = {
    *_HEADINGS,
    *("p", "div", "li", "br", "tr", "section", "article", "blockquote", "pre"),
    *("ul", "ol", "table", "header", "footer", "main", "nav", "dd", "dt"),
}
_DROPPED = {"script", "style", "noscript", "template"}


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "grokcheck"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
        body: bytes = response.read()
    return body


@dataclass(frozen=True)
class Source:
    """One ingested document; `path` is the absolute path of its text copy."""

    slug: str
    path: Path
    origin: str
    sha256: str
    pages: int | None
    kind: str

    def as_json(self, project_root: Path) -> dict[str, Any]:
        """Return the manifest record, with `path` relative to `project_root`."""
        relative = self.path.relative_to(project_root.resolve()).as_posix()
        return {
            "slug": self.slug,
            "path": relative,
            "origin": self.origin,
            "sha256": self.sha256,
            "pages": self.pages,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class NeedsTranscription:
    """A PDF `pdftotext` could not read; the agent writes the copy to `target_path`."""

    target_path: Path
    page_count_hint: int | None


def ingest(
    origin: str | Path,
    project_root: Path,
    *,
    fetch: Callable[[str], bytes] = _http_get,
    pdftotext: Callable[[Path], str | None] | None = None,
) -> Source | NeedsTranscription:
    """Copy `origin` (a file path or an http(s) URL) into the project's sources.

    `fetch` reads a URL. `pdftotext` converts a PDF, returning `None` when it
    cannot, and is itself `None` when the binary is missing (see
    `system_pdftotext`); either way a PDF writes nothing and returns where the
    agent should put its own transcription. Ingesting a file
    that already sits in the sources directory records it in place, which is
    how that transcription enters the manifest.
    """
    sources_dir = project_root.resolve() / SOURCES_DIR
    url = urllib.parse.urlsplit(str(origin))
    is_url = url.scheme in {"http", "https"}
    origin_path = Path(origin).resolve()
    origin_text = str(origin) if is_url else str(origin_path)
    kind = "html" if is_url else _kind(origin_path)
    manifest = _read_manifest(sources_dir)
    if not is_url and origin_path.parent == sources_dir:
        return _record(sources_dir, manifest, origin_path, origin_text, kind)
    stem = (Path(url.path).stem or url.netloc) if is_url else origin_path.stem
    target = sources_dir / f"{_slug(stem, origin_text, manifest)}.md"
    if kind == "pdf":
        pdf_text = None if pdftotext is None else pdftotext(origin_path)
        if pdf_text is None:
            return NeedsTranscription(target, _page_count_hint(origin_path))
        text = _mark_pages(pdf_text)
    elif kind == "html":
        raw = fetch(origin_text) if is_url else origin_path.read_bytes()
        text = _html_to_markdown(raw.decode("utf-8", errors="replace"))
    else:
        text = origin_path.read_text(encoding="utf-8")
    sources_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return _record(sources_dir, manifest, target, origin_text, kind)


def system_pdftotext() -> Callable[[Path], str | None] | None:
    """Return a runner for the installed `pdftotext`, or `None` when it is missing.

    The runner returns `None` for a PDF `pdftotext` rejects.
    """
    binary = shutil.which("pdftotext")
    if binary is None:
        return None

    def run(pdf: Path) -> str | None:
        completed = subprocess.run(  # noqa: S603
            [binary, "-layout", str(pdf), "-"],
            check=False,
            capture_output=True,
            timeout=PDFTOTEXT_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.decode("utf-8", errors="replace")

    return run


def page_of(source: Source | Path, line: int) -> int | None:
    """Return the page holding 1-based `line`, or `None` before any page marker."""
    path = source.path if isinstance(source, Source) else source
    page = None
    lines = path.read_text(encoding="utf-8").splitlines()
    for text in lines[:line]:
        if match := _PAGE_MARKER.match(text):
            page = int(match[1])
    return page


def manifest_entries(project_root: Path) -> list[dict[str, Any]]:
    """Return the project's manifest records, oldest first."""
    return _read_manifest(project_root.resolve() / SOURCES_DIR)


def _html_to_markdown(html: str) -> str:
    """Reduce HTML to paragraphs and markdown headings, dropping scripts and styles."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "\n\n".join(parser.blocks) + "\n"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._words: list[str] = []
        self._prefix = ""
        self._dropped_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in _DROPPED:
            self._dropped_depth += 1
        elif tag in _BLOCKS:
            self._flush()
            self._prefix = _HEADINGS.get(tag, "- " if tag == "li" else "")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            self._dropped_depth = max(0, self._dropped_depth - 1)
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._dropped_depth:
            self._words.extend(data.split())

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._words:
            self.blocks.append(self._prefix + " ".join(self._words))
        self._words = []
        self._prefix = ""


def _record(
    sources_dir: Path,
    manifest: list[dict[str, Any]],
    copy: Path,
    origin: str,
    kind: str,
) -> Source:
    """Hash `copy` and write its record, replacing any earlier one for the slug."""
    data = copy.read_bytes()
    pages = sum(
        1 for text in data.decode("utf-8").splitlines() if _PAGE_MARKER.match(text)
    )
    source = Source(
        slug=copy.stem,
        path=copy,
        origin=origin,
        sha256=hashlib.sha256(data).hexdigest(),
        pages=pages or None,
        kind=kind,
    )
    project_root = sources_dir.parent.parent
    entries = [entry for entry in manifest if entry["slug"] != source.slug]
    entries.append(source.as_json(project_root))
    (sources_dir / "sources.json").write_text(
        json.dumps(entries, indent=2) + "\n", encoding="utf-8"
    )
    return source


def _read_manifest(sources_dir: Path) -> list[dict[str, Any]]:
    manifest_file = sources_dir / "sources.json"
    if not manifest_file.exists():
        return []
    entries: list[dict[str, Any]] = json.loads(manifest_file.read_text("utf-8"))
    return entries


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    return "text"


def _slug(stem: str, origin: str, manifest: list[dict[str, Any]]) -> str:
    """Normalise `stem`; reuse the slug of an earlier ingest of the same origin.

    Beyond PEP 503 (runs of `-_.` become `-`, lowercased), any other run of
    characters unsafe in a file name also becomes `-`.
    """
    for entry in manifest:
        if entry["origin"] == origin:
            slug: str = entry["slug"]
            return slug
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "source"
    taken = {entry["slug"] for entry in manifest}
    slug, counter = base, 1
    while slug in taken:
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def _mark_pages(text: str) -> str:
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return "".join(
        f"<!-- page {number} -->\n{page.rstrip()}\n"
        for number, page in enumerate(pages, start=1)
    )


def _page_count_hint(pdf: Path) -> int | None:
    """Count `/Type /Page` objects; object streams can hide them all, giving `None`."""
    count = len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes()))
    return count or None
