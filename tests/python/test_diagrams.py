"""Diagram checks: size, edge labels, and a question to pair each diagram with."""

import json
from pathlib import Path

import pytest
from grokcheck import diagrams
from grokcheck.lesson import LessonError, load_lesson

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _diagram(mermaid: str) -> dict[str, str]:
    return {"type": "diagram", "mermaid": mermaid, "caption": "How it flows."}


def test_diagram_checks_node_count_edge_labels_and_paired_question(
    tmp_path: Path,
) -> None:
    """Oversized, unlabelled and unquestioned diagrams are each refused at their path.

    A diagram past twelve nodes is too much to hold at once, an unlabelled edge
    leaves the reader to guess the relation, and a diagram no checkpoint asks
    about is skimmed rather than read.
    """
    raw = json.loads((FIXTURES / "lessons" / "valid_full.json").read_text("utf-8"))
    valid = raw["sections"][1]["elements"][2]["mermaid"]
    chain = "\n".join(f"  n{i} -->|next| n{i + 1}" for i in range(13))
    raw["sections"][1]["elements"][2]["mermaid"] = f"flowchart TD\n{chain}"
    raw["sections"][2]["elements"].append(_diagram(f"{valid}\n  A --> B"))
    raw["sections"][3]["elements"] = [_diagram(valid)]
    raw["sections"][3]["checkpoints"] = []
    lesson_file = tmp_path / "lesson.json"
    lesson_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LessonError) as caught:
        load_lesson(lesson_file, FIXTURES / "project")

    found = sorted((p.path, p.message) for p in caught.value.problems)
    expected = [
        ("sections[1].elements[2].mermaid", "14 nodes; split the diagram (max 12)"),
        ("sections[2].elements[3].mermaid", "edge A --> B has no label"),
        ("sections[3].checkpoints", "needs at least 1 item(s), has 0"),
        ("sections[3].elements[0]", "a diagram needs a checkpoint in its section"),
    ]
    if found != expected:
        pytest.fail(f"problems are {found}")
    if diagrams.check(valid) != []:
        pytest.fail(f"the fixture diagram is refused: {diagrams.check(valid)}")
