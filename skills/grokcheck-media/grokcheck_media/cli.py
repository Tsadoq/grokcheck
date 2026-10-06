"""grokcheck-media: render finished grokcheck lessons as decks and videos."""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(1, str(_SKILL_DIR.parent / "grokcheck"))

from grokcheck.run import LessonRun  # noqa: E402

from grokcheck_media import concept_video, doctor, stepper_video  # noqa: E402
from grokcheck_media.reveal import render_deck  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_VENDORED_REVEAL = _SKILL_DIR / "vendor" / "reveal.js"


class CliError(Exception):
    """A failure reported as `{"ok": false, "error": ...}`."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliError(message)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return the process exit status."""
    try:
        args = _parser().parse_args(argv)
        command: Callable[[argparse.Namespace], dict[str, Any]] = args.command
        output = command(args)
    except (CliError, OSError, ValueError) as error:
        _print({"ok": False, "error": str(error)})
        return 1
    _print(output)
    return 0


def _parser() -> _Parser:
    parser = _Parser(prog="grokcheck_media", description=__doc__)
    commands = parser.add_subparsers(required=True, metavar="command")
    commands.add_parser("doctor").set_defaults(command=_doctor)
    export = commands.add_parser("export").add_subparsers(
        required=True, metavar="format"
    )
    reveal = export.add_parser("reveal")
    reveal.add_argument("lesson_dir", type=Path)
    reveal.add_argument("--out", type=Path)
    reveal.set_defaults(command=_export_reveal)
    render = commands.add_parser("render").add_subparsers(
        required=True, metavar="format"
    )
    stepper = render.add_parser("stepper")
    stepper.add_argument("lesson_dir", type=Path)
    stepper.add_argument("element_id")
    stepper.add_argument("--out", type=Path)
    stepper.add_argument("--hold", type=float, default=2.5)
    stepper.add_argument("--rate", type=float, default=1.0)
    stepper.add_argument("--allow-cloud", action="store_true")
    stepper.set_defaults(command=_render_stepper)
    video = commands.add_parser("video").add_subparsers(required=True, metavar="step")
    plan = video.add_parser("plan")
    plan.add_argument("lesson_dir", type=Path)
    plan.add_argument("--minutes", type=float, default=20.0)
    plan.add_argument("--chapter-minutes", type=float, default=2.0)
    plan.set_defaults(command=_video_plan)
    chapter = video.add_parser("chapter")
    chapter.add_argument("script", type=Path)
    chapter.add_argument("--renderer", choices=("manim", "hyperframes"), required=True)
    chapter.add_argument("--out-dir", type=Path, required=True)
    chapter.add_argument("--project", type=Path, default=Path.cwd())
    chapter.add_argument("--allow-cloud", action="store_true")
    chapter.set_defaults(command=_video_chapter)
    join = video.add_parser("join")
    join.add_argument("out_dir", type=Path)
    join.add_argument("--out", type=Path, required=True)
    join.set_defaults(command=_video_join)
    return parser


def _doctor(_: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, "tools": doctor.probe()}


def _export_reveal(args: argparse.Namespace) -> dict[str, Any]:
    """Write the deck and copy reveal.js to `../vendor` beside it, where it looks."""
    lesson_dir: Path = args.lesson_dir
    out: Path = args.out or lesson_dir / "deck" / "index.html"
    if not (lesson_dir / "lesson.json").is_file():
        msg = f"{lesson_dir} holds no lesson.json"
        raise CliError(msg)
    results_path = lesson_dir / "results.json"
    results = (
        json.loads(results_path.read_text(encoding="utf-8"))
        if results_path.is_file()
        else None
    )
    deck = render_deck(LessonRun.open(lesson_dir).lesson, results)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(deck, encoding="utf-8")
    shutil.copytree(
        _VENDORED_REVEAL, out.parent.parent / "vendor" / "reveal.js", dirs_exist_ok=True
    )
    return {"ok": True, "path": str(out)}


def _render_stepper(args: argparse.Namespace) -> dict[str, Any]:
    doctor.require(["ffmpeg", "playwright"])
    lesson_dir: Path = args.lesson_dir
    out: Path = args.out or lesson_dir / "video" / f"{args.element_id}.mp4"
    video = stepper_video.render(
        lesson_dir,
        args.element_id,
        out,
        hold=args.hold,
        rate=args.rate,
        allow_cloud=args.allow_cloud,
    )
    return {"ok": True, **video}


def _video_plan(args: argparse.Namespace) -> dict[str, Any]:
    doctor.require(["ffmpeg"])
    if args.minutes <= 0 or args.chapter_minutes <= 0:
        msg = "--minutes and --chapter-minutes must be positive"
        raise CliError(msg)
    lesson = json.loads((args.lesson_dir / "lesson.json").read_text("utf-8"))
    chapters = concept_video.plan_chapters(lesson, args.minutes, args.chapter_minutes)
    return {
        "ok": True,
        "concept_id": concept_video.concept_id(lesson),
        "chapters": [dataclasses.asdict(chapter) for chapter in chapters],
    }


def _video_chapter(args: argparse.Namespace) -> dict[str, Any]:
    doctor.require(["ffmpeg", args.renderer, "faster_whisper"])
    script = json.loads(args.script.read_text(encoding="utf-8"))
    if not isinstance(script, dict):
        msg = f"{args.script} must hold a JSON object"
        raise CliError(msg)
    chapter = concept_video.build_chapter(
        script,
        args.renderer,
        args.out_dir,
        project_root=args.project,
        allow_cloud=args.allow_cloud,
    )
    return {"ok": True, **chapter}


def _video_join(args: argparse.Namespace) -> dict[str, Any]:
    doctor.require(["ffmpeg"])
    return {"ok": True, **concept_video.join(args.out_dir, args.out)}


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value), flush=True)  # noqa: T201
