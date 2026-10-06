"""The grounding pass: the evidence a subagent sees, and its verdicts written back."""

import json
import shutil
from pathlib import Path

import pytest
from grokcheck import data
from grokcheck.data import DataError
from grokcheck.ground import apply_verdicts, claim_at, claim_hash, manifest, unsettled
from grokcheck.lesson import LessonError, load_lesson


def _choice(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "type": "single_choice",
        "prompt": "Which?",
        "options": [{"text": "a", "why": "first"}, {"text": "b", "why": "second"}],
        "correct": 0,
    }


def _lesson_file(project: Path, backings: list[dict[str, object]]) -> Path:
    claims = [
        {"text": f"Claim {n}.", "backing": backing}
        for n, backing in enumerate(backings)
    ]
    raw = {
        "schema_version": 2,
        "title": "Grounded",
        "scope": {"summary": "One module.", "files": ["pkg/a.py"]},
        "sections": [
            {
                "id": "only",
                "title": "Only",
                "body": "Lead prose.",
                "elements": [
                    {"type": "prose", "markdown": "Claims.", "claims": claims}
                ],
                "checkpoints": [_choice("cp-1")],
            }
        ],
        "final": [_choice("f-1"), _choice("f-2"), _choice("f-3")],
    }
    lesson_file = project / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")
    return lesson_file


def test_manifest_carries_cited_lines_and_verdicts_rewrite_lesson(
    tmp_path: Path,
) -> None:
    """The subagent sees exactly the cited lines, and its refutation blocks serving."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(
        "import os\n\ndef frobnicate():\n    return 1\n", encoding="utf-8"
    )
    lesson_file = _lesson_file(
        tmp_path, [{"file": "pkg/a.py", "lines": [3, 4], "symbol": "frobnicate"}]
    )

    entries = manifest(load_lesson(lesson_file, tmp_path), tmp_path)
    if entries[0]["evidence"] != "def frobnicate():\n    return 1":
        pytest.fail(f"evidence is {entries[0]['evidence']!r}")

    claim_id = entries[0]["claim_id"]
    verdict = {"claim_id": claim_id, "verdict": "contradicted", "note": "returns 2"}
    apply_verdicts(lesson_file, [verdict])
    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, tmp_path)
    paths = [problem.path for problem in caught.value.problems]
    if len(paths) != 1 or not paths[0].endswith(".verified"):
        pytest.fail(f"problem paths are {paths}")


def test_manifest_carries_spike_stdout_for_spike_backing(tmp_path: Path) -> None:
    """A spike claim is judged on its recorded stdout."""
    spike_dir = tmp_path / ".grokcheck" / "spikes" / "sum"
    spike_dir.mkdir(parents=True)
    (spike_dir / "result.json").write_text(
        json.dumps({"stdout": "42\n", "returncode": 0}), encoding="utf-8"
    )
    lesson_file = _lesson_file(tmp_path, [{"spike_id": "sum"}])

    entries = manifest(load_lesson(lesson_file, tmp_path), tmp_path)

    if entries[0]["evidence"] != "42\n":
        pytest.fail(f"evidence is {entries[0]['evidence']!r}")


def test_assumption_backing_is_grounded_and_refuted_like_a_claim(
    tmp_path: Path,
) -> None:
    """An assumption is judged on its evidence, and a refuted one blocks serving."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("LIMIT = 10\n", encoding="utf-8")
    lesson_file = _lesson_file(tmp_path, [])
    raw = json.loads(lesson_file.read_text(encoding="utf-8"))
    raw["sections"][0]["elements"] = [
        {
            "type": "assumptions",
            "id": "assume-1",
            "items": [
                {
                    "claim": "The limit is 10.",
                    "backing": {"file": "pkg/a.py", "lines": [1, 1]},
                }
            ],
        }
    ]
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    entries = manifest(load_lesson(lesson_file, tmp_path), tmp_path)
    if [(e["claim_id"], e["evidence"]) for e in entries] != [
        ("sections[0].elements[0].items[0]", "LIMIT = 10")
    ]:
        pytest.fail(f"manifest is {entries}")

    verdict = {"claim_id": entries[0]["claim_id"], "verdict": "contradicted"}
    apply_verdicts(lesson_file, [verdict])
    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, tmp_path)
    paths = [problem.path for problem in caught.value.problems]
    if paths != ["sections[0].elements[0].items[0].verified"]:
        pytest.fail(f"problem paths are {paths}")


def _views_project(tmp_path: Path) -> Path:
    fixtures = Path(__file__).parents[1] / "fixtures"
    project = tmp_path / "project"
    shutil.copytree(fixtures / "project", project)
    shutil.copy(fixtures / "lessons" / "valid_views.json", project / "lesson.json")
    return project


