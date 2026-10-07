"""The offline HTML export: grading that matches the server, and a closed page."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from grokcheck.export_html import _embedded, _script_safe, answer_key, render_html
from grokcheck.grading import ResponseError, grade, summarise
from grokcheck.lesson import load_lesson
from grokcheck.run import LessonRun

if TYPE_CHECKING:
    from grokcheck.grading import Grade
    from grokcheck.lesson import Lesson, Question

FIXTURES = Path(__file__).parent.parent / "fixtures"
_URL = r"(?:https?:)?//[\w.-]+\.[a-z]{2,}"
WEB_DIR = Path(__file__).parents[2] / "skills" / "grokcheck" / "web"
HARNESS = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const [web, cases] = process.argv.slice(2);
const { grade, summarise } = await import(pathToFileURL(`${web}/grading.js`));
const graded = [];
const results = JSON.parse(readFileSync(cases, "utf8")).map((item) => {
  try {
    const result = grade(item.question, item.response, item.confidence);
    graded.push(result);
    return result;
  } catch {
    return null;
  }
});
console.log(JSON.stringify({ results, summary: summarise(graded) }));
"""


def _fixture(name: str = "valid_full.json") -> Lesson:
    return load_lesson(FIXTURES / "lessons" / name, FIXTURES / "project")


def _responses(question: Question) -> list[object]:  # noqa: C901, PLR0911
    """Correct, partial, wrong and malformed responses to `question`."""
    key = json.loads(json.dumps(answer_key(question)))
    match key["type"]:
        case "single_choice" | "predict_state" | "predict_output" if isinstance(
            key.get("correct"), int
        ):
            count = len(key["options"])
            return [key["correct"], (key["correct"] + 1) % count, count, True, "0"]
        case "predict_output":
            accepted = key["accepted"][0]
            return [accepted, f"  {accepted}\n", accepted.lower(), "“x”", 5]
        case "multiple_choice" | "change_impact":
            correct = key.get("correct", key.get("affected"))
            count = len(key.get("options", key.get("candidates")))
            wrong = [i for i in range(count) if i not in correct]
            return [correct, correct[:1], wrong, [], [*correct, *wrong], [count], "x"]
        case "pick_line":
            lines = key["answer_lines"]
            return [lines, [*lines, 1], [1], [0], 3]
        case "mutation_quiz":
            names = [test["name"] for test in key["tests"]]
            failing = [test["name"] for test in key["tests"] if test["fails"]]
            return [failing, names, [], [1], "x"]
        case "fix_the_bug":
            return [{"passed": True}, {"passed": False}, {}, {"passed": 1}]
        case "parsons":
            lines = [dict(line) for line in key["lines"]]
            decoy = {"text": key["distractors"][0]["text"], "indent": 0}
            flat = [{**line, "indent": 0} for line in lines]
            return [
                lines,
                lines[::-1],
                flat,
                lines[:2],
                [*lines, decoy],
                [lines[0], lines[0]],
                [{"text": lines[0]["text"]}],
                [{"text": "unknown", "indent": 0}],
            ]
        case "order_steps":
            steps = key["steps"]
            return [steps, steps[::-1], steps[1:] + steps[:1], steps[:-1], [1, 2, 3]]
        case "fill_blank":
            blank = key["blanks"][0]
            accepted = blank["accepted"][0]
            return [
                {blank["id"]: accepted},
                accepted,
                {blank["id"]: f" {accepted.replace('=', ' = ')} "},
                {blank["id"]: accepted.upper()},
                {blank["id"]: "nope"},
                {},
                [accepted],
            ]
        case "select_items":
            cells = key["answer_cells"]
            others = [c for c in key["candidates"] if c not in cells]
            return [cells, cells[:1], [*cells, others[0]], [], ["nope"], "x", [1]]
        case "fill_table":
            right: dict[str, dict[str, object]] = key["cells"]
            first = next(iter(right))
            field = next(iter(right[first]))
            off = {cell: dict(row) for cell, row in right.items()}
            off[first][field] = "nope"
            short = {cell: dict(row) for cell, row in right.items()}
            del short[first][field]
            return [right, off, short, {}, {first: "x"}, [right]]
        case "open_answer":
            size = len(key["rubric"])
            return [
                {"text": "a", "met": [True] * size},
                {"text": "a", "met": [True] + [False] * (size - 1)},
                {"text": "a", "met": [False] * size},
                {"text": "a", "met": [True]},
                {"text": "a"},
            ]
    pytest.fail(f"no responses for {key['type']}")


def _python(question: Question, response: Any, confidence: Any) -> Grade | None:  # noqa: ANN401
    try:
        return grade(question, response, confidence)
    except ResponseError:
        return None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("fixture", ["valid_full.json", "valid_views.json"])
