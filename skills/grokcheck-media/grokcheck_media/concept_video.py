"""Narrated concept videos: chapter plans, cached chapter renders, the joined film.

A chapter script is JSON written by an agent from one `ChapterSpec`:
`{concept_id, chapter_id, title, word_budget, beats}`, where each beat is
`{say, show, code?, claims?}`: one narration sentence, the text on screen while
it plays, whether that text is code, and claims shaped like lesson claims.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import html
import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from grokcheck.lesson import VIDEO_CACHE_DIR

from grokcheck_media import checks, tts

if TYPE_CHECKING:
    from collections.abc import Sequence

Renderer = Literal["manim", "hyperframes"]

WORDS_PER_MINUTE = 150
_OVER_BUDGET = 1.2
_GAP_SECONDS = 0.45
_TRANSITION_SECONDS = 0.3
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_SCRIPT_FIELDS = ("concept_id", "chapter_id", "title", "word_budget", "beats")
_run = functools.partial(subprocess.run, check=True, capture_output=True)
_MANIM_SCENE = """\
import json
from manim import *

BEATS = json.loads({beats!r})


class Chapter(Scene):
    def construct(self):
        shown = None
        for beat in BEATS:
            font = "DejaVu Sans Mono" if beat["code"] else "DejaVu Sans"
            text = Text(beat["show"], font=font, font_size=32)
            if text.width > config.frame_width - 1:
                text.scale_to_fit_width(config.frame_width - 1)
            self.add_sound(beat["wav"])
            if shown is None:
                self.play(FadeIn(text), run_time={transition})
            else:
                self.play(ReplacementTransform(shown, text), run_time={transition})
            self.wait(beat["seconds"] - {transition})
            shown = text
"""


@dataclass(frozen=True)
class ChapterSpec:
    """Chapter `id` narrates `sections` in at most `word_budget` words.

    `cited` holds each `(file, first line, last line)` its sections' claims cite.
    """

    id: str
    title: str
    sections: list[str]
    word_budget: int
    cited: list[tuple[str, int, int]]


@dataclass(frozen=True)
class Cue:
    """Narration `text` spoken from `start` to `end` seconds into the video."""

    start: float
    end: float
    text: str


def concept_id(lesson: dict[str, Any]) -> str:
    """Name the cache folder of a lesson's concept video after its title."""
    return re.sub(r"[^a-z0-9]+", "-", lesson["title"].lower()).strip("-") or "concept"


def plan_chapters(
    lesson: dict[str, Any], minutes: float, chapter_minutes: float
) -> list[ChapterSpec]:
    """Split the sections of the lesson JSON `lesson` into chapters in order.

    There are `minutes / chapter_minutes` chapters, never more than sections;
    earlier chapters take the extra section when they do not divide evenly.
    """
    sections = lesson["sections"]
    count = max(1, min(len(sections), round(minutes / chapter_minutes)))
    size, extra = divmod(len(sections), count)
    chapters = []
    start = 0
    for number in range(count):
        group = sections[start : start + size + (number < extra)]
        start += len(group)
        chapters.append(
            ChapterSpec(
                id=f"ch{number + 1:02d}",
                title=group[0]["title"],
                sections=[section["id"] for section in group],
                word_budget=round(chapter_minutes * WORDS_PER_MINUTE),
                cited=_cited(group),
            )
        )
    return chapters


def lint_script(script: dict[str, Any]) -> list[str]:
    """Return what is wrong with a chapter script; over budget by 20% is wrong."""
    missing = [key for key in _SCRIPT_FIELDS if key not in script]
    if missing:
        return [f"the script lacks {', '.join(missing)}"]
    problems = [
        f"{key} must start with a letter or digit and hold only letters, digits,"
        " '_' and '-'"
        for key in ("concept_id", "chapter_id")
        if not _ID.fullmatch(str(script[key]))
    ]
    beats = script["beats"]
    if not isinstance(beats, list) or not beats:
        return [*problems, "beats must be a non-empty array"]
    problems += [
        f"beats[{index}].{key} must be a non-empty string"
        for index, beat in enumerate(beats)
        for key in ("say", "show")
        if not isinstance(beat.get(key), str) or not beat[key].strip()
    ]
    words = sum(len(str(beat.get("say", "")).split()) for beat in beats)
    budget = script["word_budget"]
    if words > budget * _OVER_BUDGET:
        problems.append(
            f"the narration has {words} words, over 20% above its budget of {budget}"
        )
    return problems


