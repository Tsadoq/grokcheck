"""The agent's side of the HTTP API: how the CLI talks to a lesson's server.

A `LessonClient` finds the server through the lesson's `session.json` and sends
the session token with every request. It never goes through a proxy, because
the server only ever listens on this machine.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN_HEADER = "X-Grokcheck-Token"  # noqa: S105
_REQUEST_SECONDS = 10.0
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServerGoneError(Exception):
    """No server answers for the lesson: never started, stopped, or timed out."""


class ApiError(Exception):
    """The server refused a request; the message is its `error` field."""


class LessonClient:
    """Calls the API of the server hosting the run in `lesson_dir`."""

    def __init__(self, lesson_dir: Path) -> None:
        """Read the server's port and token; raise `ServerGoneError` if unpublished."""
        try:
            session = json.loads(
                (lesson_dir / "session.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError as error:
            msg = "the lesson has no server"
            raise ServerGoneError(msg) from error
        self._base = f"http://127.0.0.1:{session['port']}"
        self._token = str(session["token"])

    def events_after(self, seq: int, *, timeout: float) -> list[dict[str, Any]]:
        """Return the `question` and `submitted` events after `seq`, oldest first.

        The server holds the request up to `timeout` seconds; an empty list
        means none arrived in that time.
        """
        query = urlencode({"after": seq, "timeout": timeout})
        response = self._send(
            "GET", f"/api/events?{query}", None, timeout + _REQUEST_SECONDS
        )
        events: list[dict[str, Any]] = response["events"]
        return events

    def reply(self, question_id: str, markdown: str) -> dict[str, Any]:
        """Post the agent's reply to reader question `question_id`; return the reply."""
        return self._send(
            "POST",
            "/api/reply",
            {"question_id": question_id, "markdown": markdown},
            _REQUEST_SECONDS,
        )

    def stop(self) -> None:
        """Ask the server to shut down; it exits right after answering."""
        self._send("POST", "/api/stop", None, _REQUEST_SECONDS)

    def _send(
        self, method: str, path: str, body: object, timeout: float
    ) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310
            f"{self._base}{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={_TOKEN_HEADER: self._token, "Content-Type": "application/json"},
        )
        try:
            with _NO_PROXY.open(request, timeout=timeout) as response:
                payload: dict[str, Any] = json.loads(response.read())
                return payload
        except urllib.error.HTTPError as error:
            raise ApiError(_error_message(error)) from error
        except OSError as error:
            msg = f"the lesson server is not running ({error})"
            raise ServerGoneError(msg) from error


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(error.read())["error"]
    except (ValueError, KeyError, TypeError):
        return f"HTTP {error.code}"
    return f"HTTP {error.code}: {message}"
