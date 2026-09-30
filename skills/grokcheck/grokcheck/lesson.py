"""The lesson format: parsing, validation, code references, and the public view.

A lesson is the JSON file the agent writes. `load_lesson` is the only way to
turn one into a `Lesson`, and it reports every defect in a single
`LessonError` so the agent can fix them all in one edit.
"""

from __future__ import annotations

import dataclasses
import json
import random
import re
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

SCHEMA_VERSION = 1

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_BLANK_MARKER = re.compile(r"\[\[blank:([^\]]*)\]\]")
_SECRET_FIELDS = frozenset(
    {
        "correct",
        "why",
        "accepted",
        "answer_lines",
        "model_answer",
        "explanation",
    },
)
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "bash",
    ".sql": "sql",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "ini",
    ".md": "markdown",
    ".html": "xml",
    ".xml": "xml",
    ".css": "css",
}


@dataclass(frozen=True)
class Problem:
    """One defect in a lesson, located by a JSON path such as `final[2].correct`."""

    path: str
    message: str


class LessonError(Exception):
    """A lesson was rejected; `problems` lists every defect found."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        """Keep the problems and render them one per line as the message."""
        self.problems = tuple(problems)
        super().__init__("\n".join(f"{p.path}: {p.message}" for p in self.problems))


@dataclass(frozen=True)
class CodeBlock:
    """A code snippet, either inline or cut from a file in the studied project.

    `text` holds only the snippet. `start_line` is the file line number of its
    first line, for display; line numbers inside questions count from 1 at the
    first line of `text`.
    """

    language: str
    text: str
    file: str | None = None
    start_line: int = 1


@dataclass(frozen=True)
class Option:
    """A choice, with `why` naming the misconception or reasoning behind it."""

    text: str
    why: str


@dataclass(frozen=True)
class Blank:
    """One gap in a fill-the-blank text and the answers accepted for it."""

    id: str
    accepted: tuple[str, ...]
    case_sensitive: bool = True
    quote_insensitive: bool = False


@dataclass(frozen=True, kw_only=True)
class SingleChoice:
    """Pick one option; `correct` is its 0-based index."""

    type_name: ClassVar[str] = "single_choice"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock | None = None
    options: tuple[Option, ...]
    correct: int


@dataclass(frozen=True, kw_only=True)
class MultipleChoice:
    """Pick every correct option; `correct` holds their 0-based indexes."""

    type_name: ClassVar[str] = "multiple_choice"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock | None = None
    options: tuple[Option, ...]
    correct: tuple[int, ...]


@dataclass(frozen=True, kw_only=True)
class OpenAnswer:
    """Free text, self-rated by the reader against binary `rubric` items."""

    type_name: ClassVar[str] = "open_answer"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock | None = None
    model_answer: str
    rubric: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class PredictOutput:
    """Predict what `code` prints, by choosing an option or typing the output.

    Exactly one mode is set: `options` with `correct`, or `accepted` free-text
    answers.
    """

    type_name: ClassVar[str] = "predict_output"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock
    options: tuple[Option, ...] = ()
    correct: int | None = None
    accepted: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PickLine:
    """Pick the lines of `code` that answer the prompt, numbered from 1."""

    type_name: ClassVar[str] = "pick_line"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock
    answer_lines: tuple[int, ...]


@dataclass(frozen=True, kw_only=True)
class OrderSteps:
    """Put `steps` in order; they are authored in the correct order."""

    type_name: ClassVar[str] = "order_steps"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock | None = None
    steps: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class FillBlank:
    """Fill each `[[blank:<id>]]` marker in `text`."""

    type_name: ClassVar[str] = "fill_blank"
    id: str
    prompt: str
    explanation: str = ""
    language: str = "plaintext"
    text: str
    blanks: tuple[Blank, ...]


Question = (
    SingleChoice
    | MultipleChoice
    | OpenAnswer
    | PredictOutput
    | PickLine
    | OrderSteps
    | FillBlank
)


@dataclass(frozen=True)
class Scope:
    """What the lesson covers, in prose and as project-relative file paths."""

    summary: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    """One explanation step, gated by its checkpoint questions."""

    id: str
    title: str
    body: str
    code: CodeBlock | None
    checkpoints: tuple[Question, ...]


@dataclass(frozen=True)
class Lesson:
    """A validated lesson with its code references already resolved.

    `seed` fixes the order in which `order_steps` steps are shown, so the public
    view is the same every time it is produced.
    """

    title: str
    scope: Scope
    sections: tuple[Section, ...]
    final: tuple[Question, ...]
    seed: int
    schema_version: int = SCHEMA_VERSION

    def public_view(self) -> dict[str, object]:
        """Return the lesson as JSON-ready data with every answer key removed."""
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "scope": _without_secrets(dataclasses.asdict(self.scope)),
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "body": section.body,
                    **(
                        {"code": _strip(dataclasses.asdict(section.code))}
                        if section.code
                        else {}
                    ),
                    "checkpoints": [
                        self._public_question(q) for q in section.checkpoints
                    ],
                }
                for section in self.sections
            ],
            "final": [self._public_question(q) for q in self.final],
        }

    def _public_question(self, question: Question) -> dict[str, object]:
        public = _without_secrets(dataclasses.asdict(question))
        public["type"] = question.type_name
        if isinstance(question, OrderSteps):
            public["steps"] = _scrambled(question.steps, f"{self.seed}:{question.id}")
        return public


def load_lesson(path: Path, project_root: Path) -> Lesson:
    """Parse and validate the lesson at `path`, resolving code from `project_root`.

    Raises `LessonError` listing every problem found, including an unreadable
    file or malformed JSON.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LessonError([Problem("$", f"cannot read lesson file: {exc}")]) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LessonError([Problem("$", f"not valid JSON: {exc}")]) from exc
    checker = _Checker(project_root.resolve())
    lesson = _lesson(checker, raw)
    if checker.problems:
        raise LessonError(checker.problems)
    return lesson