def build_chapter(
    script: dict[str, Any],
    renderer: Renderer,
    out_dir: Path,
    *,
    project_root: Path,
    allow_cloud: bool = False,
) -> dict[str, Any]:
    """Render, check and cache one chapter, then copy it to `out_dir/<chapter_id>/`.

    The cache key is what the chapter shows and says and the renderer, so a
    claim edit or an unchanged chapter is never rendered again. Returns the
    chapter record plus `claims`, the claim manifest for a fresh-context check.
    """
    problems = lint_script(script)
    if problems:
        raise ValueError("; ".join(problems))
    rendered = [
        {name: beat.get(name) for name in ("say", "show", "code")}
        for beat in script["beats"]
    ]
    key = json.dumps([script["title"], rendered, renderer])
    sha = hashlib.sha256(key.encode()).hexdigest()[:16]
    cache = VIDEO_CACHE_DIR / script["concept_id"] / script["chapter_id"] / sha
    cached = (cache / "chapter.json").is_file()
    if not cached:
        cache.mkdir(parents=True, exist_ok=True)
        record = _render(script, renderer, cache, allow_cloud=allow_cloud)
        (cache / "chapter.json").write_text(json.dumps(record), encoding="utf-8")
    chapter_dir = out_dir / script["chapter_id"]
    chapter_dir.mkdir(parents=True, exist_ok=True)
    for name in ("video.mp4", "captions.vtt", "chapter.json", "sheet.png"):
        shutil.copyfile(cache / name, chapter_dir / name)
    record = json.loads((cache / "chapter.json").read_text(encoding="utf-8"))
    return {
        **record,
        "path": str(chapter_dir / "video.mp4"),
        "captions": str(chapter_dir / "captions.vtt"),
        "contact_sheet": str(chapter_dir / "sheet.png"),
        "cached": cached,
        "claims": checks.claim_manifest(script, project_root),
    }