def test_browser_grading_matches_the_server_for_every_question_type(
    tmp_path: Path, fixture: str
) -> None:
    """Every fixture question grades the same in web/grading.js as in Python.

    An export grades in the browser; a drift would score the same answer
    differently offline than in a served lesson.
    """
    lesson = _fixture(fixture)
    questions = [
        *lesson.probe,
        *(q for section in lesson.sections for q in section.checkpoints),
        *lesson.final,
    ]
    cases = [
        (question, response, confidence)
        for question in questions
        for response, confidence in zip(
            _responses(question), ["sure", "unsure", "guess", None] * 3, strict=False
        )
    ]
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(
        json.dumps(
            [
                {"question": answer_key(q), "response": r, "confidence": c}
                for q, r, c in cases
            ]
        ),
        encoding="utf-8",
    )
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    node = subprocess.run(  # noqa: S603
        [shutil.which("node") or "node", str(harness), str(WEB_DIR), str(cases_file)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    browser = json.loads(node.stdout)
    python = [_python(q, r, c) for q, r, c in cases]

    mismatches = [
        (question.id, response, expected, got)
        for (question, response, _), grade_, got in zip(
            cases, python, browser["results"], strict=True
        )
        if (
            expected := None
            if grade_ is None
            else json.loads(json.dumps(dataclasses.asdict(grade_)))
        )
        != got
    ]
    if mismatches:
        pytest.fail(f"(id, response, python, browser) that differ: {mismatches}")
    expected_summary = dataclasses.asdict(
        summarise(grade_ for grade_ in python if grade_ is not None)
    )
    expected = json.loads(json.dumps(expected_summary))
    expected["final_score"] = pytest.approx(expected["final_score"])
    if expected != browser["summary"]:
        pytest.fail(f"summary {browser['summary']}, expected {expected_summary}")


def test_the_exported_page_loads_nothing_from_the_network(tmp_path: Path) -> None:
    """The page carries every script, style and font inline, and names the lesson.

    Beyond the lesson's own citations, only the vendored libraries mention web
    addresses, in comments and messages; the page itself loads none.
    """
    lesson = _fixture()
    page = render_html(LessonRun.create(lesson, tmp_path), WEB_DIR).text
    own = page
    for vendored in (
        "highlight/highlight.min.js",
        "highlight/github.min.css",
        "highlight/github-dark.min.css",
        "mermaid/mermaid.tiny.js",
    ):
        text = (WEB_DIR / "vendor" / vendored).read_text("utf-8")
        own = own.replace(_script_safe(text), "")
    raw = (FIXTURES / "lessons" / "valid_full.json").read_text("utf-8")
    cited = set(re.findall(_URL, raw))
    resources = re.findall(r"<link|@import|(?:src|href)=[\"']?(?!data:)\w", own)

    foreign = set(re.findall(_URL, own)) - cited - {"http://www.w3.org"}
    if foreign or resources:
        pytest.fail(f"external addresses {foreign}, loaded resources {resources}")
    if lesson.title not in page or not page.startswith("<!doctype html>"):
        pytest.fail("the page is not a full document naming the lesson")


def test_a_fragment_starts_with_its_title_and_style(tmp_path: Path) -> None:
    """A fragment leaves the document tags to the host page that wraps it."""
    run = LessonRun.create(_fixture(), tmp_path)
    page = render_html(run, WEB_DIR, fragment=True).text

    markup = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.DOTALL)
    if not page.startswith("<title>") or page.index("<style>") > page.index("<script"):
        pytest.fail("a fragment should open with its <title> and <style>")
    if found := re.search(r"<(?:!doctype|html|head|body)\b", markup, re.IGNORECASE):
        pytest.fail(f"a fragment should carry no document tag, found {found[0]}")


def test_the_export_carries_masked_views_and_their_gated_payloads(
    tmp_path: Path,
) -> None:
    """Offline, the page unmasks a view from `gates` once its checkpoint is graded."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", project)
    lesson = load_lesson(FIXTURES / "lessons" / "valid_views.json", project)

    data = _embedded(LessonRun.create(lesson, project))

    gates = cast("dict[str, dict[str, Any]]", data["gates"])
    if {gate: payload["view"] for gate, payload in gates.items()} != {
        "cp-walk": "v-walk",
        "cp-lost": "v-drop2",
        "cp-kinds": "v-rules",
        "cp-taken": "v-read2",
    }:
        pytest.fail(f"gates {sorted(gates)}")
    keys = cast("dict[str, dict[str, Any]]", data["keys"])
    if list(keys["cp-lost"]["answer_cells"]) != ["new|client|3", "new|client|4"]:
        pytest.fail(f"answer key {keys['cp-lost']}")
    sections = cast("dict[str, Any]", data["lesson"])["sections"]
    masked = [
        item
        for section in sections
        for element in section["elements"]
        if element["id"] == "v-drop2"
        for item in element["items"]
        if item.get("_masked")
    ]
    if len(masked) != 5:  # noqa: PLR2004
        pytest.fail(f"masked cells in the export {masked}")
