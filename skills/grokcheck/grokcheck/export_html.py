"""Export: a served lesson as one HTML file that works offline in any browser.

The page is the live lesson page. Its modules are bundled into one inline
script, the styles, fonts and videos are inlined, and the lesson travels with
its answer keys so the page grades in the browser without the server.
"""

from __future__ import annotations

import base64
import dataclasses
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import TYPE_CHECKING

from grokcheck.lesson import (
    DiagramElement,
    GatedElement,
    OptionsElement,
    VideoElement,
)
from grokcheck.run import LessonRun, _diffs_by_id, public_view_for

if TYPE_CHECKING:
    from grokcheck.lesson import Lesson, Question

MAX_BYTES = 14 * 1024 * 1024
FFMPEG_SECONDS = 600

_IMPORT = re.compile(r'^import \{([^}]*)\} from "\./([\w-]+)\.js";\n', re.MULTILINE)
_EXPORT = re.compile(
    r"^export (?:async function|function|const|let|class) (\w+)", re.MULTILINE
)
_FONT_URL = re.compile(r'url\("(vendor/fonts/[\w.-]+\.woff2)"\)')
_BODY = re.compile(r"<body>(.*)</body>", re.DOTALL)


@dataclass
class HtmlExport:
    """The page, and the ids of the videos re-encoded or left out to fit."""

    text: str
    shrunk: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def render_html(
    run: LessonRun, web_dir: Path, *, fragment: bool = False, max_bytes: int = MAX_BYTES
) -> HtmlExport:
    """Render `run`'s lesson as it is first served, as one self-contained page.

    Videos over `max_bytes` are re-encoded at 720p when ffmpeg is found, then
    dropped largest first until the page fits. With `fragment`, the page has
    no document, head or body tags, so a host page can wrap it.
    """
    lesson = run.lesson
    videos = [
        element
        for section in lesson.sections
        for element in section.elements
        if isinstance(element, VideoElement)
    ]
    data = _embedded(run)
    media = {v.id: _media(Path(v.src), Path(v.captions)) for v in videos}
    result = HtmlExport("")
    with tempfile.TemporaryDirectory() as scratch:
        result.text = _page(lesson, web_dir, data, media, fragment=fragment)
        if _size(result.text) > max_bytes and videos and (ffmpeg := _ffmpeg()):
            for video in videos:
                small = Path(scratch) / f"{video.id}.mp4"
                try:
                    _shrink(ffmpeg, Path(video.src), small)
                except (OSError, subprocess.SubprocessError):
                    continue
                media[video.id]["video"] = _data_uri(small, "video/mp4")
                result.shrunk.append(video.id)
            result.text = _page(lesson, web_dir, data, media, fragment=fragment)
    while _size(result.text) > max_bytes and media:
        largest = max(media, key=lambda key: len(media[key]["video"]))
        del media[largest]
        result.dropped.append(largest)
        result.text = _page(lesson, web_dir, data, media, fragment=fragment)
    return result


def answer_key(question: Question) -> dict[str, object]:
    """Return `question` whole, answer key included, as web/grading.js reads it."""
    return {**dataclasses.asdict(question), "type": question.type_name}


def _embedded(run: LessonRun) -> dict[str, object]:
    lesson = run.lesson
    elements = [e for section in lesson.sections for e in section.elements]
    questions = [
        *lesson.probe,
        *(q for section in lesson.sections for q in section.checkpoints),
        *lesson.final,
    ]
    return {
        "lesson": public_view_for(LessonRun(lesson, run.lesson_dir)),
        "keys": {q.id: answer_key(q) for q in questions},
        "gates": {
            e.gate: lesson.gated_payload(e)
            for e in elements
            if isinstance(e, GatedElement) and e.gate
        },
        "options": {
            e.id: lesson.gated_payload(e)
            for e in elements
            if isinstance(e, OptionsElement) and e.reader_first
        },
        "asks": {
            diff_id: {
                str(ask.line): {"question": ask.question, "answer": ask.answer}
                for ask in diff.asks
            }
            for diff_id, (_, diff) in _diffs_by_id(lesson).items()
        },
    }


