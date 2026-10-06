"""Checks on the Mermaid source of a lesson's diagrams.

`check` finds what makes a diagram unfit to load; `warnings` finds what an
author should look at; `render_error` asks `mmdc`, when installed, whether
Mermaid itself can draw it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MIN_NODES = 7
MAX_NODES = 12
_RENDER_TIMEOUT_SECONDS = 120
_ERROR_LINES = 4
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CHROMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
_KEYWORDS = frozenset(
    {"subgraph", "end", "classDef", "class", "style", "linkStyle", "click", "direction"}
)
_ID = r"[A-Za-z_][\w.]*"
_SHAPE = r"(?:\[+[^\]]*\]+|\(+[^)]*\)+|\{+[^}]*\}+|>[^\]]*\])?(?::::\w+)?"
_FLOW_EDGE = re.compile(
    rf"(?P<src>{_ID}){_SHAPE}\s*"
    r"(?:--\s+(?P<text>[^->|][^>]*?)\s+)?"
    r"(?P<arrow><?[-.=]{2,}[>xo]?)\s*"
    r"(?:\|(?P<pipe>[^|]*)\|)?\s*"
    rf"(?=(?P<dst>{_ID}))"
)
_FLOW_NODE = re.compile(rf"\s*(?P<id>{_ID}){_SHAPE}\s*")
_SEQUENCE_MESSAGE = re.compile(
    rf"\s*(?P<src>{_ID})\s*(?P<arrow>--?>>?|--?[x)])\s*[+-]?"
    rf"(?P<dst>{_ID})\s*:\s*(?P<label>.*)"
)
_PARTICIPANT = re.compile(rf"\s*(?:participant|actor)\s+(?P<id>{_ID})")
_RELATION = re.compile(rf"\s*(?P<src>{_ID})\s*\S*--\S*\s*(?P<dst>{_ID})")


@dataclass(frozen=True)
class Edge:
    """One flowchart link or sequence message; `label` is empty when unlabelled."""

    src: str
    arrow: str
    dst: str
    label: str


def check(text: str) -> list[str]:
    """Return why the diagram `text` must not load, or nothing when it may."""
    problems: list[str] = []
    count = len(symbols(text))
    if count > MAX_NODES:
        problems.append(f"{count} nodes; split the diagram (max {MAX_NODES})")
    problems.extend(
        f"edge {e.src} {e.arrow} {e.dst} has no label"
        for e in edges(text)
        if not e.label
    )
    return problems


def warnings(text: str, project_root: Path) -> list[str]:
    """Return what to review in `text`: a small diagram, symbols not in the project."""
    found: list[str] = []
    count = len(symbols(text))
    if count < MIN_NODES:
        found.append(
            f"{count} nodes; a list or prose may serve better (min {MIN_NODES})"
        )
    return found + check_symbols(text, project_root)


def edges(text: str) -> list[Edge]:
    """Return the flowchart links and sequence messages in `text`."""
    kind, lines = _parse(text)
    found: list[Edge] = []
    for line in lines:
        if kind == "sequence":
            match = _SEQUENCE_MESSAGE.fullmatch(line)
            if match:
                found.append(Edge(*match.group("src", "arrow", "dst", "label")))
        elif kind == "flowchart" and not _is_statement(line):
            found.extend(
                Edge(
                    m["src"],
                    m["arrow"],
                    m["dst"],
                    (m["pipe"] or m["text"] or "").strip(),
                )
                for m in _FLOW_EDGE.finditer(line)
            )
    return found


def symbols(text: str) -> list[str]:
    """Return the node ids of `text` in first-seen order."""
    kind, lines = _parse(text)
    found = [ident for edge in edges(text) for ident in (edge.src, edge.dst)]
    for line in lines:
        if kind == "sequence":
            match = _PARTICIPANT.match(line)
            found.extend([match["id"]] if match else [])
        elif kind == "flowchart":
            match = _FLOW_NODE.fullmatch(line)
            if match and not _is_statement(line):
                found.append(match["id"])
        else:
            match = _RELATION.match(line)
            found.extend(match.group("src", "dst") if match else [])
    return list(dict.fromkeys(found))


def check_symbols(text: str, project_root: Path) -> list[str]:
    """Return a warning for each `module.symbol` node id the project does not define.

    A node id counts as found when a file named after its module, anywhere
    under `project_root`, mentions the symbol as a whole word.
    """
    missing: list[str] = []
    for ident in symbols(text):
        module, _, name = ident.rpartition(".")
        if not module:
            continue
        stem = module.rsplit(".", 1)[-1]
        word = re.compile(rf"\b{re.escape(name)}\b")
        if not any(
            word.search(path.read_text("utf-8", errors="replace"))
            for path in project_root.rglob(f"{stem}.*")
            if path.is_file()
        ):
            missing.append(f"node {ident} names nothing in the project")
    return missing


def render_error(text: str) -> str | None:
    """Render `text` with `mmdc` and return its error, or None when it renders.

    Callers check `shutil.which("mmdc")` first.
    """
    config: dict[str, object] = {"args": ["--no-sandbox"]}
    chrome = next(filter(None, map(shutil.which, _CHROMES)), None)
    if chrome:
        config["executablePath"] = chrome
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "diagram.mmd").write_text(text, "utf-8")
        (folder / "puppeteer.json").write_text(json.dumps(config), "utf-8")
        command = ["mmdc", "-q", "-i", "diagram.mmd", "-o", "diagram.svg"]
        try:
            done = subprocess.run(  # noqa: S603
                [*command, "-p", "puppeteer.json"],
                cwd=folder,
                capture_output=True,
                text=True,
                timeout=_RENDER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"mmdc took longer than {_RENDER_TIMEOUT_SECONDS}s"
    if done.returncode == 0:
        return None
    output = _ANSI.sub("", done.stderr or done.stdout)
    lines = [line for line in output.splitlines() if line.strip()]
    return "\n".join(lines[:_ERROR_LINES]) or f"mmdc exited {done.returncode}"


def _parse(text: str) -> tuple[str, list[str]]:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("%%")
    ]
    header = lines[0].split()[0] if lines else ""
    if header in {"flowchart", "graph"}:
        kind = "flowchart"
    elif header == "sequenceDiagram":
        kind = "sequence"
    else:
        kind = "other"
    return kind, lines[1:]


def _is_statement(line: str) -> bool:
    return line.split(maxsplit=1)[0] in _KEYWORDS
