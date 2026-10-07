import sys
from itertools import islice
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from stream import Beyond, Log, read, read_old

TURNS = [
    ("prompt", "Which hosts are down?"),
    ("answer", "Two hosts are down."),
    ("prompt", "Which ones?"),
    ("thinking", "Look up the down hosts."),
    ("answer", "web01 and db02."),
]

running = bool(INPUTS["running"])
log = Log(events=TURNS[:3], pending=TURNS[3:]) if running else Log(events=TURNS)
reader = read if INPUTS["version"] == "new" else read_old

for frame in islice(reader(log, 0, running=False), INPUTS["drop"]):
    emit(
        lane="client",
        seq=frame.cursor,
        event=str(frame.cursor),
        kind=frame.kind,
        text=frame.text,
    )
try:
    for frame in reader(log, INPUTS["lid"], running=running):
        emit(
            lane="client",
            seq=frame.cursor,
            event=str(frame.cursor),
            kind=frame.kind,
            text=frame.text,
        )
except Beyond:
    emit(
        lane="client",
        seq=INPUTS["drop"] + 1,
        event="409",
        kind="error",
        text="409 Conflict",
    )

for seq, (kind, text) in enumerate(log.events, start=1):
    emit(lane="server", seq=seq, event=str(seq), kind=kind, text=text)
