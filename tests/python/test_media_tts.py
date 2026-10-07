"""How narration is respelled for the speech engine and for the transcript check."""

import pytest
from grokcheck_media.checks import diff
from grokcheck_media.tts import spoken


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("Run grokcheck on it.", "Run grok check on it."),
        ("The keepalive frame.", "The keep-alive frame."),
        ("Each subagent reports.", "Each sub-agent reports."),
        ("It sends a Last-Event-ID header.", "It sends a last event I D header."),
        (
            "SSE frames carry JSON from the API.",
            "S S E frames carry jason from the A P I.",
        ),
        ("It answers with a 409 conflict.", "It answers with a four oh nine conflict."),
        ("It gets a 204, no content.", "It gets a two oh four, no content."),
        (
            "A 422 Unprocessable and a 500 internal server error.",
            "A four twenty two Unprocessable and a five hundred internal server error.",
        ),
        ("It waited 409 seconds.", "It waited 409 seconds."),
        (
            "Call __init__ and wake_up on self.events().",
            "Call dunder init and wake up on self dot events.",
        ),
    ],
)
def test_spoken_respells_names_acronyms_statuses_and_identifiers(
    written: str, said: str
) -> None:
    """The pronunciation table runs before the identifier rules."""
    got = spoken(written)
    if got != said:
        pytest.fail(f"{written!r} became {got!r}")


def test_transcript_check_accepts_the_spoken_forms() -> None:
    """A transcript in either form matches the script; JSON may be heard as Jason."""
    if diff(["It returns JSON."], ["It returns Jason."]):
        pytest.fail("JSON heard as Jason was reported")
    script = ["grokcheck sends a 409 conflict with the Last-Event-ID over SSE."]
    heard = [
        "Grok check sends a four oh nine conflict with the last event ID over SSE.",
        "grokcheck sends a 409 Conflict with the Last Event ID over S S E.",
    ]
    for transcript in heard:
        found = diff(script, [transcript])
        if found:
            pytest.fail(f"{transcript!r} reported {found}")
