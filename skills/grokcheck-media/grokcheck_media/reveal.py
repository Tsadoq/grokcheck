"""A lesson as a reveal.js deck: one slide per section, checkpoints as speaker notes.

The deck loads reveal.js from `../vendor/reveal.js`, relative to its own file,
so it opens from disk with no network.
"""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING, Any

from grokcheck.lesson import CodeElement, ProseElement

if TYPE_CHECKING:
    from collections.abc import Mapping

    from grokcheck.lesson import Lesson, Section

_VENDOR = "../vendor/reveal.js/dist"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def render_deck(lesson: Lesson, results: Mapping[str, Any] | None = None) -> str:
    """Return the deck as one HTML page.

    `results` is a run's `results.json`; when given, each checkpoint in the
    notes carries the reader's outcome.
    """
    outcomes = {
        item["question_id"]: item["outcome"]
        for item in (results or {}).get("checkpoints", [])
    }
    slides = "\n".join(_slide(section, outcomes) for section in lesson.sections)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(lesson.title)}</title>
<link rel="stylesheet" href="{_VENDOR}/reveal.css">
<link rel="stylesheet" href="{_VENDOR}/theme/white.css">
<link rel="stylesheet" href="{_VENDOR}/theme/black.css"
 media="(prefers-color-scheme: dark)">
</head>
<body>
<div class="reveal"><div class="slides">
{slides}
</div></div>
<script src="{_VENDOR}/reveal.js"></script>
<script>Reveal.initialize({{ hash: true }});</script>
</body>
</html>
"""


def _slide(section: Section, outcomes: Mapping[str, str]) -> str:
    prose = next(
        (e.markdown for e in section.elements if isinstance(e, ProseElement)),
        section.body,
    )
    parts = [f"<h2>{escape(_first_sentence(prose))}</h2>"]
    code = next((e.code for e in section.elements if isinstance(e, CodeElement)), None)
    if code:
        parts.append(
            f'<pre><code class="language-{escape(code.language)}" data-line-numbers>'
            f"{escape(code.text)}</code></pre>"
        )
    claims = [claim.text for element in section.elements for claim in element.claims]
    if claims:
        parts.append(
            f"<footer><small>{'<br>'.join(map(escape, claims))}</small></footer>"
        )
    notes = "".join(
        f"<li>{escape(q.prompt)}{_outcome(outcomes.get(q.id))}</li>"
        for q in section.checkpoints
    )
    parts.append(
        f'<aside class="notes"><h3>{escape(section.title)}</h3><ul>{notes}</ul></aside>'
    )
    body = "\n".join(parts)
    return f'<section id="{escape(section.id)}">\n{body}\n</section>'


def _first_sentence(text: str) -> str:
    return _SENTENCE_END.split(text.strip(), maxsplit=1)[0]


def _outcome(outcome: str | None) -> str:
    return f" ({escape(outcome)})" if outcome else ""
