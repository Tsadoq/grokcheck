"""Turning documents into line-addressable copies under `.grokcheck/sources/`."""

import hashlib
import json
from pathlib import Path

import pytest
from grokcheck.sources import NeedsTranscription, Source, ingest, page_of

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "sources" / "guideline.html"
_PAGES_IN_FAKE_PDF = 2


def _no_network(url: str) -> bytes:
    pytest.fail(f"a local fixture must not be fetched, got {url}")


def test_ingest_html_fixture_to_markdown_with_manifest_and_hash(tmp_path: Path) -> None:
    """The copy keeps the headings, drops scripts, and the manifest hashes it."""
    source = ingest(_FIXTURE, tmp_path, fetch=_no_network, pdftotext=None)

    if not isinstance(source, Source):
        pytest.fail(f"expected a Source, got {source}")
    copy_bytes = source.path.read_bytes()
    lines = copy_bytes.decode("utf-8").splitlines()
    if "# Dosing" not in lines or "## Renal impairment" not in lines:
        pytest.fail(f"headings missing from the copy: {lines}")
    if any("alert(" in line for line in lines):
        pytest.fail(f"script text leaked into the copy: {lines}")
    manifest = json.loads(
        (tmp_path / ".grokcheck" / "sources" / "sources.json").read_text("utf-8")
    )
    expected = {
        "origin": str(_FIXTURE),
        "sha256": hashlib.sha256(copy_bytes).hexdigest(),
        "kind": "html",
    }
    if {key: manifest[0][key] for key in expected} != expected:
        pytest.fail(f"manifest record {manifest[0]} does not match {expected}")


def test_ingest_pdf_without_pdftotext_asks_for_transcription_and_with_it_marks_pages(
    tmp_path: Path,
) -> None:
    """No `pdftotext` defers to the agent; with it, form feeds become page markers."""
    root = tmp_path / "project"
    root.mkdir()
    pdf = tmp_path / "Paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 not parsed by the fake runner")

    def two_pages(_pdf: Path) -> str:
        return "Abstract\nfirst page\n\fMethods\nsecond page\n\f"

    deferred = ingest(pdf, root, pdftotext=None)

    if not isinstance(deferred, NeedsTranscription):
        pytest.fail(f"expected NeedsTranscription, got {deferred}")
    if (root / ".grokcheck" / "sources").exists():
        pytest.fail("a deferred PDF must not write anything")

    source = ingest(pdf, root, pdftotext=two_pages)

    if not isinstance(source, Source):
        pytest.fail(f"expected a Source, got {source}")
    copy = source.path.read_text("utf-8")
    if copy.count("<!-- page") != _PAGES_IN_FAKE_PDF:
        pytest.fail(f"expected one marker per page in {copy!r}")
    last_line = len(copy.splitlines())
    if page_of(source, last_line) != _PAGES_IN_FAKE_PDF:
        pytest.fail(f"line {last_line} of {copy!r} resolved to the wrong page")
