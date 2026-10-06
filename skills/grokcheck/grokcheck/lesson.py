"""The lesson format: parsing, validation, code references, and the public view.

A lesson is the JSON file the agent writes. `load_lesson` is the only way to
turn one into a `Lesson`, and it reports every defect in a single
`LessonError` so the agent can fix them all in one edit.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import random
import re
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Generic,
    Literal,
    Protocol,
    TypeVar,
    cast,
    get_args,
)

from grokcheck import diagrams
from grokcheck.sources import SOURCES_DIR, page_of
from grokcheck.trace import Trace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from grokcheck.data import Dataset

SCHEMA_VERSION = 3
_SCHEMA_VERSIONS = (2, 3)
VIDEO_CACHE_DIR = Path.home() / ".cache" / "grokcheck" / "videos"

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
        "answer",
        "fails",
        "log",
        "affected",
        "distractors",
        "answer_cells",
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


@dataclass(frozen=True, kw_only=True)
class PredictState:
    """Predict the state at `step` of a trace version or of a steps `view`, by choice.

    Either `trace_id` with `version` or `view` is set. `version` and `step` are
    0-based indexes; graded as `single_choice`.
    """

    type_name: ClassVar[str] = "predict_state"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock | None = None
    trace_id: str | None = None
    version: int | None = None
    view: str | None = None
    step: int
    options: tuple[Option, ...]
    correct: int


@dataclass(frozen=True)
class Mutation:
    """Line `line` (1-based) of project file `file` set to `replacement`.

    `None` deletes the line.
    """

    file: str
    line: int
    replacement: str | None = None


@dataclass(frozen=True)
class TestCase:
    """A test of a mutation run and whether it `fails` on the mutant."""

    name: str
    fails: bool


@dataclass(frozen=True, kw_only=True)
class MutationQuiz:
    """Tick the `tests` that fail once `mutation` is applied; `log` is the run's tail.

    Recorded by `grokcheck mutate`; graded by exact set match.
    """

    type_name: ClassVar[str] = "mutation_quiz"
    id: str
    prompt: str
    explanation: str = ""
    mutation: Mutation
    tests: tuple[TestCase, ...]
    log: str


@dataclass(frozen=True, kw_only=True)
class FixTheBug:
    """Repair the mutant planted in `worktree` until `test_command` passes there.

    Here `mutation.replacement` is the original line, the answer key. The
    server runs the tests and grades the result.
    """

    type_name: ClassVar[str] = "fix_the_bug"
    id: str
    prompt: str
    explanation: str = ""
    mutation: Mutation
    worktree: str
    test_command: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ChangeImpact:
    """Pick the `candidates` that `change` affects; `affected` holds their indexes."""

    type_name: ClassVar[str] = "change_impact"
    id: str
    prompt: str
    explanation: str = ""
    code: CodeBlock | None = None
    change: str
    candidates: tuple[Option, ...]
    affected: tuple[int, ...]


@dataclass(frozen=True)
class ParsonsLine:
    """One line of a Parsons solution, `indent` counted in levels from 0."""

    text: str
    indent: int


@dataclass(frozen=True, kw_only=True)
class Parsons:
    """Assemble `lines`, authored in order, while leaving out the `distractors`."""

    type_name: ClassVar[str] = "parsons"
    id: str
    prompt: str
    explanation: str = ""
    lines: tuple[ParsonsLine, ...]
    distractors: tuple[Option, ...]


@dataclass(frozen=True, kw_only=True)
class SelectItems:
    """Pick the items of a view that `answer` matches.

    `view` names a section view; a final or probe carries its inline view as
    the public `frame` instead. `candidates` are the cells the reader may pick,
    `answer_cells` the cells `answer` matches.
    """

    type_name: ClassVar[str] = "select_items"
    id: str
    prompt: str
    explanation: str = ""
    view: str | None = None
    frame: dict[str, object] | None = None
    answer: dict[str, object]
    candidates: tuple[str, ...] = ()
    answer_cells: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableBlank:
    """One masked `field` of the table item whose `_cell` is `cell`."""

    cell: str
    field: str


@dataclass(frozen=True, kw_only=True)
class FillTable:
    """Fill the masked cells of a `table` view; `cells` holds the right values."""

    type_name: ClassVar[str] = "fill_table"
    id: str
    prompt: str
    explanation: str = ""
    view: str | None = None
    frame: dict[str, object] | None = None
    blanks: tuple[TableBlank, ...] = ()
    cells: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)


Question = (
    SingleChoice
    | MultipleChoice
    | OpenAnswer
    | PredictOutput
    | PickLine
    | OrderSteps
    | FillBlank
    | PredictState
    | MutationQuiz
    | FixTheBug
    | ChangeImpact
    | Parsons
    | SelectItems
    | FillTable
)


Depth = Literal["short", "detail"]


@dataclass(frozen=True)
class LinesBacking:
    """An inclusive 1-based `lines` span of a project file; `symbol` appears in it.

    `quote` appears in the span up to whitespace, and is required when the file
    is an ingested document under `.grokcheck/sources/`.
    """

    file: str
    lines: tuple[int, int]
    symbol: str | None = None
    quote: str | None = None


@dataclass(frozen=True)
class SourceBacking:
    """A page outside the project, such as library docs, at a pinned `version`."""

    url: str
    version: str


@dataclass(frozen=True)
class SpikeBacking:
    """The recorded result of the spike `spike_id`."""

    spike_id: str


@dataclass(frozen=True)
class Unverified:
    """No evidence yet; `reason` says why, and the reader sees a warning."""

    reason: str


@dataclass(frozen=True)
class DataBacking:
    """The rows of recorded dataset `data` that the selector `rows` matches."""

    data: str
    rows: dict[str, object]


Backing = LinesBacking | SourceBacking | SpikeBacking | Unverified | DataBacking
Verdict = Literal["supported", "contradicted", "unchecked"]
_VERDICTS: tuple[Verdict, ...] = get_args(Verdict)
BACKING_CLASSES: dict[str, type[Backing]] = {
    "file": LinesBacking,
    "url": SourceBacking,
    "spike_id": SpikeBacking,
    "reason": Unverified,
    "data": DataBacking,
}
"""Each backing class, keyed by the one field that marks a backing as that kind."""


@dataclass(frozen=True)
class Claim:
    """A statement an element makes, with the one piece of evidence behind it.

    `verified` is the grounding pass's verdict; a `contradicted` claim never
    loads. `page` is the source page a cited document line falls on.
    """

    text: str
    backing: Backing
    verified: Verdict = "unchecked"
    page: int | None = None


@dataclass(frozen=True, kw_only=True)
class ProseElement:
    """Markdown shown after the section body; `detail` hides it in the short view."""

    type_name: ClassVar[str] = "prose"
    markdown: str
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CodeElement:
    """A code block with an optional caption."""

    type_name: ClassVar[str] = "code"
    code: CodeBlock
    caption: str = ""
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


DiffOp = Literal[" ", "+", "-"]
_DIFF_OPS: tuple[DiffOp, ...] = get_args(DiffOp)


@dataclass(frozen=True)
class DiffLine:
    """One line of a hunk: `op` is `" "` for context, `"+"` added, `"-"` removed."""

    op: DiffOp
    text: str


@dataclass(frozen=True)
class DiffNote:
    """An explanation of the new-side `lines` span, backed by `cite`."""

    lines: tuple[int, int]
    cite: LinesBacking
    text: str


@dataclass(frozen=True)
class LineAsk:
    """An ungraded question on one new-side `line`; `answer` is revealed on click."""

    line: int
    question: str
    answer: str


@dataclass(frozen=True, kw_only=True)
class DiffElement:
    """One hunk of `git diff` for `file`, with notes and asks on its new side.

    `old_start` and `new_start` are the hunk header's first line numbers; `0`
    means that side is empty.
    """

    type_name: ClassVar[str] = "diff"
    file: str
    old_start: int
    new_start: int
    lines: tuple[DiffLine, ...]
    notes: tuple[DiffNote, ...] = ()
    asks: tuple[LineAsk, ...] = ()
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


_OWNERS = ("ours", "library", "stdlib")
_OWNER_COLOURS = ("accent", "good", "bad", "warn", "muted")


@dataclass(frozen=True)
class Term:
    """A name the lesson uses, who owns it, and the snippet `lines` it appears on.

    `owner` is one of `ours`, `library` and `stdlib`, or a label of the
    element's `owners` legend.

    `library_term` names the library's own word for it in a translation table;
    `false_friend` flags a term that sounds like the library's but means
    something else.
    """

    term: str
    owner: str
    definition: str
    lines: tuple[int, ...] = ()
    detail: str = ""
    library_term: str | None = None
    false_friend: bool = False


@dataclass(frozen=True, kw_only=True)
class VocabElement:
    """Terms beside the code they appear in; done once `min_opened` are opened.

    `owners` maps each owner label to a page colour token, replacing the
    default owners.
    """

    type_name: ClassVar[str] = "vocab"
    code: CodeBlock | None
    terms: tuple[Term, ...]
    min_opened: int = 3
    owners: dict[str, str] | None = None
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


PanelKind = Literal["status", "chips", "nullable_chips"]
_PANEL_KINDS: tuple[PanelKind, ...] = get_args(PanelKind)


@dataclass(frozen=True)
class StatePanel:
    """One box of a trace's state view.

    `kind` says how a step's value for it is drawn: `status` a line of text,
    `chips` a list, `nullable_chips` a list or `null` for "does not exist".
    """

    id: str
    label: str
    kind: PanelKind


@dataclass(frozen=True)
class TraceStep:
    """One step of a recorded run: the authored `state` per panel id and `narration`.

    `cur_line` is the file line the run was on, from the recording.
    """

    cur_line: int | None
    state: dict[str, object]
    narration: tuple[str, ...]


@dataclass(frozen=True)
class TraceVersion:
    """One recorded run of `code`, such as the code before or after a change."""

    label: str
    code: CodeBlock
    steps: tuple[TraceStep, ...]


@dataclass(frozen=True, kw_only=True)
class TraceElement:
    """A stepper over one or more recorded runs, with a state panel per `panels`.

    `gate` names the `predict_state` checkpoint that reveals the steps it asks
    about; until it is answered the page gets only their `cur_line`.
    """

    type_name: ClassVar[str] = "trace"
    trace_id: str
    title: str
    versions: tuple[TraceVersion, ...]
    panels: tuple[StatePanel, ...]
    gate: str | None = None
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SpikeElement:
    """A spike's hypothesis and script, read from `.grokcheck/spikes/<spike_id>/`.

    `pins` maps each dependency to its exact version. `result` is the recorded
    run, withheld until the `predict_output` checkpoint `gate` is answered.
    """

    type_name: ClassVar[str] = "spike"
    spike_id: str
    hypothesis: str
    code: CodeBlock
    pins: dict[str, str]
    exclude_newer: str
    result: dict[str, object]
    gate: str
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


DiagramKind = Literal["flowchart", "sequence", "state", "class", "er", "before_after"]
_DIAGRAM_KINDS: tuple[DiagramKind, ...] = get_args(DiagramKind)


@dataclass(frozen=True, kw_only=True)
class DiagramElement:
    """A Mermaid diagram, which its section's checkpoints must ask about."""

    type_name: ClassVar[str] = "diagram"
    mermaid: str
    caption: str
    legend: str = ""
    kind: DiagramKind = "flowchart"
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


