"""The published lesson schema and the Python validator draw the same line."""

import json
from pathlib import Path

import pytest
from grokcheck.lesson import LessonError, load_lesson
from jsonschema import Draft202012Validator

FIXTURES = Path(__file__).parent.parent / "fixtures"
SCHEMA = (
    Path(__file__).parent.parent.parent
    / "skills"
    / "grokcheck"
    / "schema"
    / "lesson.schema.json"
)
LESSONS = sorted((FIXTURES / "lessons").glob("*.json"))

# These fixtures break only rules a JSON Schema cannot state (unique ids, index
# ranges, files on disk), so the schema must accept what the validator rejects.
SEMANTIC_ONLY = frozenset({"invalid_many_errors.json"})


def _schema_accepts(lesson: Path) -> bool:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    document = json.loads(lesson.read_text(encoding="utf-8"))
    return bool(Draft202012Validator(schema).is_valid(document))


def _validator_accepts(lesson: Path) -> bool:
    try:
        load_lesson(lesson, FIXTURES / "project")
    except LessonError:
        return False
    return True


@pytest.mark.parametrize("lesson", LESSONS, ids=lambda lesson: lesson.name)
def test_schema_and_validator_agree_on_every_fixture(lesson: Path) -> None:
    """A lesson the schema accepts is one `load_lesson` accepts, and vice versa.

    Agents and editors check lessons against the schema before `serve` runs
    the validator, so any disagreement is a lesson that looks right and then
    fails, or a defect the schema waves through.
    """
    schema_valid = _schema_accepts(lesson)
    validator_valid = _validator_accepts(lesson)

    if lesson.name in SEMANTIC_ONLY:
        if not schema_valid or validator_valid:
            pytest.fail(
                f"{lesson.name}: expected only the schema to accept it, got"
                f" schema={schema_valid} validator={validator_valid}"
            )
    elif schema_valid != validator_valid:
        side = "schema" if schema_valid else "validator"
        pytest.fail(f"{lesson.name}: only the {side} accepts it")
