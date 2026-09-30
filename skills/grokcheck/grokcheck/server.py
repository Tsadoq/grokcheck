"""The localhost HTTP server that hosts one `LessonRun` for the browser and the agent.

Transport only: a guard rejects every request that does not come from this
machine's own page, then a route table maps HTTP onto `LessonRun` methods. The
bundled web app is served from a fixed allowlist of files; nothing else on
disk is reachable.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar, get_args
from urllib.parse import parse_qs, urlsplit

from grokcheck.grading import Confidence, ResponseError
from grokcheck.run import RunError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from grokcheck.run import LessonRun

IDLE_SHUTDOWN_SECONDS = 120 * 60
AFTER_SUBMIT_SHUTDOWN_SECONDS = 10 * 60
MAX_BODY_BYTES = 256 * 1024
MAX_POLL_SECONDS = 30 * 60

_LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_TOKEN_HEADER = "X-Grokcheck-Token"  # noqa: S105
_WATCHDOG_INTERVAL_SECONDS = 15.0
_WEB_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/api.js": ("api.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/questions.js": ("questions.js", "text/javascript; charset=utf-8"),
    "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
    "/codeview.js": ("codeview.js", "text/javascript; charset=utf-8"),
    "/vendor/highlight/highlight.min.js": (
        "vendor/highlight/highlight.min.js",
        "text/javascript; charset=utf-8",
    ),
    "/vendor/highlight/github.min.css": (
        "vendor/highlight/github.min.css",
        "text/css; charset=utf-8",
    ),
    "/vendor/highlight/github-dark.min.css": (
        "vendor/highlight/github-dark.min.css",
        "text/css; charset=utf-8",
    ),
}


class _RequestError(Exception):
    """A request that ends with `status` and a JSON `{"error": message}` body."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        self.status = status
        super().__init__(message)


class _Lifetime:
    """Decides when the server has outlived its use.

    It expires after `IDLE_SHUTDOWN_SECONDS` without a request, or
    `AFTER_SUBMIT_SHUTDOWN_SECONDS` after submit whatever the traffic, because
    the browser keeps long-polling for replies after the quiz is over.
    """

    def __init__(self, *, submitted: bool) -> None:
        self._lock = threading.Lock()
        self._last_request = time.monotonic()
        self._submitted_at = self._last_request if submitted else None

    def touch(self) -> None:
        with self._lock:
            self._last_request = time.monotonic()

    def mark_submitted(self) -> None:
        with self._lock:
            self._submitted_at = time.monotonic()

    def expired(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._submitted_at is not None:
                return now - self._submitted_at > AFTER_SUBMIT_SHUTDOWN_SECONDS
            return now - self._last_request > IDLE_SHUTDOWN_SECONDS


class _LessonServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, run: LessonRun, web_dir: Path) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.run = run
        self.web_dir = web_dir
        self.token = ""
        self.lifetime = _Lifetime(submitted=run.submitted)
        self.log_lock = threading.Lock()

    def stop_soon(self) -> None:
        """Stop `serve_forever` from a thread other than the one running it."""
        threading.Thread(target=self.shutdown, daemon=True).start()


def serve_forever(run: LessonRun, web_dir: Path, port: int = 0) -> None:
    """Serve `run` and the web app in `web_dir` on `127.0.0.1:port` until stopped.

    Port 0 lets the OS choose. Once bound, the port and a fresh token are
    published to the run's `session.json`. Returns after `POST /api/stop`,
    after `IDLE_SHUTDOWN_SECONDS` with no request, or
    `AFTER_SUBMIT_SHUTDOWN_SECONDS` after the quiz is submitted.
    """
    with _LessonServer(port, run, web_dir) as server:
        server.token = run.publish_session(server.server_address[1]).token
        finished = threading.Event()
        watchdog = threading.Thread(
            target=_shut_down_when_expired, args=(server, finished), daemon=True
        )
        watchdog.start()
        try:
            server.serve_forever()
        finally:
            finished.set()


def _shut_down_when_expired(server: _LessonServer, finished: threading.Event) -> None:
    while not finished.wait(_WATCHDOG_INTERVAL_SECONDS):
        if server.lifetime.expired():
            server.shutdown()
            return


