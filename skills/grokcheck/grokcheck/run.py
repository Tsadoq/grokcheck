"""One live lesson: answers, reader questions, agent replies, submit, and its files.

A `LessonRun` owns `<project>/.grokcheck/lessons/<lesson-id>/`:

- `lesson.json`: a snapshot of the lesson with code already resolved, so the
  run never depends on the studied files staying unchanged.
- `events.jsonl`: every answer, question, reply and submit, one JSON record per
  line, numbered by a sequence `seq` starting at 1. `LessonRun.open` replays it.
- `session.json`: how to reach the server that hosts the run (mode 0600).
- `results.json` and `lesson.md`: written on submit, which also updates
  `<project>/.grokcheck/schedule.json`.

Every method is safe to call from concurrent request threads.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import random
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeGuard,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from grokcheck.export import QuestionMode, QuestionThread, render_markdown
from grokcheck.grading import Confidence, ResponseError, grade, summarise
from grokcheck.lesson import (
    AssumptionsElement,
    DiffElement,
    FixTheBug,
    GatedElement,
    Lesson,
    LineAsk,
    MultipleChoice,
    OptionsElement,
    PredictOutput,
    PredictState,
    SingleChoice,
    ViewElement,
)
from grokcheck.mutate import ANSI_ESCAPE, LOG_TAIL_CHARS, MutationError
from grokcheck.mutate import run_tests as run_test_command
from grokcheck.schedule import Schedule

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from _typeshed import DataclassInstance

    from grokcheck.grading import Grade, Response, Summary
    from grokcheck.lesson import Element, Question

EventType = Literal["answer", "question", "reply", "submitted", "ask_line", "commit"]

DEFAULT_RETAKE = frozenset({"incorrect", "partial", "needs_review", "confident_wrong"})

_AGENT_EVENTS = frozenset({"question", "submitted"})


class RunError(Exception):
    """A request the run cannot honour in its current state, such as an unknown id."""


@dataclass(frozen=True)
class Event:
    """One entry of the run's log; `body` holds the fields of its `type`.

    `question`: `question_id`, `section_id`, `selection`, `text`, `mode`.
    `submitted`: `results_path` and `summary`.
    `ask_line`: `question_id`, `element_id`, `line`; a prepared line ask was read.
    `commit`: `element_id`, `text`; the reader's own options before the table.
    `answer` and `reply` are logged for replay and for the browser's replies.
    """

    seq: int
    type: EventType
    body: Mapping[str, Any]

    def as_json(self) -> dict[str, Any]:
        """Return the event as one flat JSON object, as stored in `events.jsonl`."""
        return {"seq": self.seq, "type": self.type, **self.body}


@dataclass(frozen=True)
class Reply:
    """The agent's markdown answer to a reader question; `seq` is its log position."""

    seq: int
    question_id: str
    markdown: str


@dataclass(frozen=True)
class Feedback:
    """What the reader sees after answering.

    `grade` carries the full reveal for a checkpoint or probe question and is
    `None` for a final question, whose grade is withheld until submit.
    """

    question_id: str
    grade: Grade | None


@dataclass(frozen=True)
class Session:
    """How to reach the server hosting a run; `url` carries `token` in its fragment."""

    port: int
    token: str
    pid: int
    url: str