Standing = Literal["do_nothing", "existing_dependency", "build", "other"]
_STANDINGS: tuple[Standing, ...] = get_args(Standing)


@dataclass(frozen=True)
class OptionConstraint:
    """A limit an option must respect; a soft one names who can lift it."""

    text: str
    hard: bool
    waivable_by: str | None = None


@dataclass(frozen=True)
class OptionRow:
    """One row of an options table.

    `standing` marks the baselines every table carries: doing nothing, using
    a dependency the project already has, and building it.
    """

    name: str
    costs: str
    assumes: str
    standing: Standing
    sketch: CodeBlock | None = None
    constraints: tuple[OptionConstraint, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OptionsElement:
    """The options for `question`, compared on `criteria`.

    With `reader_first`, `criteria` and `options` are withheld until the
    reader commits their own list under `id`.
    """

    type_name: ClassVar[str] = "options"
    id: str
    question: str
    criteria: tuple[str, ...]
    options: tuple[OptionRow, ...]
    reader_first: bool = True
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


@dataclass(frozen=True)
class Assumption:
    """A claim a choice rests on; `checked_by_spike` names the spike that tests it.

    `verified` is the grounding pass's verdict, as on a `Claim`.
    """

    claim: str
    backing: Backing
    checked_by_spike: str | None = None
    verified: Verdict = "unchecked"


@dataclass(frozen=True, kw_only=True)
class AssumptionsElement:
    """Assumptions the reader rates; `id` is answered with one confidence per item."""

    type_name: ClassVar[str] = "assumptions"
    id: str
    items: tuple[Assumption, ...]
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


@dataclass(frozen=True)
class PlaygroundInput:
    """A slider over `min` to `max` in steps of `step`, starting at `value`."""

    id: str
    label: str
    min: int
    max: int
    step: int
    value: int


@dataclass(frozen=True)
class Constraint:
    """Keeps input `after` above input `gt` by moving `after` up when needed."""

    after: str
    gt: str


@dataclass(frozen=True)
class Preset:
    """A named slider position; `values` maps every input id to its value."""

    label: str
    values: dict[str, int]


@dataclass(frozen=True)
class Row:
    """One timeline row; the authored state cells for it are keyed by `id`."""

    id: str
    label: str


@dataclass(frozen=True)
class LegendEntry:
    """What a `cell` class means, drawn in the page colour token `colour`."""

    cell: str
    label: str
    colour: str


Outcome = Literal["complete", "lost", "hang"]
_OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)


@dataclass(frozen=True)
class VariantOutcome:
    """How one variant's run ended and how many events it `lost`."""

    outcome: Outcome
    lost: int = 0


@dataclass(frozen=True)
class PlaygroundState:
    """The recorded result for one input combination.

    `key` is the input values joined with `,` in input order. `cells` holds
    one cell class per tick for each row, and `outcomes` one outcome per
    variant, both in authored order.
    """

    key: str
    cells: tuple[tuple[str, ...], ...]
    outcomes: tuple[VariantOutcome, ...]
    explain: str


@dataclass(frozen=True)
class Condition:
    """Holds when `variant` ends in `outcome` having lost at least `min_lost`."""

    variant: str
    outcome: Outcome
    min_lost: int = 0


@dataclass(frozen=True)
class PlaygroundTask:
    """Met once the reader reaches a state where every condition in `when` holds."""

    text: str
    when: tuple[Condition, ...]


@dataclass(frozen=True, kw_only=True)
class PlaygroundElement:
    """Sliders over a state table recorded for every input combination.

    `variants` name the rows whose outcome is shown and that tasks test; the
    element is done once every task has been met.
    """

    type_name: ClassVar[str] = "playground"
    inputs: tuple[PlaygroundInput, ...]
    constraints: tuple[Constraint, ...] = ()
    presets: tuple[Preset, ...] = ()
    variants: tuple[str, ...]
    ticks: int
    rows: tuple[Row, ...]
    legend: tuple[LegendEntry, ...]
    states: tuple[PlaygroundState, ...]
    tasks: tuple[PlaygroundTask, ...] = ()
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


@dataclass(frozen=True, kw_only=True)
class VideoElement:
    """A narrated video `id` with its WebVTT `captions`, `duration` seconds long.

    `src` and `captions` are absolute paths inside the lesson directory or
    `VIDEO_CACHE_DIR`; the server streams them and nothing else.
    """

    type_name: ClassVar[str] = "video"
    id: str
    src: str
    captions: str
    duration: float
    transcript: str
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


_VIEW_OPTIONAL = frozenset(
    {"data", "where", "encode", "split", "inputs", "presets", "tasks", "missing"}
    | {"marks", "layers", "notes", "mask", "fill", "gate", "steps", "nodes"}
)
Layout = Literal["lanes", "table", "steps", "blocks", "decision"]
LAYOUTS: tuple[Layout, ...] = get_args(Layout)
KIND_COLOURS = ("accent", "ok", "warn", "bad", "replay", "num", "fn", "muted")


@dataclass(frozen=True)
class Kind:
    """A class of view item, drawn as a chip with `label` in colour token `colour`."""

    id: str
    label: str
    colour: str


@dataclass(frozen=True)
class DatasetUse:
    """A dataset the lesson declares, with what was read from its file at load.

    `source` is the file's `source.kind`; `provenance` is what the page shows
    under a view; `stale` lists cited files changed since the recording.
    """

    id: str
    held_out: dict[str, object] | None = None
    source: str = "run"
    provenance: dict[str, object] | None = None
    stale: tuple[str, ...] = ()


Control = Literal["range", "toggle", "select"]
Place = Literal["column", "status"]
NodeKind = Literal["check", "outcome"]


@dataclass(frozen=True)
class ViewInput:
    """A control choosing the value of `field`; `values` are computed at load."""

    field: str
    label: str
    control: Control
    default: object
    follows: str | None = None
    hint: str = ""
    values: tuple[object, ...] = ()


@dataclass(frozen=True)
class ViewPreset:
    """A named scenario: `values` maps input fields to values."""

    label: str
    values: dict[str, object]


@dataclass(frozen=True)
class ViewTask:
    """Met once an item of the current scenario matches the selector `when`."""

    text: str
    when: dict[str, object]


@dataclass(frozen=True)
class Mark:
    """A label on the items `where` matches, above their column or in the status."""

    where: dict[str, object]
    text: str
    tone: str = "accent"
    place: Place = "column"


@dataclass(frozen=True)
class Layer:
    """A toggle showing extra `fields` under the items `where` matches."""

    id: str
    label: str
    fields: tuple[str, ...]
    where: dict[str, object] = dataclasses.field(default_factory=dict)
    on: bool = False


@dataclass(frozen=True)
class ViewNote:
    """A grounded statement about the items `where` matches."""

    where: dict[str, object]
    text: str
    verified: Verdict = "unchecked"


@dataclass(frozen=True)
class Span:
    """Lines of a file from 1-based `start_line`, joined by newlines in `text`."""

    start_line: int
    text: str


@dataclass(frozen=True)
class StepCode:
    """The code a step shows: one or more spans of one project file."""

    file: str
    language: str
    spans: tuple[Span, ...]


@dataclass(frozen=True)
class ViewStep:
    """One step of a `steps` view: its code, a note, links and state chips.

    A step of a data steps view has its item's `line` and `show` fields and no
    note.
    """

    code: StepCode
    note: str = ""
    link: tuple[str, ...] = ()
    show: dict[str, object] = dataclasses.field(default_factory=dict)
    line: int | None = None
    verified: Verdict = "unchecked"


@dataclass(frozen=True)
class DecisionNode:
    """A check or outcome of a `decision` view, cut from the cited `code` lines.

    `yes` is the span whose running means a check answered yes; `example`
    names input values that reach the node.
    """

    id: str
    label: str
    kind: NodeKind
    code: CodeBlock
    lines: tuple[int, int]
    note: str
    yes: tuple[int, int] | None = None
    tone: str | None = None
    example: dict[str, object] | None = None
    verified: Verdict = "unchecked"


@dataclass(frozen=True, kw_only=True)
class ViewElement:
    """A picture drawn from recorded rows, in one of the `LAYOUTS`.

    `items` are the derived items of every scenario, answers included;
    `inputs_from` names the view whose inputs this one shares. `choices` maps
    each `fill` field to the values a reader may pick. `matrix` names the
    dataset's input fields.
    """

    type_name: ClassVar[str] = "view"
    id: str
    layout: Layout
    caption: str
    data: str | None = None
    where: dict[str, object] = dataclasses.field(default_factory=dict)
    encode: dict[str, object] = dataclasses.field(default_factory=dict)
    split: str | None = None
    inputs: tuple[ViewInput, ...] = ()
    inputs_from: str | None = None
    presets: tuple[ViewPreset, ...] = ()
    tasks: tuple[ViewTask, ...] = ()
    missing: str = ""
    marks: tuple[Mark, ...] = ()
    layers: tuple[Layer, ...] = ()
    notes: tuple[ViewNote, ...] = ()
    mask: dict[str, object] | None = None
    fill: tuple[str, ...] = ()
    gate: str | None = None
    steps: tuple[ViewStep, ...] = ()
    nodes: tuple[DecisionNode, ...] = ()
    items: tuple[dict[str, object], ...] = ()
    choices: dict[str, list[object]] = dataclasses.field(default_factory=dict)
    matrix: tuple[str, ...] = ()
    depth: Depth = "short"
    claims: tuple[Claim, ...] = ()


Element = (
    ProseElement
    | CodeElement
    | DiffElement
    | VocabElement
    | TraceElement
    | SpikeElement
    | PlaygroundElement
    | DiagramElement
    | OptionsElement
    | AssumptionsElement
    | VideoElement
    | ViewElement
)
GatedElement = TraceElement | SpikeElement | ViewElement


@dataclass(frozen=True)
class Scope:
    """What the lesson covers, in prose and as project-relative file paths."""

    summary: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    """One explanation step, gated by its checkpoint questions.

    `body` is the lead prose; `elements` follow it in authored order.
    `code_sugar` is set when `elements[0]` came from the section's `code` field,
    so the authored `elements` array starts one later.
    """

    id: str
    title: str
    body: str
    elements: tuple[Element, ...]
    checkpoints: tuple[Question, ...]
    code_sugar: bool = dataclasses.field(default=False, compare=False)


