from collections.abc import Iterator
from dataclasses import dataclass, field


class Beyond(Exception):
    pass


class AtEnd(Exception):
    pass


@dataclass(frozen=True)
class Frame:
    cursor: int
    kind: str
    text: str


@dataclass
class Log:
    events: list[tuple[str, str]] = field(default_factory=list)
    pending: list[tuple[str, str]] = field(default_factory=list)

    def append(self, kind: str, text: str) -> int:
        self.events.append((kind, text))
        return len(self.events)


def read(log: Log, cursor: int, *, running: bool) -> Iterator[Frame]:
    end = len(log.events)
    if cursor > end:
        raise Beyond(cursor)
    if cursor == end and not running:
        raise AtEnd(cursor)
    if running:
        return _follow(log, cursor)
    return _replay(log, cursor)


def read_old(log: Log, _cursor: int, *, running: bool) -> Iterator[Frame]:
    return _follow(log, 0) if running else _replay(log, 0)


def _replay(log: Log, cursor: int) -> Iterator[Frame]:
    for index in range(cursor, len(log.events)):
        kind, text = log.events[index]
        yield Frame(index + 1, kind, text)


def _follow(log: Log, cursor: int) -> Iterator[Frame]:
    yield from _replay(log, cursor)
    while log.pending:
        cursor = log.append(*log.pending.pop(0))
        yield Frame(cursor, *log.events[cursor - 1])
