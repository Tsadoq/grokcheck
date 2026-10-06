"""A trace element's stepper as a narrated MP4: one screenshot per step."""

from __future__ import annotations

import dataclasses
import functools
import json
import math
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import grokcheck
from grokcheck.lesson import PredictState, TraceElement
from grokcheck.run import LessonRun

from grokcheck_media import tts

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grokcheck.lesson import Lesson, Section, TraceStep

Runner = Callable[[list[str]], object]

_GROKCHECK_DIR = Path(grokcheck.__file__).parent
_SESSION_WAIT_SECONDS = 10.0
_SHEET_COLUMNS = 4
_run: Runner = functools.partial(subprocess.run, check=True)


def render(  # noqa: PLR0913
    lesson_dir: Path,
    element_id: str,
    out: Path,
    *,
    hold: float = 2.5,
    rate: float = 1.0,
    allow_cloud: bool = False,
) -> dict[str, Any]:
    """Write the MP4 of trace `element_id` to `out` and a contact sheet beside it."""
    trace = _trace(LessonRun.open(lesson_dir).lesson, element_id)
    work = out.with_name(f"{out.stem}-frames")
    work.mkdir(parents=True, exist_ok=True)
    shots = frames(lesson_dir, element_id, work)
    steps = [step for version in trace.versions for step in version.steps]
    audio = narrate(steps, work, allow_cloud=allow_cloud)
    assemble(shots, audio, out, hold=hold, rate=rate)
    sheet = out.with_name(f"{out.stem}-sheet.png")
    contact_sheet(shots, sheet)
    return {
        "path": str(out),
        "contact_sheet": str(sheet),
        "frames": len(shots),
        "narrated": any(audio),
    }


