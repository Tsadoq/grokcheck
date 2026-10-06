"""Record a real run of a script as replayable task and line events.

Task events (`created`, `resume`, `suspend`, `done`, `cancelled`) come from an
asyncio task factory, a done callback and the `call` and `return` events of
each task's outermost coroutine frame. Line events, with the frame's locals,
are recorded only inside the cited files.
"""

from __future__ import annotations

import asyncio
import contextlib
import dis
import inspect
import reprlib
import runpy
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from types import FrameType

DEFAULT_MAX_EVENTS = 5000

_YIELD_VALUE = dis.opmap["YIELD_VALUE"]
_SCALARS = (bool, int, float, type(None))


@dataclass
class Event:
    """One recorded event; `seq` orders events across all tasks.

    Line events carry no `task`; `Trace.steps` nests them under the task event
    before them.
    """

    seq: int
    event: str
    task: str | None = None
    file: str | None = None
    line: int | None = None
    locals: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Step:
    """One stepper step: a task event with the line events that followed it."""

    event: Event
    lines: list[Event] = field(default_factory=list)


@dataclass
class Trace:
    """The events of one run; `truncated` means it hit `max_events`."""

    events: list[Event]
    truncated: bool = False

    @property
    def steps(self) -> list[Step]:
        """Group line events under the preceding task event.

        A line event with no task event before it is a step of its own.
        """
        steps: list[Step] = []
        for event in self.events:
            if event.event == "line" and steps and steps[-1].event.event != "line":
                steps[-1].lines.append(event)
            else:
                steps.append(Step(event))
        return steps

    def to_json(self) -> dict[str, Any]:
        """Return the trace as JSON-safe data."""
        return {
            "events": [asdict(event) for event in self.events],
            "truncated": self.truncated,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Trace:
        """Rebuild a trace written by `to_json`."""
        events = [Event(**event) for event in data["events"]]
        return cls(events, truncated=data["truncated"])


def record(
    script: Path,
    cited: set[Path],
    entry: str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> Trace:
    """Run `script` under the recorder and return what it did.

    Without `entry` the script runs as `__main__`; with it, the script is
    loaded and `entry()` is called, and run with `asyncio.run` if it returns a
    coroutine. A line event's `file` is the cited path as the caller spelled it.
    `sys.exit` ends the run normally; other exceptions from the script
    propagate after the recorder is removed.
    """
    recorder = _Recorder(cited, max_events)
    previous_policy = asyncio.get_event_loop_policy()
    previous_trace = sys.gettrace()
    asyncio.set_event_loop_policy(_RecordingPolicy(recorder))
    sys.settrace(recorder.trace)
    try:
        with contextlib.suppress(SystemExit):
            _run(script, entry)
    finally:
        sys.settrace(previous_trace)
        asyncio.set_event_loop_policy(previous_policy)
    return Trace(recorder.events, truncated=recorder.truncated)


def _run(script: Path, entry: str | None) -> None:
    if entry is None:
        runpy.run_path(str(script), run_name="__main__")
        return
    result = runpy.run_path(str(script))[entry]()
    if inspect.iscoroutine(result):
        asyncio.run(result)


class _Recorder:
    def __init__(self, cited: set[Path], max_events: int) -> None:
        self.events: list[Event] = []
        self.truncated = False
        self._max_events = max_events
        self._cited = {path.resolve(): str(path) for path in cited}
        self._files: dict[str, str | None] = {}
        self._names: dict[asyncio.Task[Any], str] = {}
        self._unnamed: dict[asyncio.Task[Any], list[Event]] = {}

    def trace(self, frame: FrameType, _event: str, _arg: object) -> Any:  # noqa: ANN401
        """Global tracer: follow cited frames and tasks' outermost coroutine frames."""
        try:
            file = self._cited_file(frame)
            task = self._task_owning(frame)
            if task is not None:
                self._emit("resume", task=self._name(task), frame=frame)
            if file is None and task is None:
                return None
        except Exception as error:  # noqa: BLE001
            self._error(error)
            return None
        return self._local

    def _local(self, frame: FrameType, event: str, _arg: object) -> Any:  # noqa: ANN401
        try:
            if event == "line":
                file = self._cited_file(frame)
                if file is not None:
                    self._emit("line", frame=frame, frame_locals=_safe_locals(frame))
            elif event == "return" and _is_suspending(frame):
                task = self._task_owning(frame)
                if task is not None:
                    self._emit("suspend", task=self._name(task), frame=frame)
        except Exception as error:  # noqa: BLE001
            self._error(error)
        return self._local

    def task_factory(
        self,
        loop: asyncio.AbstractEventLoop,
        coro: Coroutine[Any, Any, Any],
        **kwargs: Any,  # noqa: ANN401
    ) -> asyncio.Task[Any]:
        """Create the task, record `created` and watch for its end."""
        task: asyncio.Task[Any] = asyncio.Task(coro, loop=loop, **kwargs)
        try:
            created = self._emit("created")
            self._unnamed.setdefault(task, []).append(created)
            task.add_done_callback(self._on_done)
        except Exception as error:  # noqa: BLE001
            self._error(error)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        try:
            self._emit(
                "cancelled" if task.cancelled() else "done", task=self._name(task)
            )
        except Exception as error:  # noqa: BLE001
            self._error(error)

    def _name(self, task: asyncio.Task[Any]) -> str:
        """Resolve the name once, after the creator had the chance to set it."""
        name = self._names.get(task)
        if name is None:
            name = self._names[task] = task.get_name()
            for event in self._unnamed.pop(task, []):
                event.task = name
        return name

    def _cited_file(self, frame: FrameType) -> str | None:
        filename = frame.f_code.co_filename
        if filename not in self._files:
            self._files[filename] = self._cited.get(Path(filename).resolve())
        return self._files[filename]

    def _task_owning(self, frame: FrameType) -> asyncio.Task[Any] | None:
        if not frame.f_code.co_flags & inspect.CO_COROUTINE:
            return None
        task = self._current_task()
        if task is None or getattr(task.get_coro(), "cr_frame", None) is not frame:
            return None
        return task

    @staticmethod
    def _current_task() -> asyncio.Task[Any] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None

    def _emit(
        self,
        event: str,
        *,
        task: str | None = None,
        frame: FrameType | None = None,
        frame_locals: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Event:
        recorded = Event(
            seq=len(self.events),
            event=event,
            task=task,
            file=None
            if frame is None
            else self._cited_file(frame) or frame.f_code.co_filename,
            line=None if frame is None else frame.f_lineno,
            locals=frame_locals or {},
            error=error,
        )
        if len(self.events) < self._max_events:
            self.events.append(recorded)
        else:
            self.truncated = True
        return recorded

    def _error(self, error: Exception) -> None:
        self._emit("recorder_error", error=repr(error))


class _RecordingPolicy(asyncio.DefaultEventLoopPolicy):
    """Give every new event loop the recorder's task factory."""

    def __init__(self, recorder: _Recorder) -> None:
        super().__init__()
        self._recorder = recorder

    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        loop = super().new_event_loop()
        loop.set_task_factory(cast("Any", self._recorder.task_factory))
        return loop


def _is_suspending(frame: FrameType) -> bool:
    """Tell an `await` that yields from the coroutine's final return or raise.

    Python 3.13 reports the instruction after `YIELD_VALUE`, older versions
    the `YIELD_VALUE` itself.
    """
    code, last = frame.f_code.co_code, frame.f_lasti
    return code[last] == _YIELD_VALUE or (last > 0 and code[last - 2] == _YIELD_VALUE)


def _safe_locals(frame: FrameType) -> dict[str, Any]:
    return {
        name: value if isinstance(value, _SCALARS) else reprlib.repr(value)
        for name, value in frame.f_locals.items()
        if not name.startswith("__")
        and not callable(value)
        and not inspect.ismodule(value)
    }
