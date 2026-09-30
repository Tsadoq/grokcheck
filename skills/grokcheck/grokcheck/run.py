"""One live lesson: answers, reader questions, agent replies, submit, and its files.

A `LessonRun` owns `<project>/.grokcheck/lessons/<lesson-id>/`:

- `lesson.json`: a snapshot of the lesson with code already resolved, so the
  run never depends on the studied files staying unchanged.
- `events.jsonl`: every answer, question, reply and submit, one JSON record per
  line, numbered by a sequence `seq` starting at 1. `LessonRun.open` replays it.
- `session.json`: how to reach the server that hosts the run (mode 0600).
- `results.json` and `lesson.md`: written on submit.

Every method is safe to call from concurrent request threads.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from grokcheck.export import QuestionThread, render_markdown
from grokcheck.grading import grade, summarise
from grokcheck.lesson import (
    Blank,
    CodeBlock,
    FillBlank,
    Lesson,
    MultipleChoice,
    OpenAnswer,
    Option,
    OrderSteps,
    PickLine,
    PredictOutput,
    Scope,
    Section,
    SingleChoice,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from grokcheck.grading import Confidence, Grade, Response, Summary
    from grokcheck.lesson import Question

EventType = Literal["answer", "question", "reply", "submitted"]

DEFAULT_RETAKE = frozenset({"incorrect", "partial", "needs_review", "confident_wrong"})

_AGENT_EVENTS = frozenset({"question", "submitted"})
_QUESTION_TYPES: dict[str, type[Question]] = {
    cls.type_name: cls
    for cls in (
        SingleChoice,
        MultipleChoice,
        OpenAnswer,
        PredictOutput,
        PickLine,
        OrderSteps,
        FillBlank,
    )
}


class RunError(Exception):
    """A request the run cannot honour in its current state, such as an unknown id."""


@dataclass(frozen=True)
class Event:
    """One entry of the run's log; `body` holds the fields of its `type`.

    `question`: `question_id`, `section_id`, `selection`, `text`.
    `submitted`: `results_path` and `summary`.
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

    `grade` carries the full reveal for a checkpoint question and is `None` for
    a final question, whose grade is withheld until submit.
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
        self._sections = {section.id for section in lesson.sections}

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
        )

    @property
    def lesson_id(self) -> str:
        """The directory name that identifies this run to the CLI."""
        return self.lesson_dir.name

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

        Raises `grading.ResponseError` when `response` has the wrong shape.
        """
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
        return Feedback(
            question_id, graded if question_id in self._checkpoints else None
        )

    def ask(self, section_id: str, selection: str, text: str) -> QuestionThread:
        """Record a reader question about `section_id` for the agent to answer.

        `selection` is the text highlighted when asking, empty for the Ask box.
        """
        with self._changed:
            self._require_open()
            if section_id not in self._sections:
                msg = f"unknown section '{section_id}'"
                raise RunError(msg)
            question_id = f"q{len(self._threads) + 1}"
            self._append(
                "question",
                {
                    "question_id": question_id,
                    "section_id": section_id,
                    "selection": selection,
                    "text": text,
                },
            )
            return self._threads[question_id]

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

        Raises `RunError` when a final question is unanswered or the run was
        already submitted.
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
                _graded_item(self._finals[qid], grade) for qid, grade in finals.items()
            ],
            "checkpoints": [
                _graded_item(self._checkpoints[g.question_id], g) for g in checkpoints
            ],
            "questions": [
                dataclasses.asdict(thread) for thread in self._threads.values()
            ],
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
        question = self._checkpoints.get(question_id) or self._finals.get(question_id)
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
                )
            case "reply":
                thread = self._threads[body["question_id"]]
                self._threads[thread.id] = dataclasses.replace(
                    thread, reply=body["markdown"]
                )
            case "submitted":
                self._summary = summarise(self._grades[qid] for qid in self._finals)
        self._log.append(event)
        self._changed.notify_all()


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
    return {
        "schema_version": lesson.schema_version,
        "title": lesson.title,
        "seed": lesson.seed,
        "scope": dataclasses.asdict(lesson.scope),
        "sections": [
            {
                "id": section.id,
                "title": section.title,
                "body": section.body,
                "code": section.code and dataclasses.asdict(section.code),
                "checkpoints": [_dump_question(q) for q in section.checkpoints],
            }
            for section in lesson.sections
        ],
        "final": [_dump_question(q) for q in lesson.final],
    }


def _dump_question(question: Question) -> dict[str, object]:
    return {"type": question.type_name, **dataclasses.asdict(question)}


def _load_lesson(raw: dict[str, Any]) -> Lesson:
    return Lesson(
        title=raw["title"],
        scope=Scope(raw["scope"]["summary"], tuple(raw["scope"]["files"])),
        sections=tuple(
            Section(
                id=section["id"],
                title=section["title"],
                body=section["body"],
                code=_load_code(section["code"]),
                checkpoints=tuple(_load_question(q) for q in section["checkpoints"]),
            )
            for section in raw["sections"]
        ),
        final=tuple(_load_question(q) for q in raw["final"]),
        seed=raw["seed"],
        schema_version=raw["schema_version"],
    )


def _load_question(raw: dict[str, Any]) -> Question:
    fields = {key: _load_field(key, value) for key, value in raw.items()}
    return _QUESTION_TYPES[fields.pop("type")](**fields)


def _load_field(key: str, value: Any) -> Any:  # noqa: ANN401
    if key == "code":
        return _load_code(value)
    if key == "options":
        return tuple(Option(**option) for option in value)
    if key == "blanks":
        return tuple(
            Blank(**{**blank, "accepted": tuple(blank["accepted"])}) for blank in value
        )
    return tuple(value) if isinstance(value, list) else value


def _load_code(raw: dict[str, Any] | None) -> CodeBlock | None:
    return CodeBlock(**raw) if raw else None