Subject = Literal[
    "change", "area", "concept", "library", "options", "decision", "document"
]
_SUBJECTS: tuple[Subject, ...] = get_args(Subject)
_TIME_BUDGETS = (5, 15, 30)
_CONTENT = ("structure", "behaviour", "change", "tests")
_CODE_ONLY = frozenset(
    {"trace", "diff", "mutation_quiz", "fix_the_bug", "spike", "playground"}
)
_CLAIM_ID = re.compile(
    r"sections\[(\d+)\]\.elements\[(\d+)\]\.(claims|items|steps|nodes|notes)\[(\d+)\]"
)
ClaimKind = Literal["claims", "items", "steps", "nodes", "notes"]


def format_claim_id(section: int, element: int, kind: ClaimKind, index: int) -> str:
    """Return the authored JSON path of a claim, as `Lesson.claims()` yields it."""
    return f"sections[{section}].elements[{element}].{kind}[{index}]"


def parse_claim_id(claim_id: str) -> tuple[int, int, ClaimKind, int] | None:
    """Invert `format_claim_id`; `None` when `claim_id` is not a claim path."""
    match = _CLAIM_ID.fullmatch(claim_id)
    if match is None:
        return None
    section, element, kind, index = match.groups()
    return int(section), int(element), cast("ClaimKind", kind), int(index)


@dataclass(frozen=True)
class Plan:
    """Why the lesson is shaped as it is, picked from the subject strategy table.

    `media` and `rejected` name element or question types; `time_budget` is in
    minutes.
    """

    subject: Subject
    time_budget: int
    content: tuple[str, ...]
    media: tuple[str, ...]
    rejected: tuple[str, ...]
    rationale: tuple[str, ...]
    default_depth: Depth


@dataclass(frozen=True)
class Lesson:
    """A validated lesson with its code references already resolved.

    `seed` fixes the order in which `order_steps` steps are shown, so the public
    view is the same every time it is produced. `probe` holds closed questions
    asked before the first section to set the starting depth; they never count
    toward the score. `intro` is a short spoken-style opening shown above the
    first section. `kinds` and `datasets` serve the lesson's views.
    """

    title: str
    scope: Scope
    sections: tuple[Section, ...]
    final: tuple[Question, ...]
    seed: int
    schema_version: int = SCHEMA_VERSION
    plan: Plan | None = None
    probe: tuple[Question, ...] = ()
    intro: str | None = None
    kinds: tuple[Kind, ...] = ()
    datasets: tuple[DatasetUse, ...] = ()

    def public_view(
        self, gated: Callable[[Element], bool] | None = None
    ) -> dict[str, object]:
        """Return the lesson as JSON-ready data with every answer key removed.

        An element with a `gate` also loses its gated payload while `gated`
        returns true for it; without `gated` every gate counts as unanswered.
        """
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "intro": self.intro,
            "scope": _without_secrets(dataclasses.asdict(self.scope)),
            "plan": dataclasses.asdict(self.plan) if self.plan else None,
            "kinds": [dataclasses.asdict(kind) for kind in self.kinds],
            "datasets": [use.provenance for use in self.datasets if use.provenance],
            "probe": [self._public_question(q) for q in self.probe],
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "body": section.body,
                    "elements": [
                        self._public_element(e, withhold=gated is None or gated(e))
                        for e in section.elements
                    ],
                    "checkpoints": [
                        self._public_question(q) for q in section.checkpoints
                    ],
                }
                for section in self.sections
            ],
            "final": [self._public_question(q) for q in self.final],
        }

    def claims(self) -> Iterator[tuple[str, Claim]]:
        """Yield every element claim with its JSON path in the authored lesson.

        Each assumption of an assumptions element is yielded as a claim too,
        and so are the notes of a view: its steps, nodes and `notes`.
        """
        for i, section in enumerate(self.sections):
            for j, element in enumerate(section.elements):
                authored = j - section.code_sugar
                for k, claim in enumerate(element.claims):
                    yield format_claim_id(i, authored, "claims", k), claim
                if isinstance(element, AssumptionsElement):
                    for k, item in enumerate(element.items):
                        yield (
                            format_claim_id(i, authored, "items", k),
                            Claim(item.claim, item.backing, item.verified),
                        )
                if isinstance(element, ViewElement):
                    for kind, k, claim in _view_claims(element):
                        yield format_claim_id(i, authored, kind, k), claim

    def questions(self) -> Iterator[tuple[str, Question]]:
        """Yield every question with its JSON path in the authored lesson."""
        for i, section in enumerate(self.sections):
            for j, question in enumerate(section.checkpoints):
                yield f"sections[{i}].checkpoints[{j}]", question
        for name, questions in (("probe", self.probe), ("final", self.final)):
            for k, question in enumerate(questions):
                yield f"{name}[{k}]", question

    def _public_question(self, question: Question) -> dict[str, object]:
        public = _without_secrets(dataclasses.asdict(question))
        public["type"] = question.type_name
        if isinstance(question, OrderSteps):
            public["steps"] = _scrambled(question.steps, f"{self.seed}:{question.id}")
        if isinstance(question, Parsons):
            texts = tuple(line.text for line in question.lines) + tuple(
                d.text for d in question.distractors
            )
            public["lines"] = _scrambled(texts, f"{self.seed}:{question.id}")
        if isinstance(question, FixTheBug):
            mutation = question.mutation
            public["mutation"] = {"file": mutation.file, "line": mutation.line}
        if isinstance(question, FillTable):
            del public["cells"]
        if isinstance(question, (SelectItems, FillTable)) and question.frame:
            public["frame"] = question.frame
        return public

    def gated_payload(
        self, element: GatedElement | OptionsElement
    ) -> dict[str, object]:
        """Return what `element` withholds until its gate is answered.

        For a spike that is its recorded `result`; for a trace, every step,
        `state` and `narration` included, of the version its `predict_state`
        gate asks about.
        """
        if isinstance(element, SpikeElement):
            return {"spike_id": element.spike_id, "result": element.result}
        if isinstance(element, OptionsElement):
            public = self._public_element(element, withhold=False)
            return {key: public[key] for key in ("id", "criteria", "options")}
        if isinstance(element, ViewElement):
            from grokcheck import views  # noqa: PLC0415

            return views.gated_payload(element)
        gate = self._gate(element)
        version = gate.version or 0
        steps = element.versions[version].steps
        return {
            "trace_id": element.trace_id,
            "version": version,
            "steps": [_without_secrets(dataclasses.asdict(s)) for s in steps],
        }

    def _gate(self, element: TraceElement | ViewElement) -> PredictState:
        return next(
            question
            for section in self.sections
            for question in section.checkpoints
            if isinstance(question, PredictState) and question.id == element.gate
        )

    def _public_element(self, element: Element, *, withhold: bool) -> dict[str, object]:
        if isinstance(element, ViewElement):
            from grokcheck import views  # noqa: PLC0415

            step = None
            if element.layout == "steps" and element.gate:
                step = self._gate(element).step
            return views.public(element, withhold=withhold, step=step)
        public = _without_secrets(dataclasses.asdict(element))
        public["type"] = element.type_name
        if withhold and isinstance(element, TraceElement) and element.gate:
            gate = self._gate(element)
            versions = cast(
                "list[dict[str, list[dict[str, object]]]]", public["versions"]
            )
            for hidden in versions[gate.version or 0]["steps"][gate.step :]:
                del hidden["state"], hidden["narration"]
        if withhold and isinstance(element, SpikeElement):
            del public["result"]
        if withhold and isinstance(element, OptionsElement) and element.reader_first:
            del public["criteria"], public["options"]
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
    checker = _Checker(project_root.resolve(), path.parent.resolve())
    lesson = _lesson(checker, raw)
    if checker.problems:
        raise LessonError(checker.problems)
    return lesson


def _view_claims(view: ViewElement) -> Iterator[tuple[ClaimKind, int, Claim]]:
    for k, step in enumerate(view.steps):
        if step.note:
            spans = step.code.spans
            first = spans[0].start_line
            last = spans[-1].start_line + len(spans[-1].text.splitlines()) - 1
            backing = LinesBacking(step.code.file, (first, max(first, last)))
            yield "steps", k, Claim(step.note, backing, step.verified)
    for k, node in enumerate(view.nodes):
        backing = LinesBacking(node.code.file or "", node.lines)
        yield "nodes", k, Claim(node.note, backing, node.verified)
    if view.data:
        from grokcheck.views import note_rows  # noqa: PLC0415

        for k, note in enumerate(view.notes):
            backing_rows = DataBacking(view.data, note_rows(view, note))
            yield "notes", k, Claim(note.text, backing_rows, note.verified)


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

    def __init__(self, project_root: Path, lesson_dir: Path) -> None:
        self.project_root = project_root
        self.lesson_dir = lesson_dir
        self.problems: list[Problem] = []
        self._first_use: dict[tuple[str, str], str] = {}
        self.schema_version = SCHEMA_VERSION
        self.kinds: set[str] = set()
        self.datasets: dict[str, Dataset] = {}
        self.uses: dict[str, DatasetUse] = {}
        self.view_bytes = 0

    def failed_since(self, path: str) -> bool:
        return any(
            p.path == path or p.path.startswith((f"{path}.", f"{path}["))
            for p in self.problems
        )

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
        frozenset({"seed", "plan", "probe", "intro", "kinds", "datasets"}),
    )
    version = obj.get("schema_version", SCHEMA_VERSION)
    if isinstance(version, bool) or version not in _SCHEMA_VERSIONS:
        checker.report("schema_version", "must be 2 or 3")
    else:
        checker.schema_version = version
    from grokcheck import views  # noqa: PLC0415

    kinds = views.kinds(checker, obj)
    datasets = views.datasets(checker, obj)
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
    probe = _probe(checker, checker.array(obj, "probe", "", maximum=2))
    for name, questions in (("final", final), ("probe", probe)):
        for index, question in enumerate(questions):
            if isinstance(question, PredictState):
                checker.report(
                    _at(_index(name, index), "type"),
                    "predict_state is only allowed as a checkpoint beside its trace",
                )
    plan = _plan(checker, obj["plan"], sections) if "plan" in obj else None
    return Lesson(
        title=title,
        scope=scope,
        sections=sections,
        final=final,
        seed=seed,
        schema_version=checker.schema_version,
        plan=plan,
        probe=probe,
        intro=checker.text(obj, "intro", "") if "intro" in obj else None,
        kinds=kinds,
        datasets=datasets,
    )