def frames(lesson_dir: Path, element_id: str, out_dir: Path) -> list[Path]:
    """Screenshot every step of every version of the trace into `out_dir`.

    A throwaway copy of the run holds only the trace's section, with its gate
    already answered so no step is withheld.
    """
    lesson = LessonRun.open(lesson_dir).lesson
    trace = _trace(lesson, element_id)
    with tempfile.TemporaryDirectory() as project:
        run = LessonRun.create(_only_trace(lesson, trace), Path(project))
        if trace.gate:
            run.answer(trace.gate, _gate(lesson, trace.gate).correct, "sure")
        server = subprocess.Popen(  # noqa: S603
            [sys.executable, _GROKCHECK_DIR, "_server", run.lesson_dir, "--no-open"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            return _screenshots(_session_url(run.lesson_dir, server), trace, out_dir)
        finally:
            server.terminate()
            server.wait()


def narrate(
    steps: Sequence[TraceStep], out_dir: Path, *, allow_cloud: bool = False
) -> list[Path | None]:
    """Speak each step's narration into one WAV per step.

    Every entry is None when no speech engine is available.
    """
    spoken: list[Path | None] = []
    for index, step in enumerate(steps, start=1):
        parts = []
        for number, sentence in enumerate(step.narration, start=1):
            part = out_dir / f"step-{index:04d}-{number}.wav"
            if tts.synthesise(sentence, part, allow_cloud=allow_cloud) is None:
                return [None] * len(steps)
            parts.append(part)
        spoken.append(_joined(parts, out_dir / f"step-{index:04d}.wav"))
    return spoken


def assemble(  # noqa: PLR0913
    frames: Sequence[Path],
    audio: Sequence[Path | None],
    out: Path,
    *,
    hold: float = 2.5,
    rate: float = 1.0,
    run: Runner = _run,
) -> None:
    """Encode `frames` as an H.264 MP4, each shown while its `audio` plays.

    `rate` speeds narration up; a frame without audio is shown for `hold`
    seconds. The ffmpeg concat lists are written beside the first frame.
    """
    work = frames[0].parent
    seconds = [_seconds(wav) / rate if wav else hold for wav in audio]
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0"]
    command += ["-i", str(_concat_list(work / "frames.txt", frames, seconds))]
    if any(audio):
        tracks = [
            wav or _silence(work / f"silence-{n}.wav", hold * rate, audio)
            for n, wav in enumerate(audio)
        ]
        command += ["-f", "concat", "-safe", "0"]
        command += ["-i", str(_concat_list(work / "audio.txt", tracks))]
        command += ["-af", f"atempo={rate}", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-crf", "28", "-pix_fmt", "yuv420p"]
    run([*command, "-r", "25", str(out)])


def contact_sheet(frames: Sequence[Path], out: Path, *, run: Runner = _run) -> None:
    """Tile `frames` into one PNG, four across, to check the layout at a glance."""
    shots = _concat_list(frames[0].parent / "sheet.txt", frames, [1.0] * len(frames))
    rows = math.ceil(len(frames) / _SHEET_COLUMNS)
    tile = f"fps=1,scale=320:-1,tile={_SHEET_COLUMNS}x{rows}"
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(shots)]
    run([*command, "-vf", tile, "-frames:v", "1", "-update", "1", str(out)])


def _trace(lesson: Lesson, element_id: str) -> TraceElement:
    for section in lesson.sections:
        for element in section.elements:
            if isinstance(element, TraceElement) and element.trace_id == element_id:
                return element
    msg = f"the lesson has no trace '{element_id}'"
    raise ValueError(msg)


def _gate(lesson: Lesson, question_id: str) -> PredictState:
    return next(
        question
        for section in lesson.sections
        for question in section.checkpoints
        if isinstance(question, PredictState) and question.id == question_id
    )


def _only_trace(lesson: Lesson, trace: TraceElement) -> Lesson:
    """Return `lesson` cut to the trace's section, then its gate's if elsewhere.

    The page shows sections one at a time, so the trace's must come first.
    """
    home = next(s for s in lesson.sections if trace in s.elements)
    sections: tuple[Section, ...] = (home,)
    if trace.gate:
        gated = next(
            s for s in lesson.sections if any(q.id == trace.gate for q in s.checkpoints)
        )
        if gated is not home:
            sections += (gated,)
    return dataclasses.replace(lesson, probe=(), sections=sections)


def _session_url(lesson_dir: Path, server: subprocess.Popen[bytes]) -> str:
    session_file = lesson_dir / "session.json"
    deadline = time.monotonic() + _SESSION_WAIT_SECONDS
    while not session_file.exists():
        if server.poll() is not None or time.monotonic() > deadline:
            msg = "the lesson server did not start"
            raise OSError(msg)
        time.sleep(0.05)
    url: str = json.loads(session_file.read_text(encoding="utf-8"))["url"]
    return url


def _screenshots(url: str, trace: TraceElement, out_dir: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    shots: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1280, "height": 720},
            color_scheme="light",
            reduced_motion="reduce",
        )
        page.goto(f"{url}&step-{trace.trace_id}=1")
        stepper = page.locator(".stepper").filter(
            has=page.get_by_role("heading", name=trace.title, exact=True)
        )
        for number, version in enumerate(trace.versions):
            if number:
                stepper.locator(".versions").get_by_role(
                    "button", name=version.label, exact=True
                ).click()
            for index in range(len(version.steps)):
                if index:
                    stepper.get_by_role("button", name="Next step").click()
                stepper.evaluate("node => node.scrollIntoView({block: 'start'})")
                shot = out_dir / f"frame-{len(shots) + 1:04d}.png"
                page.screenshot(path=shot, animations="disabled")
                shots.append(shot)
        browser.close()
    return shots


def _concat_list(
    path: Path, files: Sequence[Path], seconds: Sequence[float] | None = None
) -> Path:
    """Write an ffmpeg concat list; the last file repeats so its duration holds."""
    lines = []
    for at, file in enumerate(files):
        lines.append(f"file '{_quoted(file)}'")
        if seconds is not None:
            lines.append(f"duration {seconds[at]:.3f}")
    if seconds is not None:
        lines.append(f"file '{_quoted(files[-1])}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _quoted(file: Path) -> str:
    return str(file).replace("'", "'\\''")


def _seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def _joined(parts: Sequence[Path], out: Path) -> Path:
    with wave.open(str(out), "wb") as joined:
        for number, part in enumerate(parts):
            with wave.open(str(part), "rb") as wav:
                if number == 0:
                    joined.setparams(wav.getparams())
                joined.writeframes(wav.readframes(wav.getnframes()))
    return out


def _silence(out: Path, seconds: float, audio: Sequence[Path | None]) -> Path:
    """Write `seconds` of silence in the format of the first narrated step."""
    sample = next(wav for wav in audio if wav)
    with wave.open(str(sample), "rb") as wav:
        params = wav.getparams()
    with wave.open(str(out), "wb") as silent:
        silent.setparams(params)
        frame_count = int(seconds * params.framerate)
        silent.writeframes(b"\0" * frame_count * params.sampwidth * params.nchannels)
    return out