def join(chapters_dir: Path, out: Path) -> dict[str, Any]:
    """Concatenate every chapter under `chapters_dir` into `out`, in name order.

    The MP4 gets one chapter marker per chapter, and `out` with suffix `.vtt`
    gets the chapters' captions shifted to their place in the film.
    """
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(chapters_dir.glob("*/chapter.json"))
    ]
    if not records:
        msg = f"{chapters_dir} holds no rendered chapter"
        raise ValueError(msg)
    work = out.with_name(f"{out.stem}-join")
    work.mkdir(parents=True, exist_ok=True)
    listing = work / "chapters.txt"
    listing.write_text(
        "".join(
            f"file '{_quoted(chapters_dir / r['chapter_id'] / 'video.mp4')}'\n"
            for r in records
        ),
        encoding="utf-8",
    )
    metadata = [";FFMETADATA1"]
    cues: list[Cue] = []
    offset = 0.0
    for record in records:
        end = offset + record["duration"]
        metadata += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={round(offset * 1000)}",
            f"END={round(end * 1000)}",
            f"title={_ffmetadata(record['title'])}",
        ]
        cues += [
            Cue(cue["start"] + offset, cue["end"] + offset, cue["text"])
            for cue in record["cues"]
        ]
        offset = end
    (work / "chapters.meta").write_text("\n".join(metadata) + "\n", encoding="utf-8")
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    command += ["-i", str(work / "chapters.meta"), "-map", "0"]
    command += ["-map_metadata", "1", "-map_chapters", "1"]
    command += ["-c:v", "libx264", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    _run([*command, str(out)])
    captions = out.with_suffix(".vtt")
    captions.write_text(_vtt(cues), encoding="utf-8")
    return {
        "path": str(out),
        "captions": str(captions),
        "chapters": len(records),
        "duration": offset,
    }


def _cited(sections: Sequence[dict[str, Any]]) -> list[tuple[str, int, int]]:
    cited = (
        (claim["backing"]["file"], *claim["backing"]["lines"])
        for section in sections
        for element in section.get("elements", [])
        for claim in element.get("claims", [])
        if "file" in claim["backing"]
    )
    return list(dict.fromkeys(cited))


def _render(
    script: dict[str, Any], renderer: Renderer, work: Path, *, allow_cloud: bool
) -> dict[str, Any]:
    """Speak, render and check the chapter in `work`, returning its record."""
    beats = script["beats"]
    says = [beat["say"] for beat in beats]
    audio = []
    for number, say in enumerate(says, start=1):
        wav = work / f"beat-{number:03d}.wav"
        if tts.synthesise(say, wav, allow_cloud=allow_cloud) is None:
            msg = "no speech engine: install Kokoro or pass --allow-cloud"
            raise ValueError(msg)
        audio.append(wav)
    seconds = [_seconds(wav) + _GAP_SECONDS for wav in audio]
    cues = _cues(says, seconds)
    video = work / "video.mp4"
    timed = [
        {**beat, "code": bool(beat.get("code")), "wav": str(wav), "seconds": length}
        for beat, wav, length in zip(beats, audio, seconds, strict=True)
    ]
    if renderer == "manim":
        _manim(timed, work, video)
    else:
        _hyperframes(timed, cues, work, video)
    duration = sum(seconds)
    (work / "captions.vtt").write_text(_vtt(cues), encoding="utf-8")
    checks.contact_sheet(video, duration, work / "sheet.png")
    differences = checks.transcribe_back(says, audio)
    return {
        "chapter_id": script["chapter_id"],
        "title": script["title"],
        "duration": duration,
        "cues": [dataclasses.asdict(cue) for cue in cues],
        "transcript_diff": [dataclasses.asdict(d) for d in differences],
    }


def _cues(says: Sequence[str], seconds: Sequence[float]) -> list[Cue]:
    """Time each sentence from the start of its beat to its last spoken word."""
    cues = []
    start = 0.0
    for say, length in zip(says, seconds, strict=True):
        cues.append(Cue(start, start + length - _GAP_SECONDS, say))
        start += length
    return cues


def _manim(beats: list[dict[str, Any]], work: Path, video: Path) -> None:
    scene = work / "scene.py"
    scene.write_text(
        _MANIM_SCENE.format(beats=json.dumps(beats), transition=_TRANSITION_SECONDS),
        encoding="utf-8",
    )
    media = work / "media"
    command = ["manim", "render", "--resolution", "1920,1080", "--frame_rate", "30"]
    command += ["--media_dir", str(media), "-o", "chapter", str(scene), "Chapter"]
    _run(command)
    shutil.move(next(media.rglob("chapter.mp4")), video)


def _hyperframes(
    beats: list[dict[str, Any]], cues: Sequence[Cue], work: Path, video: Path
) -> None:
    """Write a HyperFrames composition: one text clip and one audio clip per beat."""
    total = sum(beat["seconds"] for beat in beats)
    clips = []
    for beat, cue in zip(beats, cues, strict=True):
        timing = f'data-start="{cue.start:.3f}" data-duration="{beat["seconds"]:.3f}"'
        tag = "pre" if beat["code"] else "p"
        clips.append(
            f'<div class="clip beat" {timing} data-track-index="0">'
            f"<{tag}>{html.escape(beat['show'])}</{tag}></div>"
            f'<audio {timing} data-track-index="1"'
            f' src="{Path(beat["wav"]).name}"></audio>'
        )
    (work / "index.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "body{margin:0;background:#0f1117;color:#e8e6e3;font:48px sans-serif}"
        ".beat{position:absolute;inset:0;display:grid;place-items:center;"
        "padding:80px;text-align:center}pre{font:40px monospace;text-align:left}"
        "</style></head><body>"
        f'<div id="root" data-composition-id="chapter" data-start="0"'
        f' data-duration="{total:.3f}" data-width="1920" data-height="1080">'
        f"{''.join(clips)}</div></body></html>",
        encoding="utf-8",
    )
    _run(["hyperframes", "render", str(work), "--output", str(video)])


def _vtt(cues: Sequence[Cue]) -> str:
    blocks = [
        f"{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}" for cue in cues
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def _timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    return f"{hours:02d}:{minutes:02d}:{millis // 1000:02d}.{millis % 1000:03d}"


def _seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def _quoted(file: Path) -> str:
    return str(file.resolve()).replace("'", "'\\''")


def _ffmetadata(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)
