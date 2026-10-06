"""Speak one narration sentence into a WAV file, locally when possible."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import urllib.request
import wave
from typing import TYPE_CHECKING

from grokcheck_media import env

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_ELEVENLABS_VOICE = "21m00Tcm4TlvDq8ikWAM"
_ELEVENLABS_RATE = 22050
_CLOUD_SECONDS = 60.0
_DIGITS = ("oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_TEENS = (
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
_TENS = ("", "", "twenty", "thirty", "forty", "fifty")
_REASONS = (
    "ok|created|accepted|no content|moved permanently|found|not modified|"
    "bad request|unauthorized|forbidden|not found|method not allowed|conflict|gone|"
    "unprocessable|unprocessable content|too many requests|internal server error|"
    "bad gateway|service unavailable|gateway timeout"
)
_STATUS = re.compile(rf"\b([2-5]\d\d)\b(?=,?\s+(?:{_REASONS})\b)", re.IGNORECASE)


def _status(match: re.Match[str]) -> str:
    head, tens, ones = (int(digit) for digit in match.group(1))
    if tens == ones == 0:
        tail = "hundred"
    elif tens == 0:
        tail = f"oh {_DIGITS[ones]}"
    elif tens == 1:
        tail = _TEENS[ones]
    else:
        tail = f"{_TENS[tens]} {_DIGITS[ones]}" if ones else _TENS[tens]
    return f"{_DIGITS[head]} {tail}"


PRONUNCIATIONS = (
    (re.compile(r"\bgrokcheck\b", re.IGNORECASE), "grok check"),
    (re.compile(r"\bkeepalive\b", re.IGNORECASE), "keep-alive"),
    (re.compile(r"\bsubagent(s?)\b", re.IGNORECASE), r"sub-agent\1"),
    (re.compile(r"\bLast-Event-ID\b", re.IGNORECASE), "last event I D"),
    (re.compile(r"\bID\b"), "I D"),
    (re.compile(r"\bSSE\b"), "S S E"),
    (re.compile(r"\bJSON\b"), "jason"),
    (re.compile(r"\bAPI(s?)\b"), r"A P I\1"),
)
_SPOKEN: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    *PRONUNCIATIONS,
    (_STATUS, _status),
    (re.compile(r"__(\w+?)__"), r"dunder \1"),
    (re.compile(r"\(\)"), ""),
    (re.compile(r"(?<=\w)\.(?=\w)"), " dot "),
    (re.compile(r"(?<=\w)_(?=\w)"), " "),
)


def synthesise(sentence: str, out: Path, *, allow_cloud: bool = False) -> Path | None:
    """Write `sentence` spoken to the WAV file `out` and return it.

    Uses Kokoro when its model files are in `env.kokoro_dir()`, else
    ElevenLabs when `allow_cloud` and `ELEVENLABS_API_KEY` are both set.
    Returns None when neither is available.
    """
    text = spoken(sentence)
    if importlib.util.find_spec("kokoro_onnx") and all(
        (env.kokoro_dir() / name).is_file() for name in env.KOKORO_FILES
    ):
        _kokoro(text, out)
        return out
    key = os.environ.get("ELEVENLABS_API_KEY")
    if allow_cloud and key:
        _elevenlabs(text, out, key)
        return out
    return None


def spoken(sentence: str) -> str:
    """Return `sentence` as the narrator says it.

    Product names and acronyms follow `PRONUNCIATIONS`, an HTTP status before
    its reason is read like "four oh nine conflict", and code identifiers lose
    their dots, underscores and dunders.
    """
    for pattern, replacement in _SPOKEN:
        sentence = pattern.sub(replacement, sentence)
    return sentence


def _kokoro(text: str, out: Path) -> None:
    module = importlib.import_module("kokoro_onnx")
    data = importlib.import_module("espeakng_loader").get_data_path()
    if len(data) > env.ESPEAK_MAX_PATH:
        data = str(env.espeak_copy())
    kokoro = module.Kokoro(
        *(str(env.kokoro_dir() / name) for name in env.KOKORO_FILES),
        espeak_config=module.EspeakConfig(data_path=data),
    )
    samples, rate = kokoro.create(text, voice="af_heart", speed=0.95, lang="en-us")
    pcm = (samples.clip(-1, 1) * 32767).astype("<i2").tobytes()
    _write_wav(out, pcm, rate)


def _elevenlabs(text: str, out: Path, key: str) -> None:
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{_ELEVENLABS_VOICE}"
        f"?output_format=pcm_{_ELEVENLABS_RATE}",
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2"}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_CLOUD_SECONDS) as response:  # noqa: S310
        _write_wav(out, response.read(), _ELEVENLABS_RATE)


def _write_wav(out: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
