"""Checks on a rendered concept video chapter: layout, audio and truth."""

from __future__ import annotations

import difflib
import functools
import importlib
import math
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grokcheck.ground import manifest_entry
from grokcheck.lesson import BACKING_CLASSES, Claim

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SHEET_SECONDS = 3.0
_SHEET_COLUMNS = 6
_WHISPER_MODEL = "small.en"
_UK_SPELLINGS = {
    "canceled": "cancelled",
    "canceling": "cancelling",
    "color": "colour",
    "colors": "colours",
    "behavior": "behaviour",
    "behaviors": "behaviours",
    "center": "centre",
    "gray": "grey",
    "labeled": "labelled",
    "modeling": "modelling",
    "traveled": "travelled",
    "catalog": "catalogue",
    "analyze": "analyse",
    "analyzed": "analysed",
}
_IZE = re.compile(r"(?<=\w{2})iz(e|es|ed|ing|ation|ations)$")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_SCALES = ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand"))
_run = functools.partial(subprocess.run, check=True, capture_output=True)


@dataclass(frozen=True)
class Difference:
    """Sentence `index` was heard as `heard`; `changes` pairs expected, heard words."""

    index: int
    expected: str
    heard: str
    changes: list[tuple[str, str]]


def diff(script: Sequence[str], heard: Sequence[str]) -> list[Difference]:
    """Compare each script sentence with its transcript, ignoring spelling noise.

    Case, punctuation, US against UK spelling and digits against spoken numbers
    are not differences. A sentence missing from `heard` is heard as "".
    """
    differences = []
    for index, expected in enumerate(script):
        said = heard[index] if index < len(heard) else ""
        want, got = _words(expected), _words(said)
        matcher = difflib.SequenceMatcher(None, want, got, autojunk=False)
        changes = [
            (" ".join(want[a:b]), " ".join(got[c:d]))
            for op, a, b, c, d in matcher.get_opcodes()
            if op != "equal"
        ]
        if changes:
            differences.append(Difference(index, expected, said, changes))
    return differences


def transcribe_back(script: Sequence[str], audio: Sequence[Path]) -> list[Difference]:
    """Transcribe each sentence's WAV with faster-whisper and `diff` it."""
    whisper = importlib.import_module("faster_whisper")
    model = whisper.WhisperModel(_WHISPER_MODEL, device="cpu", compute_type="int8")
    heard = []
    for wav in audio:
        segments, _ = model.transcribe(str(wav), beam_size=5)
        heard.append(" ".join(segment.text for segment in segments))
    return diff(script, heard)


def contact_sheet(video: Path, seconds: float, out: Path) -> Path:
    """Tile one frame every three seconds of `video` into the PNG `out`."""
    frames = max(1, math.ceil(seconds / _SHEET_SECONDS))
    rows = math.ceil(frames / _SHEET_COLUMNS)
    tile = f"fps=1/{_SHEET_SECONDS},scale=320:-1,tile={_SHEET_COLUMNS}x{rows}"
    options = ["-vf", tile, "-frames:v", "1", "-update", "1"]
    _run(["ffmpeg", "-y", "-i", str(video), *options, str(out)])
    return out


def claim_manifest(script: dict[str, Any], project_root: Path) -> list[dict[str, Any]]:
    """Return `grokcheck ground`'s manifest entries for every claim in the script.

    The same fresh-context subagent prompt then judges them.
    """
    return [
        manifest_entry(f"beats[{i}].claims[{k}]", _claim(claim), project_root)
        for i, beat in enumerate(script["beats"])
        for k, claim in enumerate(beat.get("claims", []))
    ]


def _claim(raw: dict[str, Any]) -> Claim:
    backing = dict(raw["backing"])
    kind = next(key for key in BACKING_CLASSES if key in backing)
    if "lines" in backing:
        backing["lines"] = tuple(backing["lines"])
    return Claim(raw["text"], BACKING_CLASSES[kind](**backing))


def _words(sentence: str) -> list[str]:
    text = _NUMBER.sub(lambda match: f" {_spoken(match.group())} ", sentence.lower())
    words = re.sub(r"[^a-z0-9' ]", " ", text.replace("-", " ")).replace("'", "")
    return [_uk(word) for word in words.split()]


def _uk(word: str) -> str:
    return _IZE.sub(r"is\1", _UK_SPELLINGS.get(word, word))


def _spoken(number: str) -> str:
    whole, _, fraction = number.replace(",", "").partition(".")
    words = _integer(int(whole))
    if fraction:
        words += " point " + " ".join(_ONES[int(digit)] for digit in fraction)
    return words


def _integer(value: int) -> str:
    for size, name in _SCALES:
        if value >= size:
            rest = value % size
            head = f"{_integer(value // size)} {name}"
            return f"{head} {_integer(rest)}" if rest else head
    if value >= 100:  # noqa: PLR2004
        rest = value % 100
        head = f"{_ONES[value // 100]} hundred"
        return f"{head} {_integer(rest)}" if rest else head
    if value >= 20:  # noqa: PLR2004
        tens, ones = divmod(value, 10)
        return f"{_TENS[tens]} {_ONES[ones]}" if ones else _TENS[tens]
    return _ONES[value]