def _without_secrets(value: dict[str, object]) -> dict[str, object]:
    return {
        key: _strip(item)
        for key, item in value.items()
        if key not in _SECRET_FIELDS and item is not None
    }


def _strip(value: object) -> object:
    if isinstance(value, dict):
        return _without_secrets(value)
    if isinstance(value, (list, tuple)):
        return [_strip(item) for item in value]
    return value


def _scrambled(steps: tuple[str, ...], seed: str) -> list[str]:
    """Shuffle deterministically, never returning the authored order.

    The authored order is the answer, so showing it unchanged would give it away.
    """
    shown = list(steps)
    random.Random(seed).shuffle(shown)  # noqa: S311
    if tuple(shown) == steps:
        shown = shown[1:] + shown[:1]
    return shown


class _Checker:
    """Collects problems while parsing, so parsing never stops at the first one.

    Readers always return a well-typed value, substituting an empty one after
    reporting a problem; the lesson built from such values is discarded.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.problems: list[Problem] = []
        self._first_use: dict[tuple[str, str], str] = {}

    def report(self, path: str, message: str) -> None:
        self.problems.append(Problem(path or "$", message))

    def claim_id(self, noun: str, ident: str, path: str) -> None:
        if not ident:
            return
        first = self._first_use.setdefault((noun, ident), path)
        if first != path:
            self.report(
                _at(path, "id"), f"duplicate {noun} id '{ident}', first used at {first}"
            )

    def fields(
        self,
        value: object,
        path: str,
        required: frozenset[str],
        optional: frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            self.report(path, "must be an object")
            return {}
        for key in sorted(required - value.keys()):
            self.report(_at(path, key), "is required")
        for key in sorted(value.keys() - required - optional):
            self.report(_at(path, key), "is not a known field")
        return value

    def text(self, obj: dict[str, object], key: str, path: str) -> str:
        value = obj.get(key, "")
        if key in obj and (not isinstance(value, str) or not value.strip()):
            self.report(_at(path, key), "must be a non-empty string")
            return ""
        return value if isinstance(value, str) else ""

    def identifier(self, obj: dict[str, object], key: str, path: str) -> str:
        value = self.text(obj, key, path)
        if value and not _ID_PATTERN.fullmatch(value):
            self.report(
                _at(path, key),
                "must start with a letter or digit and hold only letters, digits,"
                " '_' and '-'",
            )
        return value

    def flag(
        self, obj: dict[str, object], key: str, path: str, *, default: bool
    ) -> bool:
        value = obj.get(key, default)
        if not isinstance(value, bool):
            self.report(_at(path, key), "must be true or false")
            return default
        return value

    def integer(self, value: object, path: str) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            self.report(path, "must be an integer")
            return None
        return value

    def array(
        self,
        obj: dict[str, object],
        key: str,
        path: str,
        minimum: int = 0,
        maximum: int | None = None,
    ) -> list[object]:
        if key not in obj:
            return []
        value = obj[key]
        where = _at(path, key)
        if not isinstance(value, list):
            self.report(where, "must be an array")
            return []
        if len(value) < minimum:
            self.report(where, f"needs at least {minimum} item(s), has {len(value)}")
        if maximum is not None and len(value) > maximum:
            self.report(where, f"allows at most {maximum} item(s), has {len(value)}")
        return value

    def texts(
        self,
        obj: dict[str, object],
        key: str,
        path: str,
        minimum: int = 0,
        maximum: int | None = None,
    ) -> tuple[str, ...]:
        found: list[str] = []
        for index, item in enumerate(self.array(obj, key, path, minimum, maximum)):
            if isinstance(item, str) and item.strip():
                found.append(item)
            else:
                self.report(_index(_at(path, key), index), "must be a non-empty string")
        return tuple(found)


def _at(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _index(path: str, index: int) -> str:
    return f"{path}[{index}]"


def _lesson(checker: _Checker, raw: object) -> Lesson:
    obj = checker.fields(
        raw,
        "",
        frozenset({"schema_version", "title", "scope", "sections", "final"}),
        frozenset({"seed"}),
    )
    if "schema_version" in obj and (
        isinstance(obj["schema_version"], bool)
        or obj["schema_version"] != SCHEMA_VERSION
    ):
        checker.report("schema_version", f"must be {SCHEMA_VERSION}")
    title = checker.text(obj, "title", "")
    seed = zlib.crc32(title.encode())
    if "seed" in obj:
        seed = checker.integer(obj["seed"], "seed") or 0
    scope_obj = checker.fields(
        obj.get("scope"), "scope", frozenset({"summary", "files"})
    )
    scope = Scope(
        checker.text(scope_obj, "summary", "scope"),
        checker.texts(scope_obj, "files", "scope"),
    )
    sections = tuple(
        _section(checker, item, _index("sections", index))
        for index, item in enumerate(checker.array(obj, "sections", "", minimum=1))
    )
    final = _questions(checker, checker.array(obj, "final", "", minimum=3), "final")
    return Lesson(title=title, scope=scope, sections=sections, final=final, seed=seed)


def _section(checker: _Checker, value: object, path: str) -> Section:
    obj = checker.fields(
        value,
        path,
        frozenset({"id", "title", "body", "checkpoints"}),
        frozenset({"code"}),
    )
    ident = checker.identifier(obj, "id", path)
    checker.claim_id("section", ident, path)
    return Section(
        id=ident,
        title=checker.text(obj, "title", path),
        body=checker.text(obj, "body", path),
        code=_code(checker, obj["code"], _at(path, "code")) if "code" in obj else None,
        checkpoints=_questions(
            checker,
            checker.array(obj, "checkpoints", path, minimum=1, maximum=2),
            _at(path, "checkpoints"),
        ),
    )


def _code(checker: _Checker, value: object, path: str) -> CodeBlock | None:
    if isinstance(value, dict) and "file" in value:
        return _file_code(checker, value, path)
    obj = checker.fields(value, path, frozenset({"language", "text"}))
    language = checker.text(obj, "language", path)
    text = checker.text(obj, "text", path)
    return CodeBlock(language=language, text=text) if language and text else None


def _file_code(
    checker: _Checker, value: dict[str, object], path: str
) -> CodeBlock | None:
    obj = checker.fields(
        value, path, frozenset({"file", "lines"}), frozenset({"language"})
    )
    relative = checker.text(obj, "file", path)
    language = checker.text(obj, "language", path)
    span = _line_span(checker, obj, path)
    if not relative:
        return None
    file_path = _at(path, "file")
    resolved = (checker.project_root / relative).resolve()
    if not resolved.is_relative_to(checker.project_root):
        checker.report(file_path, f"'{relative}' resolves outside the project root")
        return None
    try:
        file_lines = resolved.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        checker.report(file_path, f"cannot read '{relative}' as UTF-8 text: {exc}")
        return None
    if span is None:
        return None
    start, end = span
    if end > len(file_lines):
        checker.report(
            _at(path, "lines"),
            f"ends at line {end} but '{relative}' has {len(file_lines)} lines",
        )
        return None
    return CodeBlock(
        language=language or _LANGUAGES.get(resolved.suffix.lower(), "plaintext"),
        text="\n".join(file_lines[start - 1 : end]),
        file=resolved.relative_to(checker.project_root).as_posix(),
        start_line=start,
    )


def _line_span(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[int, int] | None:
    where = _at(path, "lines")
    bounds = checker.array(obj, "lines", path, minimum=2, maximum=2)
    numbers = [
        checker.integer(item, _index(where, index)) for index, item in enumerate(bounds)
    ]
    match numbers:
        case [int() as start, int() as end]:
            pass
        case _:
            return None
    if start < 1 or end < start:
        checker.report(where, "must be [start, end] with 1 <= start <= end")
        return None
    return start, end


def _questions(
    checker: _Checker, items: list[object], path: str
) -> tuple[Question, ...]:
    parsed = (
        _question(checker, item, _index(path, index))
        for index, item in enumerate(items)
    )
    return tuple(question for question in parsed if question is not None)


@dataclass(frozen=True)
class _Stem:
    id: str
    prompt: str
    explanation: str


@dataclass(frozen=True)
class _Format:
    required: frozenset[str]
    optional: frozenset[str]
    parse: Callable[[_Checker, dict[str, object], str, _Stem], Question]


def _question(checker: _Checker, value: object, path: str) -> Question | None:
    if not isinstance(value, dict):
        checker.report(path, "must be an object")
        return None
    kind = value.get("type")
    question_format = _FORMATS.get(kind) if isinstance(kind, str) else None
    if question_format is None:
        checker.report(
            _at(path, "type"), f"must be one of {', '.join(sorted(_FORMATS))}"
        )
        return None
    obj = checker.fields(
        value,
        path,
        frozenset({"id", "type", "prompt"}) | question_format.required,
        frozenset({"explanation"}) | question_format.optional,
    )
    ident = checker.identifier(obj, "id", path)
    checker.claim_id("question", ident, path)
    stem = _Stem(
        id=ident,
        prompt=checker.text(obj, "prompt", path),
        explanation=checker.text(obj, "explanation", path),
    )
    return question_format.parse(checker, obj, path, stem)


def _optional_code(
    checker: _Checker, obj: dict[str, object], path: str
) -> CodeBlock | None:
    return _code(checker, obj["code"], _at(path, "code")) if "code" in obj else None


def _options(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[Option, ...]:
    where = _at(path, "options")
    options: list[Option] = []
    for index, item in enumerate(checker.array(obj, "options", path, minimum=2)):
        option_path = _index(where, index)
        fields = checker.fields(item, option_path, frozenset({"text", "why"}))
        options.append(
            Option(
                checker.text(fields, "text", option_path),
                checker.text(fields, "why", option_path),
            )
        )
    return tuple(options)


def _option_index(checker: _Checker, value: object, path: str, count: int) -> int:
    index = checker.integer(value, path)
    if index is None:
        return 0
    if not 0 <= index < count:
        checker.report(
            path, f"option index {index} is out of range for {count} options (0-based)"
        )
    return index


def _single_choice(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    options = _options(checker, obj, path)
    return SingleChoice(
        **dataclasses.asdict(stem),
        code=_optional_code(checker, obj, path),
        options=options,
        correct=_option_index(
            checker, obj.get("correct"), _at(path, "correct"), len(options)
        )
        if "correct" in obj
        else 0,
    )


def _multiple_choice(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    options = _options(checker, obj, path)
    where = _at(path, "correct")
    correct = tuple(
        _option_index(checker, item, _index(where, index), len(options))
        for index, item in enumerate(checker.array(obj, "correct", path, minimum=1))
    )
    if len(set(correct)) != len(correct):
        checker.report(where, "lists the same option more than once")
    return MultipleChoice(
        **dataclasses.asdict(stem),
        code=_optional_code(checker, obj, path),
        options=options,
        correct=correct,
    )


def _open_answer(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    return OpenAnswer(
        **dataclasses.asdict(stem),
        code=_optional_code(checker, obj, path),
        model_answer=checker.text(obj, "model_answer", path),
        rubric=checker.texts(obj, "rubric", path, minimum=2, maximum=6),
    )


def _predict_output(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    code = _optional_code(checker, obj, path)
    by_choice = "options" in obj or "correct" in obj
    if by_choice == ("accepted" in obj):
        checker.report(
            path, "needs either 'options' with 'correct', or 'accepted', but not both"
        )
    options = _options(checker, obj, path) if "options" in obj else ()
    if by_choice and ("options" not in obj or "correct" not in obj):
        checker.report(
            path, "needs both 'options' and 'correct' when answered by choice"
        )
    correct = (
        _option_index(checker, obj["correct"], _at(path, "correct"), len(options))
        if "correct" in obj
        else None
    )
    return PredictOutput(
        **dataclasses.asdict(stem),
        code=code or CodeBlock(language="plaintext", text=""),
        options=options,
        correct=correct,
        accepted=checker.texts(obj, "accepted", path, minimum=1),
    )


def _pick_line(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    code = _optional_code(checker, obj, path)
    where = _at(path, "answer_lines")
    lines: list[int] = []
    for index, item in enumerate(checker.array(obj, "answer_lines", path, minimum=1)):
        line = checker.integer(item, _index(where, index))
        if line is None:
            continue
        count = len(code.text.splitlines()) if code else None
        if count is not None and not 1 <= line <= count:
            checker.report(
                _index(where, index),
                f"line {line} is outside the snippet (1 to {count})",
            )
        lines.append(line)
    if len(set(lines)) != len(lines):
        checker.report(where, "lists the same line more than once")
    return PickLine(
        **dataclasses.asdict(stem),
        code=code or CodeBlock(language="plaintext", text=""),
        answer_lines=tuple(lines),
    )


def _order_steps(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    steps = checker.texts(obj, "steps", path, minimum=2)
    if len(set(steps)) != len(steps):
        checker.report(
            _at(path, "steps"), "must not repeat a step, or its position is ambiguous"
        )
    return OrderSteps(
        **dataclasses.asdict(stem),
        code=_optional_code(checker, obj, path),
        steps=steps,
    )


def _fill_blank(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    text = checker.text(obj, "text", path)
    blanks: list[Blank] = []
    where = _at(path, "blanks")
    for index, item in enumerate(checker.array(obj, "blanks", path, minimum=1)):
        blank_path = _index(where, index)
        fields = checker.fields(
            item,
            blank_path,
            frozenset({"id", "accepted"}),
            frozenset({"case_sensitive", "quote_insensitive"}),
        )
        blanks.append(
            Blank(
                id=checker.identifier(fields, "id", blank_path),
                accepted=checker.texts(fields, "accepted", blank_path, minimum=1),
                case_sensitive=checker.flag(
                    fields, "case_sensitive", blank_path, default=True
                ),
                quote_insensitive=checker.flag(
                    fields, "quote_insensitive", blank_path, default=False
                ),
            ),
        )
    _check_blank_markers(checker, text, blanks, path)
    return FillBlank(
        **dataclasses.asdict(stem),
        language=checker.text(obj, "language", path) or "plaintext",
        text=text,
        blanks=tuple(blanks),
    )


def _check_blank_markers(
    checker: _Checker, text: str, blanks: list[Blank], path: str
) -> None:
    markers = _BLANK_MARKER.findall(text)
    defined = [blank.id for blank in blanks]
    for marker in dict.fromkeys(markers):
        if marker not in defined:
            checker.report(
                _at(path, "text"),
                f"marker [[blank:{marker}]] has no matching entry in blanks",
            )
    for index, ident in enumerate(defined):
        if ident and ident not in markers:
            checker.report(
                _at(_index(_at(path, "blanks"), index), "id"),
                f"blank '{ident}' has no marker in text",
            )
        elif ident and defined.index(ident) != index:
            checker.report(
                _at(_index(_at(path, "blanks"), index), "id"),
                f"duplicate blank id '{ident}'",
            )
    if markers and len(markers) != len(set(markers)):
        checker.report(_at(path, "text"), "uses the same blank marker more than once")


_FORMATS: dict[str, _Format] = {
    SingleChoice.type_name: _Format(
        frozenset({"options", "correct"}), frozenset({"code"}), _single_choice
    ),
    MultipleChoice.type_name: _Format(
        frozenset({"options", "correct"}), frozenset({"code"}), _multiple_choice
    ),
    OpenAnswer.type_name: _Format(
        frozenset({"model_answer", "rubric"}), frozenset({"code"}), _open_answer
    ),
    PredictOutput.type_name: _Format(
        frozenset({"code"}),
        frozenset({"options", "correct", "accepted"}),
        _predict_output,
    ),
    PickLine.type_name: _Format(
        frozenset({"code", "answer_lines"}), frozenset(), _pick_line
    ),
    OrderSteps.type_name: _Format(
        frozenset({"steps"}), frozenset({"code"}), _order_steps
    ),
    FillBlank.type_name: _Format(
        frozenset({"text", "blanks"}), frozenset({"language"}), _fill_blank
    ),
}