class LessonRun:
    """The state of one lesson from `serve` to submit, mirrored to its directory."""

    def __init__(self, lesson: Lesson, lesson_dir: Path) -> None:
        """Start an empty run; use `create` or `open` rather than calling this."""
        self.lesson = lesson
        self.lesson_dir = lesson_dir
        self._changed = threading.Condition()
        self._log: list[Event] = []
        self._grades: dict[str, Grade] = {}
        self._threads: dict[str, QuestionThread] = {}
        self._summary: Summary | None = None
        self._checkpoints = {
            question.id: question
            for section in lesson.sections
            for question in section.checkpoints
        }
        self._finals = {question.id: question for question in lesson.final}
        self._probes = {question.id: question for question in lesson.probe}
        self._sections = {section.id for section in lesson.sections}
        self._gated_elements = [
            element
            for section in lesson.sections
            for element in section.elements
            if isinstance(element, GatedElement) and element.gate
        ]
        self._diffs = _diffs_by_id(lesson)
        elements = [e for section in lesson.sections for e in section.elements]
        self._options = {e.id: e for e in elements if isinstance(e, OptionsElement)}
        self._assumptions = {
            e.id: e for e in elements if isinstance(e, AssumptionsElement)
        }
        self._commits: dict[str, str] = {}
        self._ratings: dict[str, list[Confidence]] = {}
        self._elements = _element_names(lesson)

    @classmethod
    def create(cls, lesson: Lesson, project_root: Path) -> LessonRun:
        """Start a new run of `lesson` in a fresh directory under `project_root`.

        Also writes `.grokcheck/.gitignore` so lesson files are never committed.
        """
        grokcheck_dir = project_root / ".grokcheck"
        lessons_dir = grokcheck_dir / "lessons"
        lessons_dir.mkdir(parents=True, exist_ok=True)
        gitignore = grokcheck_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        lesson_dir = lessons_dir / f"{stamp}-{secrets.token_hex(3)}"
        lesson_dir.mkdir(mode=0o700)
        _write_atomic(lesson_dir / "lesson.json", json.dumps(_dump_lesson(lesson)))
        (lesson_dir / "events.jsonl").touch()
        return cls(lesson, lesson_dir)

    @classmethod
    def open(cls, lesson_dir: Path) -> LessonRun:
        """Rebuild the run in `lesson_dir` by replaying its `events.jsonl`."""
        raw = json.loads((lesson_dir / "lesson.json").read_text(encoding="utf-8"))
        run = cls(_load_lesson(raw), lesson_dir)
        lines = (lesson_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        with run._changed:
            for line in filter(str.strip, lines):
                record = json.loads(line)
                run._apply(Event(record.pop("seq"), record.pop("type"), record))
        return run

    @classmethod
    def retake_from(
        cls,
        lesson_dir: Path,
        include: Iterable[str] = DEFAULT_RETAKE,
    ) -> Lesson:
        """Build a closed-book lesson from the submitted run in `lesson_dir`.

        It holds the final questions whose outcome is in `include`, plus those
        rated `sure` but not correct when `include` names `confident_wrong`,
        with their options reshuffled. It has no explanation sections.
        """
        run: LessonRun = cls.open(lesson_dir)
        return run._retake(set(include))

    def _retake(self, wanted: set[str]) -> Lesson:
        if self._summary is None:
            msg = "the lesson has not been submitted yet"
            raise RunError(msg)
        confident_wrong = (
            set(self._summary.confident_wrong) if "confident_wrong" in wanted else set()
        )
        seed = secrets.randbits(32)
        rng = random.Random(seed)  # noqa: S311
        final = tuple(
            _reshuffled(question, rng)
            for question in self.lesson.final
            if self._grades[question.id].outcome in wanted
            or question.id in confident_wrong
        )
        if not final:
            msg = f"no final question has an outcome in {sorted(wanted)}"
            raise RunError(msg)
        return Lesson(
            title=f"Retake: {self.lesson.title}",
            scope=self.lesson.scope,
            sections=(),
            final=final,
            seed=seed,
            kinds=self.lesson.kinds,
            datasets=self.lesson.datasets,
        )

    @property
    def lesson_id(self) -> str:
        """The directory name that identifies this run to the CLI."""
        return self.lesson_dir.name

    @property
    def grades(self) -> dict[str, Grade]:
        """The latest grade of every answered question, by question id."""
        with self._changed:
            return dict(self._grades)

    @property
    def submitted(self) -> bool:
        """Whether the reader has submitted the final quiz."""
        with self._changed:
            return self._summary is not None

    def publish_session(self, port: int) -> Session:
        """Record in `session.json` that this process now serves the run on `port`.

        A new token is minted on every call.
        """
        token = secrets.token_urlsafe(32)
        session = Session(
            port=port,
            token=token,
            pid=os.getpid(),
            url=f"http://127.0.0.1:{port}/#t={token}",
        )
        _write_atomic(
            self.lesson_dir / "session.json",
            json.dumps(dataclasses.asdict(session)),
        )
        return session

    def answer(
        self,
        question_id: str,
        response: Response,
        confidence: Confidence | None = None,
    ) -> Feedback:
        """Record the reader's answer; a later answer to the same question replaces it.

        Raises `grading.ResponseError` when `response` has the wrong shape, or
        when the question is a `fix_the_bug`, which only `run_tests` answers.
        An assumptions element is answered by its id, with one confidence per
        item as `response`, and is never graded.
        """
        if question_id in self._assumptions:
            return self._rate(question_id, response)
        if isinstance(self._question(question_id), FixTheBug):
            msg = f"'{question_id}' is graded by running its tests: use /api/run"
            raise ResponseError(msg)
        return self._answer(question_id, response, confidence)

    def _rate(self, element_id: str, response: Response) -> Feedback:
        levels = get_args(Confidence)
        count = len(self._assumptions[element_id].items)
        if not (
            isinstance(response, list)
            and len(response) == count
            and all(level in levels for level in response)
        ):
            msg = f"'{element_id}' takes {count} confidence(s) from {', '.join(levels)}"
            raise ResponseError(msg)
        with self._changed:
            self._require_open()
            self._append(
                "answer",
                {"question_id": element_id, "response": response, "confidence": None},
            )
        return Feedback(element_id, None)

    def _answer(
        self,
        question_id: str,
        response: Response,
        confidence: Confidence | None,
    ) -> Feedback:
        with self._changed:
            self._require_open()
            graded = grade(self._question(question_id), response, confidence)
            self._append(
                "answer",
                {
                    "question_id": question_id,
                    "response": response,
                    "confidence": confidence,
                },
            )
        if question_id in self._finals:
            return Feedback(question_id, None)
        payload = next(
            (
                self.lesson.gated_payload(element)
                for element in self._gated_elements
                if element.gate == question_id
            ),
            None,
        )
        reveal = dataclasses.replace(graded.reveal, element_payload=payload)
        return Feedback(question_id, dataclasses.replace(graded, reveal=reveal))

    def run_tests(
        self, question_id: str, confidence: Confidence | None = None
    ) -> tuple[RunOutcome, Feedback]:
        """Run `fix_the_bug` question `question_id`'s test command in its worktree.

        The result is recorded as the reader's answer; the run's log tail is
        also added to the returned grade's reveal.
        """
        with self._changed:
            self._require_open()
        question = self._question(question_id)
        if not isinstance(question, FixTheBug):
            msg = f"'{question_id}' is not a fix_the_bug question"
            raise RunError(msg)
        outcome = _run_in_worktree(question)
        feedback = self._answer(question_id, {"passed": outcome.passed}, confidence)
        if feedback.grade is None:
            return outcome, feedback
        reveal = dataclasses.replace(feedback.grade.reveal, log=outcome.log)
        graded = dataclasses.replace(feedback.grade, reveal=reveal)
        return outcome, Feedback(question_id, graded)

    def gated(self, element: Element) -> bool:
        """Whether `element` still withholds its payload, its gate unanswered.

        A reader-first options element counts as gated until it is committed.
        """
        if isinstance(element, OptionsElement):
            return element.reader_first and element.id not in self._commits
        gate = element.gate if isinstance(element, GatedElement) else None
        return gate is not None and gate not in self._grades

    def ask(
        self, section_id: str, selection: str, text: str, mode: str = "answer"
    ) -> QuestionThread:
        """Record a reader question about `section_id` for the agent to answer.

        `selection` is the text highlighted when asking, empty for the Ask box;
        `mode` is "answer", or "socratic" to be guided by questions instead.
        """
        with self._changed:
            self._require_open()
            if section_id not in self._sections:
                msg = f"unknown section '{section_id}'"
                raise RunError(msg)
            if mode not in get_args(QuestionMode):
                msg = f"mode must be one of {', '.join(get_args(QuestionMode))}"
                raise RunError(msg)
            question_id = f"q{len(self._threads) + 1}"
            self._append(
                "question",
                {
                    "question_id": question_id,
                    "section_id": section_id,
                    "selection": selection,
                    "text": text,
                    "mode": mode,
                },
            )
            return self._threads[question_id]

    def ask_line(self, element_id: str, line: int) -> LineAsk | None:
        """Reveal the prepared ask on `line` of diff `element_id`, `None` if none.

        A revealed ask is logged and kept as an answered reader question.
        """
        with self._changed:
            self._require_open()
            if element_id not in self._diffs:
                msg = f"unknown diff element '{element_id}'"
                raise RunError(msg)
            ask = _line_ask(self._diffs[element_id][1], line)
            if ask is not None:
                self._append(
                    "ask_line",
                    {
                        "question_id": f"q{len(self._threads) + 1}",
                        "element_id": element_id,
                        "line": line,
                    },
                )
            return ask

    def commit(self, element_id: str, text: str) -> dict[str, object]:
        """Record the reader's own options and criteria for options `element_id`.

        Returns the table the element withheld; a later commit replaces the text.
        """
        with self._changed:
            self._require_open()
            element = self._options.get(element_id)
            if element is None:
                msg = f"unknown options element '{element_id}'"
                raise RunError(msg)
            if not text.strip():
                msg = "list your options before committing"
                raise RunError(msg)
            self._append("commit", {"element_id": element_id, "text": text})
        return self.lesson.gated_payload(element)

    def reply(self, question_id: str, markdown: str) -> Reply:
        """Attach the agent's answer to reader question `question_id`."""
        with self._changed:
            if question_id not in self._threads:
                msg = f"unknown reader question '{question_id}'"
                raise RunError(msg)
            event = self._append(
                "reply", {"question_id": question_id, "markdown": markdown}
            )
            return Reply(event.seq, question_id, markdown)

    def events_after(self, seq: int, *, timeout: float) -> list[Event]:
        """Return the `question` and `submitted` events after `seq`, oldest first.

        Blocks up to `timeout` seconds until there is at least one; an empty
        list means the timeout passed.
        """
        return self._after(seq, timeout, _AGENT_EVENTS)

    def replies_after(self, seq: int, *, timeout: float) -> list[Reply]:
        """Return the replies logged after `seq`, blocking like `events_after`."""
        return [
            Reply(event.seq, event.body["question_id"], event.body["markdown"])
            for event in self._after(seq, timeout, frozenset({"reply"}))
        ]

    def submit(self) -> Summary:
        """Close the final quiz, write `results.json` and `lesson.md`, notify the agent.

        Missed questions go on the project's `Schedule`, and questions served
        from it are recorded as reviews. Raises `RunError` when a final
        question is unanswered or the run was already submitted.
        """
        with self._changed:
            self._require_open()
            missing = [qid for qid in self._finals if qid not in self._grades]
            if missing:
                msg = f"unanswered final questions: {', '.join(missing)}"
                raise RunError(msg)
            finals = {qid: self._grades[qid] for qid in self._finals}
            summary = summarise(finals.values())
            results_path = self.lesson_dir / "results.json"
            _write_atomic(
                results_path, json.dumps(self._results(finals, summary), indent=2)
            )
            _write_atomic(
                self.lesson_dir / "lesson.md",
                render_markdown(self.lesson, finals, list(self._threads.values())),
            )
            self._append(
                "submitted",
                {
                    "results_path": str(results_path),
                    "summary": dataclasses.asdict(summary),
                },
            )
            Schedule.load(self.lesson_dir.parents[2]).record_submit(self)
            return summary

    def _results(
        self, finals: Mapping[str, Grade], summary: Summary
    ) -> dict[str, object]:
        checkpoints = [
            self._grades[qid] for qid in self._checkpoints if qid in self._grades
        ]
        return {
            "lesson_id": self.lesson_id,
            "title": self.lesson.title,
            "summary": dataclasses.asdict(summary),
            "final": [
                self._graded_item(self._finals[qid], grade)
                for qid, grade in finals.items()
            ],
            "checkpoints": [
                self._graded_item(self._checkpoints[g.question_id], g)
                for g in checkpoints
            ],
            "probe": [
                self._graded_item(question, self._grades[qid])
                for qid, question in self._probes.items()
                if qid in self._grades
            ],
            "by_element": _by_element(
                [(self._elements.get(g.question_id), g) for g in checkpoints]
            ),
            "questions": [
                dataclasses.asdict(thread) for thread in self._threads.values()
            ],
            "commits": [
                {"element_id": element_id, "text": text}
                for element_id, text in self._commits.items()
            ],
            "assumptions": [
                {
                    "element_id": element_id,
                    "claim": item.claim,
                    "confidence": confidence,
                    "checked_by_spike": item.checked_by_spike,
                }
                for element_id, ratings in self._ratings.items()
                for item, confidence in zip(
                    self._assumptions[element_id].items, ratings, strict=True
                )
            ],
        }

    def _graded_item(self, question: Question, graded: Grade) -> dict[str, object]:
        return {
            **_graded_item(question, graded),
            "element": self._elements.get(question.id),
        }

    def _after(self, seq: int, timeout: float, types: frozenset[str]) -> list[Event]:
        def newer() -> list[Event]:
            return [e for e in self._log if e.seq > seq and e.type in types]

        with self._changed:
            self._changed.wait_for(newer, timeout)
            return newer()

    def _require_open(self) -> None:
        if self._summary is not None:
            msg = "the lesson has already been submitted"
            raise RunError(msg)

    def _question(self, question_id: str) -> Question:
        question = (
            self._checkpoints.get(question_id)
            or self._finals.get(question_id)
            or self._probes.get(question_id)
        )
        if question is None:
            msg = f"unknown question '{question_id}'"
            raise RunError(msg)
        return question

    def _append(self, event_type: EventType, body: Mapping[str, Any]) -> Event:
        """Persist a new event, then apply it; the caller holds `_changed`."""
        event = Event(len(self._log) + 1, event_type, body)
        with (self.lesson_dir / "events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event.as_json()) + "\n")
        self._apply(event)
        return event

    def _apply(self, event: Event) -> None:
        body = event.body
        match event.type:
            case "answer" if body["question_id"] in self._assumptions:
                self._ratings[body["question_id"]] = body["response"]
            case "answer":
                self._grades[body["question_id"]] = grade(
                    self._question(body["question_id"]),
                    body["response"],
                    body["confidence"],
                )
            case "question":
                self._threads[body["question_id"]] = QuestionThread(
                    id=body["question_id"],
                    section_id=body["section_id"],
                    selection=body["selection"],
                    text=body["text"],
                    mode=body.get("mode", "answer"),
                )
            case "reply":
                thread = self._threads[body["question_id"]]
                self._threads[thread.id] = dataclasses.replace(
                    thread, reply=body["markdown"]
                )
            case "ask_line":
                section_id, diff = self._diffs[body["element_id"]]
                ask = next(a for a in diff.asks if a.line == body["line"])
                self._threads[body["question_id"]] = QuestionThread(
                    id=body["question_id"],
                    section_id=section_id,
                    selection=f"{diff.file}:{ask.line}",
                    text=ask.question,
                    reply=ask.answer,
                )
            case "commit":
                self._commits[body["element_id"]] = body["text"]
            case "submitted":
                self._summary = summarise(self._grades[qid] for qid in self._finals)
        self._log.append(event)
        self._changed.notify_all()


def public_view_for(run: LessonRun) -> dict[str, object]:
    """Return the lesson as the reader may see it now, holding back unearned reveals."""
    view = run.lesson.public_view(run.gated)
    sections = cast("list[dict[str, list[dict[str, object]]]]", view["sections"])
    diffs = (e for s in sections for e in s["elements"] if e["type"] == "diff")
    for element, element_id in zip(diffs, _diffs_by_id(run.lesson), strict=True):
        element["id"] = element_id
    return view


@dataclass(frozen=True)
class RunOutcome:
    """Whether a `fix_the_bug` test run passed, and the tail of its output."""

    passed: bool
    log: str


def _run_in_worktree(question: FixTheBug) -> RunOutcome:
    try:
        result = run_test_command(list(question.test_command), Path(question.worktree))
    except MutationError as error:
        raise RunError(str(error)) from error
    output = ANSI_ESCAPE.sub("", result.stdout + result.stderr)
    return RunOutcome(passed=result.returncode == 0, log=output[-LOG_TAIL_CHARS:])


def _diffs_by_id(lesson: Lesson) -> dict[str, tuple[str, DiffElement]]:
    """Key the lesson's diff elements `d1`, `d2`, ... in reading order."""
    diffs = (
        (section.id, element)
        for section in lesson.sections
        for element in section.elements
        if isinstance(element, DiffElement)
    )
    return {f"d{n}": pair for n, pair in enumerate(diffs, 1)}


def _line_ask(diff: DiffElement, line: int) -> LineAsk | None:
    return next((ask for ask in diff.asks if ask.line == line), None)


def _element_names(lesson: Lesson) -> dict[str, str]:
    """Name the element each question is about: `view:<layout>`, `trace`, or a gate."""
    names: dict[str, str] = {}
    for section in lesson.sections:
        views = {e.id: e for e in section.elements if isinstance(e, ViewElement)}
        gates = {
            gate: e.type_name
            for e in section.elements
            if isinstance(gate := getattr(e, "gate", None), str)
        }
        for question in section.checkpoints:
            view = views.get(str(getattr(question, "view", None)))
            if view is not None:
                names[question.id] = f"view:{view.layout}"
            elif isinstance(question, PredictState) and question.trace_id:
                names[question.id] = "trace"
            elif question.id in gates:
                names[question.id] = gates[question.id]
    for question in (*lesson.final, *lesson.probe):
        frame = getattr(question, "frame", None)
        if isinstance(frame, dict):
            names[question.id] = f"view:{frame.get('layout')}"
    return names


def _by_element(
    graded: Iterable[tuple[str | None, Grade]],
) -> dict[str, dict[str, object]]:
    """Count answers, correct answers and the mean score per element name."""
    groups: dict[str, list[Grade]] = {}
    for name, grade_ in graded:
        groups.setdefault(str(name) if name else "null", []).append(grade_)
    return {
        name: {
            "answered": len(grades),
            "correct": sum(g.outcome == "correct" for g in grades),
            "mean_score": sum(g.score for g in grades) / len(grades),
        }
        for name, grades in groups.items()
    }


def _graded_item(question: Question, graded: Grade) -> dict[str, object]:
    return {
        "question_id": question.id,
        "type": question.type_name,
        "prompt": question.prompt,
        **{
            key: value
            for key, value in dataclasses.asdict(graded).items()
            if key != "question_id"
        },
    }


def _reshuffled(question: Question, rng: random.Random) -> Question:
    """Return `question` with its options in a new order and `correct` remapped."""
    match question:
        case (
            SingleChoice(options=options, correct=correct)
            | PredictOutput(options=options, correct=int() as correct)
        ):
            order = _new_order(len(options), rng)
            return dataclasses.replace(
                question,
                options=tuple(options[old] for old in order),
                correct=order.index(correct),
            )
        case MultipleChoice(options=options, correct=correct_set):
            order = _new_order(len(options), rng)
            return dataclasses.replace(
                question,
                options=tuple(options[old] for old in order),
                correct=tuple(sorted(order.index(old) for old in correct_set)),
            )
    return question


def _new_order(count: int, rng: random.Random) -> list[int]:
    """Shuffle `range(count)`, never returning the original order when count > 1."""
    order = list(range(count))
    rng.shuffle(order)
    if order == sorted(order):
        order = order[1:] + order[:1]
    return order


def _write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` in one step; the file is created with mode 0600."""
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(temp).replace(path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _dump_lesson(lesson: Lesson) -> dict[str, object]:
    return cast("dict[str, object]", _dump(lesson))


def _dump(value: object) -> object:
    """Like `dataclasses.asdict`, adding `type` for classes that have a `type_name`."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        typed = {"type": value.type_name} if hasattr(value, "type_name") else {}
        fields = dataclasses.fields(value)
        return typed | {f.name: _dump(getattr(value, f.name)) for f in fields}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _load_lesson(raw: dict[str, Any]) -> Lesson:
    lesson: Lesson = _rebuild(Lesson, raw)
    return lesson


def _rebuild(hint: Any, value: Any) -> Any:  # noqa: ANN401
    """Turn `value`, parsed from a `_dump_lesson` snapshot, back into type `hint`.

    A union of dataclasses picks its member by the snapshot's `type` key, or
    else by the member whose field names are exactly the snapshot's keys.
    """
    origin = get_origin(hint)
    if _is_dataclass_type(hint):
        hints = _type_hints(hint)
        return hint(
            **{
                field.name: _rebuild(hints[field.name], value[field.name])
                for field in dataclasses.fields(hint)
                if field.name in value
            }
        )
    if origin is tuple:
        args = get_args(hint)
        if args[-1] is Ellipsis:
            return tuple(_rebuild(args[0], item) for item in value)
        return tuple(_rebuild(arg, item) for arg, item in zip(args, value, strict=True))
    if origin in {Union, UnionType} and value is not None:
        present = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(present) == 1:
            return _rebuild(present[0], value)
        members = [arg for arg in get_args(hint) if _is_dataclass_type(arg)]
        if members:
            member = next(
                (
                    arg
                    for arg in members
                    if (
                        getattr(arg, "type_name", None) == value["type"]
                        if "type" in value
                        else value.keys() == _field_names(arg)
                    )
                ),
                None,
            )
            if member is None:
                msg = f"no member of {hint} matches the snapshot keys {sorted(value)}"
                raise RunError(msg)
            return _rebuild(member, value)
    return value


@functools.cache
def _type_hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _is_dataclass_type(hint: object) -> TypeGuard[type[DataclassInstance]]:
    return isinstance(hint, type) and dataclasses.is_dataclass(hint)


def _field_names(cls: type) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}