class _Handler(BaseHTTPRequestHandler):
    server: _LessonServer
    server_version = "grokcheck"
    sys_version = ""

    _GET_ROUTES: ClassVar[dict[str, Callable[[_Handler], object]]]
    _POST_ROUTES: ClassVar[dict[str, Callable[[_Handler, dict[str, Any]], object]]]

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401
        line = (
            f"{datetime.now(UTC).isoformat()} {self.address_string()} {format % args}"
        )
        with (
            self.server.log_lock,
            (self.server.run.lesson_dir / "server.log").open(
                "a", encoding="utf-8"
            ) as log,
        ):
            log.write(line + "\n")

    def _handle(self, method: str) -> None:
        self._body_read = False
        self._stop_requested = False
        self.server.lifetime.touch()
        path = urlsplit(self.path).path
        try:
            self._guard(path)
            if path.startswith("/api/"):
                self._send_json(HTTPStatus.OK, self._route(method, path))
            else:
                self._send_web_file(method, path)
        except _RequestError as rejected:
            self._fail(rejected.status, str(rejected))
        except RunError as error:
            self._fail(HTTPStatus.CONFLICT, str(error))
        except ResponseError as error:
            self._fail(HTTPStatus.BAD_REQUEST, str(error))
        if self._stop_requested:
            self.server.stop_soon()
        self.server.lifetime.touch()

    def _guard(self, path: str) -> None:
        """Reject, before any routing, requests a foreign web page could forge.

        The Host check defeats DNS rebinding; its port is ignored so an SSH
        tunnel on another local port still works. The token, which only the
        page opened from the session URL knows, gates the API.
        """
        host = self.headers.get("Host", "")
        if _hostname(host) not in _LOCAL_HOSTNAMES:
            raise _RequestError(HTTPStatus.FORBIDDEN, "foreign Host")
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            raise _RequestError(HTTPStatus.FORBIDDEN, "foreign Origin")
        if path.startswith("/api/") and not self._authorised():
            raise _RequestError(HTTPStatus.FORBIDDEN, "missing or wrong token")

    def _authorised(self) -> bool:
        sent = self.headers.get(_TOKEN_HEADER, "")
        return bool(self.server.token) and hmac.compare_digest(
            sent.encode(), self.server.token.encode()
        )

    def _route(self, method: str, path: str) -> object:
        if method == "GET" and path in self._GET_ROUTES:
            return self._GET_ROUTES[path](self)
        if method == "POST" and path in self._POST_ROUTES:
            return self._POST_ROUTES[path](self, self._json_body())
        if path in self._GET_ROUTES or path in self._POST_ROUTES:
            raise _RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
        raise _RequestError(HTTPStatus.NOT_FOUND, "no such endpoint")

    def _json_body(self) -> dict[str, Any]:
        """Read the POST body: a JSON object, or `{}` when the body is empty."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise _RequestError(HTTPStatus.BAD_REQUEST, "bad Content-Length") from error
        if length < 0:
            raise _RequestError(HTTPStatus.BAD_REQUEST, "bad Content-Length")
        if length > MAX_BODY_BYTES:
            raise _RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
        self._body_read = True
        raw = self.rfile.read(length)
        if length == 0:
            return {}
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type.lower() != "application/json":
            raise _RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "body must be application/json"
            )
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _RequestError(HTTPStatus.BAD_REQUEST, "body is not JSON") from error
        if not isinstance(body, dict):
            raise _RequestError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
        return body

    def _query(self) -> tuple[int, float]:
        """Parse the `after` cursor and the `timeout` hold of a long-poll."""
        query = parse_qs(urlsplit(self.path).query)
        try:
            after = int(query.get("after", ["0"])[0])
            timeout = float(query.get("timeout", ["0"])[0])
        except ValueError as error:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "bad after or timeout"
            ) from error
        if not 0 <= timeout < float("inf"):
            raise _RequestError(HTTPStatus.BAD_REQUEST, "bad after or timeout")
        return after, min(timeout, MAX_POLL_SECONDS)

    def _get_lesson(self) -> object:
        return self.server.run.lesson.public_view()

    def _get_replies(self) -> object:
        after, timeout = self._query()
        replies = self.server.run.replies_after(after, timeout=timeout)
        return {"replies": [dataclasses.asdict(reply) for reply in replies]}

    def _get_events(self) -> object:
        after, timeout = self._query()
        events = self.server.run.events_after(after, timeout=timeout)
        return {"events": [event.as_json() for event in events]}

    def _post_answer(self, body: dict[str, Any]) -> object:
        confidence = body.get("confidence")
        if confidence is not None and confidence not in get_args(Confidence):
            msg = f"'confidence' must be one of {', '.join(get_args(Confidence))}"
            raise _RequestError(HTTPStatus.BAD_REQUEST, msg)
        feedback = self.server.run.answer(
            _required_str(body, "question_id"), _required(body, "response"), confidence
        )
        return dataclasses.asdict(feedback)

    def _post_question(self, body: dict[str, Any]) -> object:
        thread = self.server.run.ask(
            _required_str(body, "section_id"),
            _optional_str(body, "selection"),
            _required_str(body, "text"),
        )
        return dataclasses.asdict(thread)

    def _post_reply(self, body: dict[str, Any]) -> object:
        reply = self.server.run.reply(
            _required_str(body, "question_id"), _required_str(body, "markdown")
        )
        return dataclasses.asdict(reply)

    def _post_submit(self, _body: dict[str, Any]) -> object:
        """Submit, then answer with the full `results.json` for the results screen."""
        self.server.run.submit()
        self.server.lifetime.mark_submitted()
        results = (self.server.run.lesson_dir / "results.json").read_text(
            encoding="utf-8"
        )
        return json.loads(results)

    def _post_stop(self, _body: dict[str, Any]) -> object:
        self._stop_requested = True
        return {"stopping": True}

    def _send_web_file(self, method: str, path: str) -> None:
        if method != "GET":
            raise _RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
        entry = _WEB_FILES.get(path)
        if entry is None:
            raise _RequestError(HTTPStatus.NOT_FOUND, "not found")
        name, content_type = entry
        try:
            payload = (self.server.web_dir / name).read_bytes()
        except OSError as error:
            raise _RequestError(HTTPStatus.NOT_FOUND, "not found") from error
        self._send(HTTPStatus.OK, content_type, payload)

    def _fail(self, status: HTTPStatus, message: str) -> None:
        """Send an error, first draining an unread POST body.

        Closing a socket with unread input makes the kernel reset the
        connection, and the client would see that instead of the error.
        """
        if self.command == "POST" and not self._body_read:
            length = self.headers.get("Content-Length", "")
            if length.isdigit() and int(length) <= MAX_BODY_BYTES:
                self.rfile.read(int(length))
        self._send_json(status, {"error": message})

    def _send_json(self, status: HTTPStatus, value: object) -> None:
        payload = json.dumps(value).encode()
        self._send(status, "application/json", payload)

    def _send(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)


_Handler._GET_ROUTES = {  # noqa: SLF001
    "/api/lesson": _Handler._get_lesson,  # noqa: SLF001
    "/api/replies": _Handler._get_replies,  # noqa: SLF001
    "/api/events": _Handler._get_events,  # noqa: SLF001
}
_Handler._POST_ROUTES = {  # noqa: SLF001
    "/api/answer": _Handler._post_answer,  # noqa: SLF001
    "/api/question": _Handler._post_question,  # noqa: SLF001
    "/api/reply": _Handler._post_reply,  # noqa: SLF001
    "/api/submit": _Handler._post_submit,  # noqa: SLF001
    "/api/stop": _Handler._post_stop,  # noqa: SLF001
}


def _hostname(host: str) -> str | None:
    """Return the hostname of a Host header value, without port or IPv6 brackets."""
    try:
        return urlsplit(f"//{host}").hostname
    except ValueError:
        return None


def _required_str(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        msg = f"'{key}' must be a string"
        raise _RequestError(HTTPStatus.BAD_REQUEST, msg)
    return value


def _required(body: Mapping[str, Any], key: str) -> Any:  # noqa: ANN401
    if key not in body:
        msg = f"'{key}' is required"
        raise _RequestError(HTTPStatus.BAD_REQUEST, msg)
    return body[key]


def _optional_str(body: Mapping[str, Any], key: str) -> str:
    """Return `body[key]` when it is a string, `""` when it is absent or null."""
    if body.get(key) is None:
        return ""
    return _required_str(body, key)