def _probe(checker: _Checker, items: list[object]) -> tuple[Question, ...]:
    """Parse the probe; an `open_answer` cannot be graded before the lesson starts."""
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("type") == OpenAnswer.type_name:
            checker.report(
                _at(_index("probe", index), "type"),
                "probe questions must be a closed type, not open_answer",
            )
    return tuple(
        question
        for question in _questions(checker, items, "probe")
        if not isinstance(question, OpenAnswer)
    )


def _plan(checker: _Checker, value: object, sections: tuple[Section, ...]) -> Plan:
    """Parse the plan; every `media` entry must name a type some section uses."""
    path = "plan"
    obj = checker.fields(
        value,
        path,
        frozenset(
            {
                "subject",
                "time_budget",
                "content",
                "media",
                "rejected",
                "rationale",
                "default_depth",
            }
        ),
    )
    subject = obj.get("subject", "change")
    if subject not in _SUBJECTS:
        checker.report(_at(path, "subject"), f"must be one of {', '.join(_SUBJECTS)}")
    budget = obj.get("time_budget", 30)
    if isinstance(budget, bool) or budget not in _TIME_BUDGETS:
        checker.report(_at(path, "time_budget"), "must be 5, 15 or 30")
    elif budget == 5 and len(sections) > 1:  # noqa: PLR2004
        checker.report(
            _at(path, "time_budget"),
            f"a 5-minute lesson has one section, this one has {len(sections)}",
        )
    content = checker.texts(obj, "content", path)
    for index, item in enumerate(content):
        if item not in _CONTENT:
            checker.report(
                _index(_at(path, "content"), index),
                f"must be one of {', '.join(_CONTENT)}",
            )
    if len(set(content)) != len(content):
        checker.report(_at(path, "content"), "lists the same item more than once")
    used = {
        item.type_name
        for section in sections
        for item in (*section.elements, *section.checkpoints)
    }
    media = checker.texts(obj, "media", path)
    if subject == "document":
        _check_document_media(checker, sections, media)
        used |= _CODE_ONLY
    for index, name in enumerate(media):
        if name not in used:
            checker.report(
                _index(_at(path, "media"), index),
                f"'{name}' is not used by any section",
            )
    return Plan(
        subject=cast("Subject", subject),
        time_budget=cast("int", budget),
        content=content,
        media=media,
        rejected=checker.texts(obj, "rejected", path),
        rationale=checker.texts(obj, "rationale", path, minimum=1),
        default_depth=_depth(checker, obj, path, key="default_depth"),
    )


def _check_document_media(
    checker: _Checker, sections: tuple[Section, ...], media: tuple[str, ...]
) -> None:
    """Report each code-only type in `media`, and each code-only item it leaves out."""
    for index, name in enumerate(media):
        if name in _CODE_ONLY:
            checker.report(_index("plan.media", index), _code_only_message(name))
    for i, section in enumerate(sections):
        items = [
            (f"sections[{i}].elements[{j - section.code_sugar}]", element)
            for j, element in enumerate(section.elements)
        ] + [
            (f"sections[{i}].checkpoints[{j}]", question)
            for j, question in enumerate(section.checkpoints)
        ]
        for item_path, item in items:
            if item.type_name in _CODE_ONLY and item.type_name not in media:
                checker.report(
                    _at(item_path, "type"), _code_only_message(item.type_name)
                )


def _code_only_message(name: str) -> str:
    return f"'{name}' is code-only and cannot appear in a document lesson"


def _section(checker: _Checker, value: object, path: str) -> Section:
    """Parse a section; a `code` field is sugar for a leading code element."""
    obj = checker.fields(
        value,
        path,
        frozenset({"id", "title", "body", "checkpoints"}),
        frozenset({"code", "elements"}),
    )
    ident = checker.identifier(obj, "id", path)
    checker.claim_id("section", ident, path)
    elements: list[Element] = []
    sugar = _optional_code(checker, obj, path)
    if sugar:
        elements.append(CodeElement(code=sugar))
    where = _at(path, "elements")
    for index, item in enumerate(checker.array(obj, "elements", path)):
        element = _element(checker, item, _index(where, index))
        if element is not None:
            elements.append(element)
    section = Section(
        id=ident,
        title=checker.text(obj, "title", path),
        body=checker.text(obj, "body", path),
        elements=tuple(elements),
        code_sugar=sugar is not None,
        checkpoints=_questions(
            checker,
            checker.array(obj, "checkpoints", path, minimum=1, maximum=2),
            _at(path, "checkpoints"),
        ),
    )
    _check_traces(checker, section, path)
    _check_spikes(checker, section, path)
    _check_diagrams(checker, section, path)
    _check_videos(checker, section, path)
    if any(isinstance(e, ViewElement) for e in section.elements) or any(
        isinstance(q, (SelectItems, FillTable)) for q in section.checkpoints
    ):
        from grokcheck import views  # noqa: PLC0415

        section = views.check_section(checker, section, path)
    return section


def _check_diagrams(checker: _Checker, section: Section, path: str) -> None:
    if section.checkpoints:
        return
    for index, element in enumerate(section.elements):
        if isinstance(element, DiagramElement):
            checker.report(
                _index(_at(path, "elements"), index - section.code_sugar),
                "a diagram needs a checkpoint in its section",
            )


def _check_videos(checker: _Checker, section: Section, path: str) -> None:
    if section.checkpoints:
        return
    for index, element in enumerate(section.elements):
        if isinstance(element, VideoElement):
            checker.report(
                _index(_at(path, "elements"), index - section.code_sugar),
                "a video needs a checkpoint in its section",
            )


def _check_traces(checker: _Checker, section: Section, path: str) -> None:
    """Check that each `predict_state` and `gate` points into this section's traces.

    A gate must name a `predict_state` checkpoint on its own trace, so the
    step it reveals is the step the reader predicted.
    """
    traces = {e.trace_id: e for e in section.elements if isinstance(e, TraceElement)}
    predictions = {q.id: q for q in section.checkpoints if isinstance(q, PredictState)}
    for index, question in enumerate(section.checkpoints):
        if not isinstance(question, PredictState) or question.trace_id is None:
            continue
        version = question.version or 0
        where = _index(_at(path, "checkpoints"), index)
        trace = traces.get(question.trace_id)
        if trace is None:
            checker.report(
                _at(where, "trace_id"),
                f"no trace element '{question.trace_id}' in this section",
            )
        elif not 0 <= version < len(trace.versions):
            checker.report(
                _at(where, "version"),
                f"version {version} is out of range for"
                f" {len(trace.versions)} version(s)",
            )
        elif not 0 <= question.step < len(trace.versions[version].steps):
            checker.report(
                _at(where, "step"),
                f"step {question.step} is out of range for"
                f" {len(trace.versions[version].steps)} step(s)",
            )
    for index, element in enumerate(section.elements):
        if not isinstance(element, TraceElement) or element.gate is None:
            continue
        where = _at(_index(_at(path, "elements"), index - section.code_sugar), "gate")
        gate = predictions.get(element.gate)
        if gate is None or gate.trace_id != element.trace_id:
            checker.report(
                where,
                f"must name a predict_state checkpoint of this section on trace"
                f" '{element.trace_id}'",
            )
        checker.claim_id("gate", element.gate, where)