def test_manifest_shows_data_evidence_and_authored_rows(tmp_path: Path) -> None:
    """A view note is judged on its matched items; authored rows on their cites."""
    project = _views_project(tmp_path)

    entries = {
        e["claim_id"]: e
        for e in manifest(load_lesson(project / "lesson.json", project), project)
    }

    notes = [e for key, e in entries.items() if ".notes[" in key]
    if not notes:
        pytest.fail("no view note in the manifest")
    evidence = notes[0]["evidence"]
    if evidence["source"]["kind"] != "run" or evidence["source"]["cited_lines_run"] < 1:
        pytest.fail(f"evidence source is {evidence['source']}")
    if not evidence["matched"] or not all("_cell" in row for row in evidence["rows"]):
        pytest.fail(f"evidence rows are not the view's items: {evidence['rows'][:2]}")
    row = entries.get("data:terms.rows[1]")
    if row is None or "raise Beyond(cursor)" not in row["evidence"]:
        pytest.fail(f"authored row entry is {row}")
    if not row["text"].startswith("term=Beyond"):
        pytest.fail(f"authored row text is {row['text']!r}")


def test_row_verdicts_go_into_the_data_file(tmp_path: Path) -> None:
    """An authored row's verdict is kept in its dataset; contradicted refuses it."""
    project = _views_project(tmp_path)
    lesson_file = project / "lesson.json"

    apply_verdicts(
        lesson_file,
        [{"claim_id": "data:terms.rows[1]", "verdict": "contradicted"}],
        project,
    )

    path = project / ".grokcheck" / "data" / "terms.json"
    source = json.loads(path.read_text("utf-8"))["source"]
    if source["verdicts"] != {"1": "contradicted"} or set(
        source["verified_hashes"]
    ) != {"1"}:
        pytest.fail("the verdict and its hash did not reach the data file")
    with pytest.raises(DataError, match="contradicted"):
        data.load(project, "terms")
    with pytest.raises(LessonError):
        apply_verdicts(
            lesson_file,
            [{"claim_id": "data:terms.rows[9]", "verdict": "supported"}],
            project,
        )


def _supported_lesson(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("LIMIT = 10\n", encoding="utf-8")
    lesson_file = _lesson_file(
        tmp_path,
        [{"file": "pkg/a.py", "lines": [1, 1]}, {"file": "pkg/a.py", "lines": [1, 1]}],
    )
    claim_id = manifest(load_lesson(lesson_file, tmp_path), tmp_path)[0]["claim_id"]
    apply_verdicts(lesson_file, [{"claim_id": claim_id, "verdict": "supported"}])
    return lesson_file, claim_id


def test_a_verdict_stores_the_hash_of_what_was_judged(tmp_path: Path) -> None:
    """The verdict carries the hash of the claim's text, backing and evidence."""
    lesson_file, claim_id = _supported_lesson(tmp_path)

    entry = manifest(load_lesson(lesson_file, tmp_path), tmp_path)[0]
    claim = claim_at(json.loads(lesson_file.read_text("utf-8")), claim_id)
    if claim is None or claim.get("verified_hash") != claim_hash(entry):
        pytest.fail(f"stored claim is {claim}")


def test_unsettled_lists_only_unjudged_or_changed_claims(tmp_path: Path) -> None:
    """A claim judged on unchanged evidence drops out; a changed one returns."""
    lesson_file, _ = _supported_lesson(tmp_path)

    def listed() -> list[str]:
        entries = manifest(load_lesson(lesson_file, tmp_path), tmp_path)
        return [e["claim_id"] for e in unsettled(entries, lesson_file, tmp_path)]

    if listed() != ["sections[0].elements[0].claims[1]"]:
        pytest.fail(f"listed {listed()}")
    (tmp_path / "pkg" / "a.py").write_text("LIMIT = 20\n", encoding="utf-8")
    if listed() != [f"sections[0].elements[0].claims[{k}]" for k in (0, 1)]:
        pytest.fail(f"after the evidence changed, listed {listed()}")


def test_a_stale_verdict_is_treated_as_unchecked(tmp_path: Path) -> None:
    """A contradicted claim rewritten since its verdict validates as unchecked."""
    lesson_file, claim_id = _supported_lesson(tmp_path)
    apply_verdicts(lesson_file, [{"claim_id": claim_id, "verdict": "contradicted"}])
    with pytest.raises(LessonError):
        load_lesson(lesson_file, tmp_path)

    raw = json.loads(lesson_file.read_text("utf-8"))
    raw["sections"][0]["elements"][0]["claims"][0]["text"] = "The limit is 10."
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    claim = next(iter(load_lesson(lesson_file, tmp_path).claims()))[1]
    if claim.verified != "unchecked":
        pytest.fail(f"verified is {claim.verified}")
