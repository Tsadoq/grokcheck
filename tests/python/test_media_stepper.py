"""The MP4 a reader shares: one stepper frame per step, held for its narration."""

import wave
from pathlib import Path

import pytest
from grokcheck_media.stepper_video import assemble


def _silent_wav(path: Path, seconds: float) -> Path:
    rate = 8000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * int(seconds * rate))
    return path


def test_assemble_builds_concat_list_with_narration_durations(tmp_path: Path) -> None:
    """Narrated frames last as long as their audio; a silent one lasts `hold`."""
    frames = [tmp_path / f"frame-{n}.png" for n in range(3)]
    for frame in frames:
        frame.write_bytes(b"png")
    audio = [
        _silent_wav(tmp_path / "a.wav", 1.5),
        None,
        _silent_wav(tmp_path / "c.wav", 3.0),
    ]
    captured: list[str] = []

    assemble(frames, audio, tmp_path / "out.mp4", hold=2.5, run=captured.extend)

    expected = (
        f"file '{frames[0]}'\nduration 1.500\n"
        f"file '{frames[1]}'\nduration 2.500\n"
        f"file '{frames[2]}'\nduration 3.000\n"
        f"file '{frames[2]}'\n"
    )
    concat = (tmp_path / "frames.txt").read_text(encoding="utf-8")
    if concat != expected:
        pytest.fail(f"concat list was\n{concat}\nexpected\n{expected}")
    if " -c:v libx264 " not in " ".join(captured):
        pytest.fail(f"no H.264 encoding in {captured}")