def _page(
    lesson: Lesson,
    web_dir: Path,
    data: dict[str, object],
    media: dict[str, dict[str, str]],
    *,
    fragment: bool,
) -> str:
    embedded = json.dumps({**data, "media": media}).replace("<", "\\u003c")
    has_diagram = any(
        isinstance(element, DiagramElement)
        for section in lesson.sections
        for element in section.elements
    )
    vendor = ["vendor/highlight/highlight.min.js"]
    if has_diagram:
        vendor.append("vendor/mermaid/mermaid.tiny.js")
    scripts = "".join(
        f"<script>{_script_safe((web_dir / name).read_text('utf-8'))}</script>\n"
        for name in vendor
    )
    head = (
        f"<title>{html.escape(lesson.title)}</title>\n"
        f"<style>\n{_css(web_dir)}</style>\n"
    )
    body = _BODY.search((web_dir / "index.html").read_text("utf-8"))
    if body is None:
        msg = "index.html has no <body>"
        raise ValueError(msg)
    content = (
        f"{body[1].strip()}\n"
        f'<script type="application/json" id="grokcheck-lesson">{embedded}</script>\n'
        f"{scripts}"
        f'<script type="module">\n{_script_safe(bundle(web_dir))}</script>\n'
    )
    if fragment:
        return head + content
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"{head}</head>\n<body>\n{content}</body>\n</html>\n"
    )


def bundle(web_dir: Path, entry: str = "app") -> str:
    """Bundle `entry` and the modules it imports into one module script.

    Each module runs in its own function scope, in dependency order; its
    imports become reads of the modules already run, its exports its result.
    """
    sources: dict[str, str] = {}
    pending = [entry]
    while pending:
        name = pending.pop()
        if name not in sources:
            sources[name] = (web_dir / f"{name}.js").read_text("utf-8")
            pending.extend(match[2] for match in _IMPORT.finditer(sources[name]))
    graph = {
        name: {match[2] for match in _IMPORT.finditer(text)}
        for name, text in sources.items()
    }
    parts = ["const modules = {};"]
    for name in TopologicalSorter(graph).static_order():
        body = _IMPORT.sub(
            lambda match: (
                "const {"
                + match[1].replace(" as ", ": ")
                + f'}} = modules["{match[2]}"];\n'
            ),
            sources[name],
        )
        exported = _EXPORT.findall(body)
        body = _EXPORT.sub(lambda match: match[0].removeprefix("export "), body)
        parts.append(
            f'modules["{name}"] = (() => {{\n{body}\n'
            f"return {{ {', '.join(exported)} }};\n}})();"
        )
    return "\n".join(parts) + "\n"


def _css(web_dir: Path) -> str:
    def font(match: re.Match[str]) -> str:
        return f'url("{_data_uri(web_dir / match[1], "font/woff2")}")'

    light = (web_dir / "vendor/highlight/github.min.css").read_text("utf-8")
    dark = (web_dir / "vendor/highlight/github-dark.min.css").read_text("utf-8")
    style = _FONT_URL.sub(font, (web_dir / "style.css").read_text("utf-8"))
    return (
        f"{light}\n"
        "@media (prefers-color-scheme: dark) {\n"
        f':root:not([data-theme="light"]) {{\n{dark}\n}}\n}}\n'
        f':root[data-theme="dark"] {{\n{dark}\n}}\n'
        f"{style}"
    )


def _media(video: Path, captions: Path) -> dict[str, str]:
    return {
        "video": _data_uri(video, "video/mp4"),
        "captions": _data_uri(captions, "text/vtt"),
    }


def _data_uri(path: Path, media_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _script_safe(text: str) -> str:
    """Keep inline script text from ending its `<script>` or opening an escape."""
    return text.replace("<!--", "\\x3C!--").replace("</script", "\\x3C/script")


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _ffmpeg() -> str | None:
    home = os.environ.get("CLAUDE_PLUGIN_DATA")
    cache = Path(home) / "media" if home else Path.home() / ".cache" / "grokcheck"
    bundled = cache / "media-venv" / "bin" / "ffmpeg"
    return shutil.which("ffmpeg") or (str(bundled) if bundled.exists() else None)


def _shrink(ffmpeg: str, source: Path, out: Path) -> None:
    subprocess.run(  # noqa: S603
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            "scale=-2:'min(720,ih)'",
            "-c:v",
            "libx264",
            "-crf",
            "30",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-movflags",
            "+faststart",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=FFMPEG_SECONDS,
    )