def _check_spikes(checker: _Checker, section: Section, path: str) -> None:
    """Check that each spike's gate is a `predict_output` checkpoint matching its run.

    The gate's correct option, or one of its accepted answers, must equal the
    recorded stdout once both are normalised, so the gate never grades the
    run's own output as wrong.
    """
    from grokcheck.grading import normalise  # noqa: PLC0415

    checkpoints = {q.id: (i, q) for i, q in enumerate(section.checkpoints)}
    for index, element in enumerate(section.elements):
        if not isinstance(element, SpikeElement):
            continue
        where = _at(_index(_at(path, "elements"), index - section.code_sugar), "gate")
        checker.claim_id("gate", element.gate, where)
        at, gate = checkpoints.get(element.gate, (0, None))
        if not isinstance(gate, PredictOutput):
            checker.report(
                where, "must name a predict_output checkpoint of this section"
            )
            continue
        stdout = element.result.get("stdout")
        if not isinstance(stdout, str):
            continue
        recorded = normalise(stdout)
        gate_path = _index(_at(path, "checkpoints"), at)
        if gate.correct is None:
            if recorded not in {normalise(answer) for answer in gate.accepted}:
                checker.report(
                    _at(gate_path, "accepted"),
                    f"no accepted answer matches the recorded output '{recorded}'",
                )
        elif (
            0 <= gate.correct < len(gate.options)
            and normalise(gate.options[gate.correct].text) != recorded
        ):
            checker.report(
                _at(gate_path, "correct"),
                f"option {gate.correct} does not match the recorded output"
                f" '{recorded}'",
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
    language = checker.text(obj, "language", path)
    cited = _cited_lines(checker, obj, path)
    if cited is None:
        return None
    file, start, lines = cited
    return CodeBlock(
        language=language
        or _LANGUAGES.get(PurePosixPath(file).suffix.lower(), "plaintext"),
        text="\n".join(lines),
        file=file,
        start_line=start,
    )


def _cited_lines(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[str, int, list[str]] | None:
    """Read the `lines` span of the project file `file` named in `obj`.

    Returns the file's project-relative POSIX path, the span's first line
    number, and the span's lines.
    """
    relative = checker.text(obj, "file", path)
    span = _line_span(checker, obj, path)
    if not relative:
        return None
    read = _read_project_file(checker, relative, _at(path, "file"))
    if read is None:
        return None
    resolved, file_lines = read
    if span is None:
        return None
    start, end = span
    if end > len(file_lines):
        checker.report(
            _at(path, "lines"),
            f"ends at line {end} but '{relative}' has {len(file_lines)} lines",
        )
        return None
    file = resolved.relative_to(checker.project_root).as_posix()
    return file, start, file_lines[start - 1 : end]


def _read_project_file(
    checker: _Checker, relative: str, where: str
) -> tuple[Path, list[str]] | None:
    """Return the resolved path and lines of `relative`, reporting at `where` if not."""
    resolved = (checker.project_root / relative).resolve()
    if not resolved.is_relative_to(checker.project_root):
        checker.report(where, f"'{relative}' resolves outside the project root")
        return None
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        checker.report(where, f"cannot read '{relative}' as UTF-8 text: {exc}")
        return None
    return resolved, lines


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
class _ElementStem:
    depth: Depth
    claims: tuple[Claim, ...]


class _Typed(Protocol):
    type_name: ClassVar[str]


_StemT = TypeVar("_StemT")
_ParsedT = TypeVar("_ParsedT", bound=_Typed)


@dataclass(frozen=True)
class _Format(Generic[_StemT, _ParsedT]):
    """How to parse one `type` of question or element beyond its common fields."""

    cls: type[_ParsedT]
    required: frozenset[str]
    optional: frozenset[str]
    parse: Callable[[_Checker, dict[str, object], str, _StemT], _ParsedT]


def _typed_fields(
    checker: _Checker,
    value: object,
    path: str,
    formats: Mapping[str, _Format[_StemT, _ParsedT]],
    common: tuple[frozenset[str], frozenset[str]],
) -> tuple[_Format[_StemT, _ParsedT], dict[str, object]] | None:
    """Look up the format named by `value["type"]` and check fields against it.

    `common` holds the required and optional fields every format shares.
    """
    if not isinstance(value, dict):
        checker.report(path, "must be an object")
        return None
    kind = value.get("type")
    found = formats.get(kind) if isinstance(kind, str) else None
    if found is None:
        checker.report(
            _at(path, "type"), f"must be one of {', '.join(sorted(formats))}"
        )
        return None
    required, optional = common
    obj = checker.fields(
        value,
        path,
        frozenset({"type"}) | required | found.required,
        optional | found.optional,
    )
    return found, obj


def _element(checker: _Checker, value: object, path: str) -> Element | None:
    typed = _typed_fields(
        checker,
        value,
        path,
        _ELEMENTS,
        (frozenset(), frozenset({"depth", "claims"})),
    )
    if typed is None:
        return None
    element_format, obj = typed
    stem = _ElementStem(
        depth=_depth(checker, obj, path), claims=_claims(checker, obj, path)
    )
    return element_format.parse(checker, obj, path, stem)


def _claims(checker: _Checker, obj: dict[str, object], path: str) -> tuple[Claim, ...]:
    where = _at(path, "claims")
    claims: list[Claim] = []
    for index, item in enumerate(checker.array(obj, "claims", path)):
        claim_path = _index(where, index)
        fields = checker.fields(
            item, claim_path, frozenset({"text", "backing"}), frozenset({"verified"})
        )
        text = checker.text(fields, "text", claim_path)
        verified = _verdict(checker, fields, claim_path, "claim")
        backing = (
            _backing(checker, fields["backing"], _at(claim_path, "backing"))
            if "backing" in fields
            else None
        )
        if backing is not None:
            page = (
                page_of(checker.project_root / backing.file, backing.lines[0])
                if isinstance(backing, LinesBacking) and _is_source(backing.file)
                else None
            )
            claims.append(Claim(text, backing, verified, page))
    return tuple(claims)


def _verdict(
    checker: _Checker, fields: dict[str, object], path: str, noun: str
) -> Verdict:
    """Return the `verified` field of `fields`, reporting a contradicted or bad one."""
    verified = fields.get("verified", "unchecked")
    if verified == "contradicted":
        checker.report(_at(path, "verified"), f"a contradicted {noun} cannot be served")
    elif verified not in _VERDICTS:
        checker.report(_at(path, "verified"), f"must be one of {', '.join(_VERDICTS)}")
    return cast("Verdict", verified)


def _backing(checker: _Checker, value: object, path: str) -> Backing | None:  # noqa: PLR0911
    """Parse a backing, whose kind is the one `BACKING_CLASSES` key it holds."""
    if not isinstance(value, dict):
        checker.report(path, "must be an object")
        return None
    kinds = [key for key in BACKING_CLASSES if key in value]
    if len(kinds) != 1:
        checker.report(
            path, f"must hold exactly one of {', '.join(map(repr, BACKING_CLASSES))}"
        )
        return None
    match kinds[0]:
        case "file":
            return _lines_backing(checker, value, path)
        case "url":
            obj = checker.fields(value, path, frozenset({"url", "version"}))
            return SourceBacking(
                checker.text(obj, "url", path), checker.text(obj, "version", path)
            )
        case "spike_id":
            obj = checker.fields(value, path, frozenset({"spike_id"}))
            spike_id = checker.identifier(obj, "spike_id", path)
            spike_dir = checker.project_root / ".grokcheck" / "spikes" / spike_id
            if spike_id and not (spike_dir / "result.json").is_file():
                checker.report(
                    _at(path, "spike_id"),
                    f"no recorded result for spike '{spike_id}'"
                    " (run `grokcheck spike run`)",
                )
            return SpikeBacking(spike_id)
        case "data":
            from grokcheck.views import data_backing  # noqa: PLC0415

            return data_backing(checker, value, path)
    obj = checker.fields(value, path, frozenset({"reason"}))
    return Unverified(checker.text(obj, "reason", path))


def _lines_backing(checker: _Checker, value: object, path: str) -> LinesBacking | None:
    obj = checker.fields(
        value, path, frozenset({"file", "lines"}), frozenset({"symbol", "quote"})
    )
    symbol = checker.text(obj, "symbol", path) or None
    quote = checker.text(obj, "quote", path) or None
    cited = _cited_lines(checker, obj, path)
    if cited is None:
        return None
    file, start, lines = cited
    end = start + len(lines) - 1
    if symbol and symbol not in "\n".join(lines):
        checker.report(
            _at(path, "symbol"),
            f"'{symbol}' does not appear in lines {start}-{end} of '{file}'",
        )
    if quote is None and _is_source(file):
        checker.report(
            _at(path, "quote"), "a citation into .grokcheck/sources needs a quote"
        )
    elif quote and " ".join(quote.split()) not in " ".join(" ".join(lines).split()):
        shown = (
            PurePosixPath(file).relative_to(SOURCES_DIR) if _is_source(file) else file
        )
        checker.report(
            _at(path, "quote"),
            f"quote '{quote}' is not in lines {start}-{end} of '{shown}'",
        )
    return LinesBacking(file=file, lines=(start, end), symbol=symbol, quote=quote)


def _is_source(file: str) -> bool:
    return PurePosixPath(file).is_relative_to(SOURCES_DIR)


def _depth(
    checker: _Checker, obj: dict[str, object], path: str, key: str = "depth"
) -> Depth:
    match obj.get(key, "short"):
        case "short":
            return "short"
        case "detail":
            return "detail"
    checker.report(_at(path, key), "must be 'short' or 'detail'")
    return "short"


def _prose(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    return ProseElement(**vars(stem), markdown=checker.text(obj, "markdown", path))


def _code_element(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    return CodeElement(
        **vars(stem),
        code=_optional_code(checker, obj, path)
        or CodeBlock(language="plaintext", text=""),
        caption=checker.text(obj, "caption", path),
    )


def _diff(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse a hunk; every note and ask must point at a line of its new side."""
    lines = _diff_lines(checker, obj, path)
    new_start = _line_number(checker, obj, "new_start", path)
    new_end = new_start + sum(line.op != "-" for line in lines) - 1
    notes: list[DiffNote] = []
    for index, item in enumerate(checker.array(obj, "notes", path)):
        note_path = _index(_at(path, "notes"), index)
        fields = checker.fields(item, note_path, frozenset({"lines", "cite", "text"}))
        span = _line_span(checker, fields, note_path)
        if span and not new_start <= span[0] <= span[1] <= new_end:
            checker.report(
                _at(note_path, "lines"),
                f"{span[0]}-{span[1]} is not on the new side"
                f" ({new_start} to {new_end})",
            )
        cite = (
            _lines_backing(checker, fields["cite"], _at(note_path, "cite"))
            if "cite" in fields
            else None
        )
        text = checker.text(fields, "text", note_path)
        if span and cite:
            notes.append(DiffNote(span, cite, text))
    asks: list[LineAsk] = []
    for index, item in enumerate(checker.array(obj, "asks", path)):
        ask_path = _index(_at(path, "asks"), index)
        fields = checker.fields(
            item, ask_path, frozenset({"line", "question", "answer"})
        )
        line = checker.integer(fields.get("line"), _at(ask_path, "line"))
        if line is not None and not new_start <= line <= new_end:
            checker.report(
                _at(ask_path, "line"),
                f"line {line} is not on the new side ({new_start} to {new_end})",
            )
        asks.append(
            LineAsk(
                line=line or 0,
                question=checker.text(fields, "question", ask_path),
                answer=checker.text(fields, "answer", ask_path),
            )
        )
    return DiffElement(
        **vars(stem),
        file=checker.text(obj, "file", path),
        old_start=_line_number(checker, obj, "old_start", path),
        new_start=new_start,
        lines=lines,
        notes=tuple(notes),
        asks=tuple(asks),
    )


def _vocab(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse terms; lines must lie in the code, and `library_term` is all or none."""
    code = _optional_code(checker, obj, path)
    owners = _owners(checker, obj, path) if "owners" in obj else None
    known_owners = tuple(owners) if owners is not None else _OWNERS
    count = len(code.text.splitlines()) if code else 0
    items = checker.array(obj, "terms", path, minimum=1)
    translated = any(isinstance(i, dict) and "library_term" in i for i in items)
    terms: list[Term] = []
    for index, item in enumerate(items):
        term_path = _index(_at(path, "terms"), index)
        fields = checker.fields(
            item,
            term_path,
            frozenset({"term", "owner", "definition"}),
            frozenset({"lines", "detail", "library_term", "false_friend"}),
        )
        owner = fields.get("owner", "ours")
        if owner not in known_owners:
            checker.report(
                _at(term_path, "owner"), f"must be one of {', '.join(known_owners)}"
            )
        lines: list[int] = []
        for at, value in enumerate(checker.array(fields, "lines", term_path)):
            line_path = _index(_at(term_path, "lines"), at)
            line = checker.integer(value, line_path)
            if line is not None and not 1 <= line <= count:
                checker.report(
                    line_path, f"line {line} is outside the code (1 to {count})"
                )
            lines.append(line or 0)
        if translated and "library_term" not in fields:
            checker.report(
                _at(term_path, "library_term"),
                "is required in a translation table; write null for no equivalent",
            )
        library_term = (
            None
            if fields.get("library_term") is None
            else checker.text(fields, "library_term", term_path)
        )
        terms.append(
            Term(
                term=checker.text(fields, "term", term_path),
                owner=cast("str", owner),
                definition=checker.text(fields, "definition", term_path),
                lines=tuple(lines),
                detail=checker.text(fields, "detail", term_path),
                library_term=library_term,
                false_friend=checker.flag(
                    fields, "false_friend", term_path, default=False
                ),
            )
        )
    min_opened = checker.integer(obj.get("min_opened", 3), _at(path, "min_opened"))
    if min_opened is not None and not 1 <= min_opened <= len(items):
        checker.report(
            _at(path, "min_opened"),
            f"must be between 1 and the number of terms ({len(items)})",
        )
    return VocabElement(
        **vars(stem),
        code=code,
        terms=tuple(terms),
        min_opened=min_opened or 1,
        owners=owners,
    )


def _owners(checker: _Checker, obj: dict[str, object], path: str) -> dict[str, str]:
    """Parse the owner legend, each label mapped to one of `_OWNER_COLOURS`."""
    where = _at(path, "owners")
    value = obj["owners"]
    if not isinstance(value, dict) or not value:
        checker.report(where, "must be a non-empty object")
        return {}
    for label, colour in value.items():
        if not label.strip():
            checker.report(where, "labels must be non-empty strings")
        if colour not in _OWNER_COLOURS:
            checker.report(
                _at(where, label), f"must be one of {', '.join(_OWNER_COLOURS)}"
            )
    return cast("dict[str, str]", value)


def _diff_lines(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[DiffLine, ...]:
    lines: list[DiffLine] = []
    for index, item in enumerate(checker.array(obj, "lines", path, minimum=1)):
        line_path = _index(_at(path, "lines"), index)
        fields = checker.fields(item, line_path, frozenset({"op", "text"}))
        op, text = fields.get("op", " "), fields.get("text", "")
        if op not in _DIFF_OPS:
            checker.report(_at(line_path, "op"), "must be ' ', '+' or '-'")
        if not isinstance(text, str):
            checker.report(_at(line_path, "text"), "must be a string")
        elif op in _DIFF_OPS:
            lines.append(DiffLine(op, text))
    return tuple(lines)


def _line_number(checker: _Checker, obj: dict[str, object], key: str, path: str) -> int:
    number = checker.integer(obj.get(key, 0), _at(path, key))
    if number is not None and number < 0:
        checker.report(_at(path, key), "must be 0 or more")
    return number or 0


def _question(checker: _Checker, value: object, path: str) -> Question | None:
    typed = _typed_fields(
        checker,
        value,
        path,
        _FORMATS,
        (frozenset({"id", "prompt"}), frozenset({"explanation"})),
    )
    if typed is None:
        return None
    question_format, obj = typed
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
    checker: _Checker,
    obj: dict[str, object],
    path: str,
    key: str = "options",
    minimum: int = 2,
) -> tuple[Option, ...]:
    where = _at(path, key)
    options: list[Option] = []
    for index, item in enumerate(checker.array(obj, key, path, minimum=minimum)):
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


def _predict_state(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    """Parse a predict_state on a trace (`trace_id`, `version`) or a steps `view`."""
    options = _options(checker, obj, path)
    on_view = "view" in obj
    if on_view in {"trace_id" in obj, "version" in obj}:
        checker.report(
            path, "needs either 'trace_id' with 'version', or 'view', but not both"
        )
    if on_view:
        from grokcheck.views import needs_v3  # noqa: PLC0415

        needs_v3(checker, _at(path, "view"), "predict_state on a view")
    return PredictState(
        **dataclasses.asdict(stem),
        code=_optional_code(checker, obj, path),
        trace_id=checker.identifier(obj, "trace_id", path) or None,
        version=checker.integer(obj.get("version"), _at(path, "version")) or 0
        if "version" in obj
        else None,
        view=checker.identifier(obj, "view", path) or None,
        step=checker.integer(obj.get("step"), _at(path, "step")) or 0,
        options=options,
        correct=_option_index(
            checker, obj.get("correct"), _at(path, "correct"), len(options)
        ),
    )


def _mutation(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[Mutation, str | None]:
    """Parse `mutation`; also return its line as the project has it now, if read."""
    where = _at(path, "mutation")
    fields = checker.fields(
        obj.get("mutation"),
        where,
        frozenset({"file", "line"}),
        frozenset({"replacement"}),
    )
    replacement = fields.get("replacement")
    if replacement is not None and not isinstance(replacement, str):
        checker.report(_at(where, "replacement"), "must be a string or null")
        replacement = None
    number = checker.integer(fields.get("line", 1), _at(where, "line"))
    line = 1 if number is None else number
    relative = checker.text(fields, "file", where)
    mutation = Mutation(relative, line, replacement)
    read = (
        _read_project_file(checker, relative, _at(where, "file")) if relative else None
    )
    if read is None:
        return mutation, None
    _, lines = read
    if not 1 <= line <= len(lines):
        checker.report(
            _at(where, "line"),
            f"line {line} is outside '{relative}' (1 to {len(lines)})",
        )
        return mutation, None
    return mutation, lines[line - 1]


def _mutation_quiz(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    """Parse a mutation quiz; at least one listed test must fail on the mutant."""
    mutation, _ = _mutation(checker, obj, path)
    where = _at(path, "tests")
    tests: list[TestCase] = []
    for index, item in enumerate(checker.array(obj, "tests", path, minimum=1)):
        test_path = _index(where, index)
        fields = checker.fields(item, test_path, frozenset({"name", "fails"}))
        tests.append(
            TestCase(
                name=checker.text(fields, "name", test_path),
                fails=checker.flag(fields, "fails", test_path, default=False),
            )
        )
    names = [test.name for test in tests]
    if len(set(names)) != len(names):
        checker.report(where, "lists the same test more than once")
    if tests and not any(test.fails for test in tests):
        checker.report(where, "needs at least one test that fails on the mutant")
    return MutationQuiz(
        **dataclasses.asdict(stem),
        mutation=mutation,
        tests=tuple(tests),
        log=checker.text(obj, "log", path),
    )


def _fix_the_bug(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    """Parse a fix-the-bug question.

    `mutation.replacement` must be the project's current text of the line, and
    `worktree` an existing directory outside the project.
    """
    mutation, original = _mutation(checker, obj, path)
    replacement_path = _at(_at(path, "mutation"), "replacement")
    if "mutation" in obj and mutation.replacement is None:
        checker.report(replacement_path, "is required: the original line to restore")
    elif original is not None and mutation.replacement != original:
        checker.report(
            replacement_path,
            f"must be line {mutation.line} of '{mutation.file}' as it is now:"
            f" {original!r}",
        )
    worktree = checker.text(obj, "worktree", path)
    location = Path(worktree)
    if worktree and (
        not location.is_absolute()
        or location.resolve().is_relative_to(checker.project_root)
    ):
        checker.report(
            _at(path, "worktree"), "must be an absolute path outside the project"
        )
    elif worktree and not location.is_dir():
        checker.report(
            _at(path, "worktree"),
            f"'{worktree}' does not exist; plant it with `grokcheck mutate`",
        )
    return FixTheBug(
        **dataclasses.asdict(stem),
        mutation=mutation,
        worktree=worktree,
        test_command=checker.texts(obj, "test_command", path, minimum=1),
    )


def _change_impact(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    candidates = _options(checker, obj, path, "candidates")
    where = _at(path, "affected")
    affected = tuple(
        _option_index(checker, item, _index(where, index), len(candidates))
        for index, item in enumerate(checker.array(obj, "affected", path, minimum=1))
    )
    if len(set(affected)) != len(affected):
        checker.report(where, "lists the same candidate more than once")
    return ChangeImpact(
        **dataclasses.asdict(stem),
        code=_optional_code(checker, obj, path),
        change=checker.text(obj, "change", path),
        candidates=candidates,
        affected=affected,
    )


def _parsons(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    """Parse a Parsons problem; every line and distractor text must be distinct."""
    where = _at(path, "lines")
    lines: list[ParsonsLine] = []
    for index, item in enumerate(checker.array(obj, "lines", path, minimum=2)):
        line_path = _index(where, index)
        fields = checker.fields(item, line_path, frozenset({"text", "indent"}))
        indent = checker.integer(fields.get("indent", 0), _at(line_path, "indent"))
        if indent is not None and indent < 0:
            checker.report(_at(line_path, "indent"), "must not be negative")
        lines.append(ParsonsLine(checker.text(fields, "text", line_path), indent or 0))
    distractors = _options(checker, obj, path, "distractors", minimum=1)
    texts = [line.text for line in lines] + [d.text for d in distractors]
    if len(set(texts)) != len(texts):
        checker.report(
            path, "must not repeat a line or distractor text, or its place is ambiguous"
        )
    return Parsons(
        **dataclasses.asdict(stem), lines=tuple(lines), distractors=distractors
    )


def _trace(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse a trace; each version's steps overlay steps of a recorded run.

    A version reads `.grokcheck/traces/<trace_id>.json`, the element's
    `trace_id` unless the version names its own. Authored step `i` overlays
    recorded step `at`, which defaults to `i`.
    """
    trace_id = checker.identifier(obj, "trace_id", path)
    checker.claim_id("trace", trace_id, path)
    panels = _panels(checker, obj, path)
    versions = tuple(
        _trace_version(
            checker,
            item,
            _index(_at(path, "versions"), index),
            trace_id,
            {panel.id for panel in panels},
        )
        for index, item in enumerate(checker.array(obj, "versions", path, minimum=1))
    )
    gate = checker.identifier(obj, "gate", path) or None
    return TraceElement(
        **vars(stem),
        trace_id=trace_id,
        title=checker.text(obj, "title", path),
        versions=versions,
        panels=panels,
        gate=gate,
    )


def _panels(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[StatePanel, ...]:
    panels: list[StatePanel] = []
    for index, item in enumerate(checker.array(obj, "panels", path, minimum=1)):
        panel_path = _index(_at(path, "panels"), index)
        fields = checker.fields(item, panel_path, frozenset({"id", "label", "kind"}))
        ident = checker.identifier(fields, "id", panel_path)
        if ident in _SECRET_FIELDS or ident in {p.id for p in panels}:
            checker.report(
                _at(panel_path, "id"), f"'{ident}' is reserved or already used"
            )
        kind = fields.get("kind", "status")
        if kind not in _PANEL_KINDS:
            checker.report(
                _at(panel_path, "kind"), f"must be one of {', '.join(_PANEL_KINDS)}"
            )
        panels.append(
            StatePanel(
                ident,
                checker.text(fields, "label", panel_path),
                cast("PanelKind", kind),
            )
        )
    return tuple(panels)


def _trace_version(
    checker: _Checker, value: object, path: str, trace_id: str, panels: set[str]
) -> TraceVersion:
    obj = checker.fields(
        value, path, frozenset({"label", "code", "steps"}), frozenset({"trace_id"})
    )
    recorded = _recorded_lines(
        checker, checker.identifier(obj, "trace_id", path) or trace_id, path
    )
    steps: list[TraceStep] = []
    for index, item in enumerate(checker.array(obj, "steps", path, minimum=1)):
        step_path = _index(_at(path, "steps"), index)
        fields = checker.fields(
            item, step_path, frozenset({"narration", "state"}), frozenset({"at"})
        )
        at = checker.integer(fields.get("at", index), _at(step_path, "at"))
        if recorded is not None and at is not None and not 0 <= at < len(recorded):
            checker.report(
                _at(step_path, "at"),
                f"recorded step {at} does not exist ({len(recorded)} recorded)",
            )
            at = None
        state = fields.get("state", {})
        if not isinstance(state, dict):
            checker.report(_at(step_path, "state"), "must be an object")
            state = {}
        for key in sorted(state.keys() - panels):
            checker.report(_at(_at(step_path, "state"), key), "is not a panel id")
        steps.append(
            TraceStep(
                cur_line=recorded[at] if recorded and at is not None else None,
                state=state,
                narration=checker.texts(fields, "narration", step_path, minimum=1),
            )
        )
    return TraceVersion(
        label=checker.text(obj, "label", path),
        code=_optional_code(checker, obj, path)
        or CodeBlock(language="plaintext", text=""),
        steps=tuple(steps),
    )


def _recorded_lines(
    checker: _Checker, trace_id: str, path: str
) -> list[int | None] | None:
    """Return the line each step of recording `trace_id` was on, or None if unread."""
    if not trace_id:
        return None
    relative = f".grokcheck/traces/{trace_id}.json"
    try:
        data = json.loads((checker.project_root / relative).read_text("utf-8"))
        steps = Trace.from_json(data).steps
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checker.report(
            path,
            f"cannot read recording '{relative}' (run `grokcheck trace record`): {exc}",
        )
        return None
    return [
        next(
            (e.line for e in reversed([s.event, *s.lines]) if e.event == "line"),
            None,
        )
        for s in steps
    ]


def _spike(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse a spike from the `result.json` and `spike.py` that `spike run` wrote."""
    spike_id = checker.identifier(obj, "spike_id", path)
    spike_dir = checker.project_root / ".grokcheck" / "spikes" / spike_id
    spec = ("hypothesis", "deps", "exclude_newer")
    try:
        record = json.loads((spike_dir / "result.json").read_text("utf-8"))
        script = (spike_dir / "spike.py").read_text("utf-8")
        hypothesis, pins, exclude_newer = (record[key] for key in spec)
        result = {key: value for key, value in record.items() if key not in spec}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        checker.report(
            _at(path, "spike_id"),
            f"cannot read spike '{spike_id}' (run `grokcheck spike run`): {exc}",
        )
        hypothesis, pins, exclude_newer, script, result = "", {}, "", "", {}
    else:
        if not isinstance(result.get("stdout"), str):
            checker.report(
                _at(path, "spike_id"), f"spike '{spike_id}' has no recorded stdout"
            )
    return SpikeElement(
        **vars(stem),
        spike_id=spike_id,
        hypothesis=hypothesis,
        code=CodeBlock(language="python", text=script),
        pins=pins,
        exclude_newer=exclude_newer,
        result=result,
        gate=checker.identifier(obj, "gate", path),
    )


def _diagram(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    mermaid = checker.text(obj, "mermaid", path)
    for message in diagrams.check(mermaid):
        checker.report(_at(path, "mermaid"), message)
    kind = obj.get("kind", "flowchart")
    if kind not in _DIAGRAM_KINDS:
        checker.report(_at(path, "kind"), f"must be one of {', '.join(_DIAGRAM_KINDS)}")
    return DiagramElement(
        **vars(stem),
        mermaid=mermaid,
        caption=checker.text(obj, "caption", path),
        legend=checker.text(obj, "legend", path),
        kind=cast("DiagramKind", kind),
    )


def _video(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    ident = checker.identifier(obj, "id", path)
    checker.claim_id("video", ident, path)
    duration = obj.get("duration", 0)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        checker.report(_at(path, "duration"), "must be a number of seconds")
        duration = 0
    elif duration <= 0:
        checker.report(_at(path, "duration"), "must be positive")
    return VideoElement(
        **vars(stem),
        id=ident,
        src=_media_file(checker, obj, "src", path),
        captions=_media_file(checker, obj, "captions", path),
        duration=float(duration),
        transcript=checker.text(obj, "transcript", path),
    )


def _media_file(checker: _Checker, obj: dict[str, object], key: str, path: str) -> str:
    """Read `obj[key]`, an absolute path to a file in the lesson dir or video cache."""
    text = checker.text(obj, key, path)
    if not text:
        return ""
    file = Path(text)
    if not file.is_absolute():
        checker.report(_at(path, key), "must be an absolute path")
        return text
    resolved = file.resolve()
    if not any(
        resolved.is_relative_to(root.resolve())
        for root in (checker.lesson_dir, VIDEO_CACHE_DIR)
    ):
        checker.report(
            _at(path, key), "is outside the lesson directory and the video cache"
        )
    elif not resolved.is_file():
        checker.report(_at(path, key), f"'{text}' does not exist")
    return str(resolved)


def _options_element(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse an options table, which must hold every baseline standing."""
    ident = checker.identifier(obj, "id", path)
    checker.claim_id("element", ident, path)
    rows = tuple(
        _option_row(checker, item, _index(_at(path, "options"), index))
        for index, item in enumerate(checker.array(obj, "options", path, minimum=3))
    )
    standings = {row.standing for row in rows}
    missing = [s for s in _STANDINGS if s != "other" and s not in standings]
    if missing:
        checker.report(
            _at(path, "options"), f"needs an option standing {', '.join(missing)}"
        )
    return OptionsElement(
        **vars(stem),
        id=ident,
        question=checker.text(obj, "question", path),
        criteria=checker.texts(obj, "criteria", path, minimum=1),
        options=rows,
        reader_first=checker.flag(obj, "reader_first", path, default=True),
    )


def _option_row(checker: _Checker, value: object, path: str) -> OptionRow:
    obj = checker.fields(
        value,
        path,
        frozenset({"name", "costs", "assumes", "standing"}),
        frozenset({"sketch", "constraints"}),
    )
    standing = obj.get("standing", "other")
    if standing not in _STANDINGS:
        checker.report(_at(path, "standing"), f"must be one of {', '.join(_STANDINGS)}")
        standing = "other"
    constraints: list[OptionConstraint] = []
    for index, item in enumerate(checker.array(obj, "constraints", path)):
        where = _index(_at(path, "constraints"), index)
        fields = checker.fields(
            item, where, frozenset({"text", "hard"}), frozenset({"waivable_by"})
        )
        hard = checker.flag(fields, "hard", where, default=False)
        waivable_by = checker.text(fields, "waivable_by", where) or None
        if hard and waivable_by:
            checker.report(
                _at(where, "waivable_by"), "a hard constraint cannot be waived"
            )
        constraints.append(
            OptionConstraint(checker.text(fields, "text", where), hard, waivable_by)
        )
    return OptionRow(
        name=checker.text(obj, "name", path),
        costs=checker.text(obj, "costs", path),
        assumes=checker.text(obj, "assumes", path),
        standing=cast("Standing", standing),
        sketch=_code(checker, obj["sketch"], _at(path, "sketch"))
        if "sketch" in obj
        else None,
        constraints=tuple(constraints),
    )


def _assumptions(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse assumptions; `id` shares the question ids, since it is answered."""
    ident = checker.identifier(obj, "id", path)
    checker.claim_id("question", ident, path)
    items: list[Assumption] = []
    for index, item in enumerate(checker.array(obj, "items", path, minimum=1)):
        where = _index(_at(path, "items"), index)
        fields = checker.fields(
            item,
            where,
            frozenset({"claim", "backing"}),
            frozenset({"checked_by_spike", "verified"}),
        )
        verified = _verdict(checker, fields, where, "assumption")
        spike_id = checker.identifier(fields, "checked_by_spike", where) or None
        spikes = checker.project_root / ".grokcheck" / "spikes"
        if spike_id and not (spikes / spike_id / "result.json").is_file():
            checker.report(
                _at(where, "checked_by_spike"),
                f"no recorded result for spike '{spike_id}'"
                " (run `grokcheck spike run`)",
            )
        backing = (
            _backing(checker, fields["backing"], _at(where, "backing"))
            if "backing" in fields
            else None
        )
        if backing is not None:
            items.append(
                Assumption(
                    checker.text(fields, "claim", where),
                    backing,
                    spike_id,
                    verified,
                )
            )
    return AssumptionsElement(**vars(stem), id=ident, items=tuple(items))


def _playground(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    """Parse a playground whose `states` hold every input combination exactly once."""
    inputs = _playground_inputs(checker, obj, path)
    _check_unique(checker, [i.id for i in inputs], _at(path, "inputs"))
    input_ids = frozenset(i.id for i in inputs)
    constraints: list[Constraint] = []
    for where, fields in _objects(
        checker,
        checker.array(obj, "constraints", path),
        _at(path, "constraints"),
        {"after", "gt"},
    ):
        for key in ("after", "gt"):
            if fields.get(key) not in input_ids:
                checker.report(_at(where, key), "must name an input id")
        constraints.append(
            Constraint(
                checker.text(fields, "after", where), checker.text(fields, "gt", where)
            )
        )
    presets: list[Preset] = []
    for where, fields in _objects(
        checker,
        checker.array(obj, "presets", path),
        _at(path, "presets"),
        {"label", "values"},
    ):
        values = checker.fields(
            fields.get("values", {}), _at(where, "values"), input_ids
        )
        on_grid: dict[str, int] = {}
        for item in inputs:
            value = values.get(item.id)
            if isinstance(value, int) and value in _grid(item):
                on_grid[item.id] = value
            elif item.id in values:
                checker.report(
                    _at(_at(where, "values"), item.id), "is not a value of its slider"
                )
        presets.append(Preset(checker.text(fields, "label", where), on_grid))
    rows = tuple(
        Row(
            checker.identifier(fields, "id", where),
            checker.text(fields, "label", where),
        )
        for where, fields in _objects(
            checker,
            checker.array(obj, "rows", path, 1),
            _at(path, "rows"),
            {"id", "label"},
        )
    )
    row_ids = [row.id for row in rows]
    _check_unique(checker, row_ids, _at(path, "rows"))
    variants = checker.texts(obj, "variants", path, minimum=1)
    for index, variant in enumerate(variants):
        if variant not in row_ids:
            checker.report(_index(_at(path, "variants"), index), "must name a row id")
    legend = tuple(
        LegendEntry(
            checker.text(fields, "cell", where),
            checker.text(fields, "label", where),
            _one_of(checker, fields, "colour", where, _OWNER_COLOURS),
        )
        for where, fields in _objects(
            checker,
            checker.array(obj, "legend", path, 1),
            _at(path, "legend"),
            {"cell", "label", "colour"},
        )
    )
    ticks = _at_least(checker, obj, "ticks", path, 1)
    table = _PlaygroundTable(
        tuple(row_ids), variants, ticks, frozenset(e.cell for e in legend) | {""}
    )
    states = _playground_states(checker, obj, path, inputs, table)
    tasks = tuple(
        PlaygroundTask(
            checker.text(fields, "text", where),
            tuple(
                _condition(checker, condition, at, variants)
                for at, condition in _objects(
                    checker,
                    checker.array(fields, "when", where, 1),
                    _at(where, "when"),
                    {"variant", "outcome"},
                    {"min_lost"},
                )
            ),
        )
        for where, fields in _objects(
            checker,
            checker.array(obj, "tasks", path),
            _at(path, "tasks"),
            {"text", "when"},
        )
    )
    return PlaygroundElement(
        **vars(stem),
        inputs=inputs,
        constraints=tuple(constraints),
        presets=tuple(presets),
        variants=variants,
        ticks=ticks,
        rows=rows,
        legend=legend,
        states=states,
        tasks=tasks,
    )


@dataclass(frozen=True)
class _PlaygroundTable:
    rows: tuple[str, ...]
    variants: tuple[str, ...]
    ticks: int
    cells: frozenset[str]


def _playground_inputs(
    checker: _Checker, obj: dict[str, object], path: str
) -> tuple[PlaygroundInput, ...]:
    inputs: list[PlaygroundInput] = []
    for where, fields in _objects(
        checker,
        checker.array(obj, "inputs", path, 1),
        _at(path, "inputs"),
        {"id", "label", "min", "max"},
        {"step", "value"},
    ):
        ident = checker.identifier(fields, "id", where)
        low = checker.integer(fields.get("min", 0), _at(where, "min"))
        low = 0 if low is None else low
        high = checker.integer(fields.get("max", low), _at(where, "max"))
        high = low if high is None else high
        if high < low:
            checker.report(_at(where, "max"), f"must be at least min ({low})")
            high = low
        item = PlaygroundInput(
            ident,
            checker.text(fields, "label", where),
            low,
            high,
            _at_least(checker, fields, "step", where, 1),
            low,
        )
        value = fields.get("value", low)
        if value not in _grid(item):
            checker.report(_at(where, "value"), "is not a value of its slider")
        elif isinstance(value, int):
            item = dataclasses.replace(item, value=value)
        inputs.append(item)
    return tuple(inputs)


def _check_unique(checker: _Checker, ids: list[str], path: str) -> None:
    for index, ident in enumerate(ids):
        if ident and ident in ids[:index]:
            checker.report(_at(_index(path, index), "id"), f"'{ident}' is already used")


def _grid(item: PlaygroundInput) -> range:
    return range(item.min, item.max + 1, item.step)


def _playground_states(
    checker: _Checker,
    obj: dict[str, object],
    path: str,
    inputs: tuple[PlaygroundInput, ...],
    table: _PlaygroundTable,
) -> tuple[PlaygroundState, ...]:
    """Parse the state table, reporting each input combination it lacks."""
    where = _at(path, "states")
    raw = obj.get("states", {})
    if not isinstance(raw, dict):
        checker.report(where, "must be an object")
        return ()
    expected = [
        ",".join(map(str, combination))
        for combination in itertools.product(*(_grid(item) for item in inputs))
    ]
    for key in expected:
        if key not in raw:
            checker.report(where, f"missing state for inputs {key}")
    states: list[PlaygroundState] = []
    for key, value in raw.items():
        state_path = _at(where, key)
        if key not in expected:
            checker.report(state_path, "is not a combination of input values")
            continue
        fields = checker.fields(
            value, state_path, frozenset({"cells", "outcomes", "explain"})
        )
        cells = checker.fields(
            fields.get("cells", {}), _at(state_path, "cells"), frozenset(table.rows)
        )
        outcomes = checker.fields(
            fields.get("outcomes", {}),
            _at(state_path, "outcomes"),
            frozenset(table.variants),
        )
        states.append(
            PlaygroundState(
                key=key,
                cells=tuple(
                    _row_cells(
                        checker,
                        cells.get(row),
                        _at(_at(state_path, "cells"), row),
                        table,
                    )
                    for row in table.rows
                ),
                outcomes=tuple(
                    _variant_outcome(
                        checker,
                        outcomes.get(variant),
                        _at(_at(state_path, "outcomes"), variant),
                    )
                    for variant in table.variants
                ),
                explain=checker.text(fields, "explain", state_path),
            )
        )
    return tuple(states)


def _row_cells(
    checker: _Checker, value: object, path: str, table: _PlaygroundTable
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != table.ticks:
        checker.report(path, f"must be an array of {table.ticks} cell(s)")
        return ()
    for index, cell in enumerate(value):
        if cell not in table.cells:
            checker.report(_index(path, index), "must be '' or a legend cell")
    return tuple(cell for cell in value if isinstance(cell, str))


def _variant_outcome(checker: _Checker, value: object, path: str) -> VariantOutcome:
    fields = checker.fields(
        value or {}, path, frozenset({"outcome"}), frozenset({"lost"})
    )
    return VariantOutcome(
        _one_of(checker, fields, "outcome", path, _OUTCOMES),
        _at_least(checker, fields, "lost", path, 0),
    )


def _condition(
    checker: _Checker, fields: dict[str, object], path: str, variants: tuple[str, ...]
) -> Condition:
    variant = checker.text(fields, "variant", path)
    if variant not in variants:
        checker.report(_at(path, "variant"), "must name one of the variants")
    return Condition(
        variant,
        _one_of(checker, fields, "outcome", path, _OUTCOMES),
        _at_least(checker, fields, "min_lost", path, 0),
    )


def _objects(
    checker: _Checker,
    items: list[object],
    path: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Iterator[tuple[str, dict[str, object]]]:
    """Yield each object's path and checked fields from the array at `path`."""
    for index, item in enumerate(items):
        where = _index(path, index)
        yield (
            where,
            checker.fields(item, where, frozenset(required), frozenset(optional or ())),
        )


def _at_least(
    checker: _Checker, obj: dict[str, object], key: str, path: str, minimum: int
) -> int:
    """Read optional integer `obj[key]`, defaulting to and never below `minimum`."""
    value = checker.integer(obj.get(key, minimum), _at(path, key))
    if value is None:
        return minimum
    if value < minimum:
        checker.report(_at(path, key), f"must be at least {minimum}")
        return minimum
    return value


_ChoiceT = TypeVar("_ChoiceT", bound=str)


def _one_of(
    checker: _Checker,
    obj: dict[str, object],
    key: str,
    path: str,
    choices: tuple[_ChoiceT, ...],
) -> _ChoiceT:
    value = obj.get(key, choices[0])
    if value not in choices:
        checker.report(_at(path, key), f"must be one of {', '.join(choices)}")
        return choices[0]
    return value


def _view(
    checker: _Checker, obj: dict[str, object], path: str, stem: _ElementStem
) -> Element:
    from grokcheck.views import parse_view  # noqa: PLC0415

    return parse_view(checker, obj, path, stem)


def _select_items(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    from grokcheck.views import parse_select_items  # noqa: PLC0415

    return parse_select_items(checker, obj, path, stem)


def _fill_table(
    checker: _Checker, obj: dict[str, object], path: str, stem: _Stem
) -> Question:
    from grokcheck.views import parse_fill_table  # noqa: PLC0415

    return parse_fill_table(checker, obj, path, stem)


def _registry(
    *formats: _Format[_StemT, _ParsedT],
) -> dict[str, _Format[_StemT, _ParsedT]]:
    return {f.cls.type_name: f for f in formats}


_ELEMENTS: dict[str, _Format[_ElementStem, Element]] = _registry(
    _Format(ProseElement, frozenset({"markdown"}), frozenset(), _prose),
    _Format(CodeElement, frozenset({"code"}), frozenset({"caption"}), _code_element),
    _Format(
        DiffElement,
        frozenset({"file", "old_start", "new_start", "lines"}),
        frozenset({"notes", "asks"}),
        _diff,
    ),
    _Format(
        VocabElement,
        frozenset({"terms"}),
        frozenset({"code", "min_opened", "owners"}),
        _vocab,
    ),
    _Format(
        TraceElement,
        frozenset({"trace_id", "title", "versions", "panels"}),
        frozenset({"gate"}),
        _trace,
    ),
    _Format(SpikeElement, frozenset({"spike_id", "gate"}), frozenset(), _spike),
    _Format(
        PlaygroundElement,
        frozenset({"inputs", "variants", "ticks", "rows", "legend", "states"}),
        frozenset({"constraints", "presets", "tasks"}),
        _playground,
    ),
    _Format(
        OptionsElement,
        frozenset({"id", "question", "criteria", "options"}),
        frozenset({"reader_first"}),
        _options_element,
    ),
    _Format(AssumptionsElement, frozenset({"id", "items"}), frozenset(), _assumptions),
    _Format(
        DiagramElement,
        frozenset({"mermaid", "caption"}),
        frozenset({"legend", "kind"}),
        _diagram,
    ),
    _Format(
        VideoElement,
        frozenset({"id", "src", "captions", "duration", "transcript"}),
        frozenset(),
        _video,
    ),
    _Format(
        ViewElement,
        frozenset({"id", "layout", "caption"}),
        _VIEW_OPTIONAL,
        _view,
    ),
)

_FORMATS: dict[str, _Format[_Stem, Question]] = _registry(
    _Format(
        SingleChoice,
        frozenset({"options", "correct"}),
        frozenset({"code"}),
        _single_choice,
    ),
    _Format(
        MultipleChoice,
        frozenset({"options", "correct"}),
        frozenset({"code"}),
        _multiple_choice,
    ),
    _Format(
        OpenAnswer,
        frozenset({"model_answer", "rubric"}),
        frozenset({"code"}),
        _open_answer,
    ),
    _Format(
        PredictOutput,
        frozenset({"code"}),
        frozenset({"options", "correct", "accepted"}),
        _predict_output,
    ),
    _Format(PickLine, frozenset({"code", "answer_lines"}), frozenset(), _pick_line),
    _Format(OrderSteps, frozenset({"steps"}), frozenset({"code"}), _order_steps),
    _Format(
        FillBlank, frozenset({"text", "blanks"}), frozenset({"language"}), _fill_blank
    ),
    _Format(
        PredictState,
        frozenset({"step", "options", "correct"}),
        frozenset({"code", "trace_id", "version", "view"}),
        _predict_state,
    ),
    _Format(
        MutationQuiz,
        frozenset({"mutation", "tests", "log"}),
        frozenset(),
        _mutation_quiz,
    ),
    _Format(
        FixTheBug,
        frozenset({"mutation", "worktree", "test_command"}),
        frozenset(),
        _fix_the_bug,
    ),
    _Format(
        ChangeImpact,
        frozenset({"change", "candidates", "affected"}),
        frozenset({"code"}),
        _change_impact,
    ),
    _Format(Parsons, frozenset({"lines", "distractors"}), frozenset(), _parsons),
    _Format(SelectItems, frozenset({"view", "answer"}), frozenset(), _select_items),
    _Format(FillTable, frozenset({"view"}), frozenset(), _fill_table),
)

QUESTION_CLASSES: dict[str, type[Question]] = {
    name: question_format.cls for name, question_format in _FORMATS.items()
}
ELEMENT_CLASSES: dict[str, type[Element]] = {
    name: element_format.cls for name, element_format in _ELEMENTS.items()
}
