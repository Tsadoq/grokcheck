"""The grounding pass: the evidence a subagent sees, and its verdicts written back."""

import json
from pathlib import Path

import pytest
from grokcheck.ground import apply_verdicts, manifest
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
