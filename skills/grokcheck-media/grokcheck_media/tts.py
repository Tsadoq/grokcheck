"""Speak one narration sentence into a WAV file, locally when possible."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import urllib.request
import wave
from pathlib import Path

_KOKORO_DIR = Path.home() / ".cache" / "grokcheck" / "kokoro"
_KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
_ELEVENLABS_VOICE = "21m00Tcm4TlvDq8ikWAM"
_ELEVENLABS_RATE = 22050
_CLOUD_SECONDS = 60.0
_SPOKEN = (
    (re.compile(r"__(\w+?)__"), r"dunder \1"),
    (re.compile(r"\(\)"), ""),
    (re.compile(r"(?<=\w)\.(?=\w)"), " dot "),
    (re.compile(r"(?<=\w)_(?=\w)"), " "),
)


def synthesise(sentence: str, out: Path, *, allow_cloud: bool = False) -> Path | None:
    """Write `sentence` spoken to the WAV file `out` and return it.

    Uses Kokoro when its model files are in `~/.cache/grokcheck/kokoro/`, else
    ElevenLabs when `allow_cloud` and `ELEVENLABS_API_KEY` are both set.
    Returns None when neither is available.
    """
    text = spoken(sentence)
    if importlib.util.find_spec("kokoro_onnx") and all(
        (_KOKORO_DIR / name).is_file() for name in _KOKORO_FILES
    ):
        _kokoro(text, out)
        return out
    key = os.environ.get("ELEVENLABS_API_KEY")
    if allow_cloud and key:
        _elevenlabs(text, out, key)
        return out
    return None


def spoken(sentence: str) -> str:
    """Return `sentence` with code identifiers spelled as a narrator says them."""
    for pattern, replacement in _SPOKEN:
        sentence = pattern.sub(replacement, sentence)
    return sentence


def _kokoro(text: str, out: Path) -> None:
    kokoro = importlib.import_module("kokoro_onnx").Kokoro(
        *(str(_KOKORO_DIR / name) for name in _KOKORO_FILES)
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
