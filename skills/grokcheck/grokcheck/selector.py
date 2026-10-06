"""The JSON selector grammar that picks dataset rows and view items.

`web/select.js` implements `matches` with the same semantics; both run the
vectors in `tests/fixtures/selectors.json`.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

MAX_DEPTH = 8
MAX_CLAUSES = 64
LOGIC = ("and", "or", "not")

_RANGES: dict[str, Callable[[float, float], bool]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}


def same(left: object, right: object) -> bool:
    """Type-strict equality: `True` never equals `1`, `1` equals `1.0`."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _number(left) and _number(right):
        return left == right
    if left is None or right is None:
        return left is right
    return isinstance(left, str) and isinstance(right, str) and left == right


def matches(selector: Mapping[str, object], item: Mapping[str, object]) -> bool:
    """Whether `item` satisfies every clause of `selector`; missing reads as null."""
    return all(_clause(key, want, item) for key, want in selector.items())


def _clause(key: str, want: object, item: Mapping[str, object]) -> bool:
    if key == "and":
        return all(matches(s, item) for s in _selectors(want))
    if key == "or":
        return any(matches(s, item) for s in _selectors(want))
    if key == "not":
        return not matches(want, item) if isinstance(want, dict) else False
    value = item.get(key)
    if isinstance(want, list):
        return any(same(value, w) for w in want)
    if isinstance(want, dict):
        return _number(value) and all(
            _number(bound) and _RANGES[op](value, bound)  # type: ignore[arg-type]
            for op, bound in want.items()
        )
    return same(value, want)


def _selectors(value: object) -> list[dict[str, object]]:
    return [s for s in value if isinstance(s, dict)] if isinstance(value, list) else []


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _scalar(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def problems(selector: object, fields: Collection[str]) -> list[tuple[str, str]]:
    """Return `(relative path, message)` for every defect; empty when valid."""
    found: list[tuple[str, str]] = []
    clauses = _check(selector, fields, "", 1, found)
    if clauses > MAX_CLAUSES:
        found.append(("", f"holds {clauses} clauses, at most {MAX_CLAUSES} allowed"))
    return found


def _check(
    selector: object,
    fields: Collection[str],
    path: str,
    depth: int,
    found: list[tuple[str, str]],
) -> int:
    if not isinstance(selector, dict):
        found.append((path, "must be an object"))
        return 0
    if depth > MAX_DEPTH:
        found.append((path, f"nests deeper than {MAX_DEPTH} levels"))
        return 0
    clauses = len(selector)
    for key, want in selector.items():
        where = f"{path}.{key}" if path else key
        if key in ("and", "or"):
            if not isinstance(want, list) or not want:
                found.append((where, "must be a non-empty list of selectors"))
                continue
            for index, inner in enumerate(want):
                clauses += _check(inner, fields, f"{where}[{index}]", depth + 1, found)
        elif key == "not":
            clauses += _check(want, fields, where, depth + 1, found)
        elif key not in fields:
            known = ", ".join(sorted(fields)) or "none"
            found.append((where, f"'{key}' is not a field (fields: {known})"))
        else:
            _check_value(want, where, found)
    return clauses


def _check_value(want: object, where: str, found: list[tuple[str, str]]) -> None:
    if isinstance(want, list):
        if not want:
            found.append((where, "must be a non-empty list"))
        elif not all(_scalar(w) for w in want):
            found.append(
                (where, "a list may hold only strings, numbers, booleans or null")
            )
    elif isinstance(want, dict):
        if not want:
            found.append((where, "a range needs at least one of gt, gte, lt or lte"))
        for op, bound in want.items():
            if op not in _RANGES:
                found.append((f"{where}.{op}", f"'{op}' is not gt, gte, lt or lte"))
            elif not _number(bound):
                found.append((f"{where}.{op}", "a range bound must be a number"))
    elif not _scalar(want):
        found.append((where, "must be a string, number, boolean or null"))
