"""Run one-question experiments against pinned packages, away from the project.

A spike is a PEP 723 script under `.grokcheck/spikes/<id>/`. Packages the
project already locks, or the user names, run on the host through an isolated
`uv` environment; any other package runs only inside a container, and with no
container runtime the spike is refused rather than run on the host.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any, Literal

from grokcheck.scope import normalise, pins

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    Fetch = Callable[[str], tuple[int, bytes]]

FETCH_TIMEOUT_SECONDS = 10
SPIKE_TIMEOUT_SECONDS = 600
RUNTIME_INFO_TIMEOUT_SECONDS = 10
RUNTIMES = ("podman", "docker")
CONTAINER_IMAGE = "ghcr.io/astral-sh/uv:python3.12-trixie-slim"

_TEMPLATE = Path(__file__).resolve().parent.parent / "references" / "spike-template.py"
_HTTP_OK = 200
_HTTP_NOT_FOUND = 404


class SpikeError(Exception):
    """A spike that cannot be written, run or checked as asked."""


@dataclass(frozen=True)
class Registry:
    """What PyPI says about a name; `latest` is `None` when it does not exist."""

    exists: bool
    latest: str | None
    normalised: str


@dataclass(frozen=True)
class SpikeResult:
    """One run of a spike; `where` is `host` or `container`, `duration` seconds."""

    stdout: str
    stderr: str
    returncode: int
    duration: float
    where: Literal["host", "container"]


@dataclass(frozen=True)
class Change:
    """A spike whose rerun no longer matches the output recorded in `result.json`."""

    spike_id: str
    recorded_stdout: str
    recorded_returncode: int
    stdout: str
    returncode: int


def _fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "grokcheck"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def check_name(name: str, fetch: Fetch = _fetch) -> Registry:
    """Ask PyPI whether `name` exists; a 404 means it does not."""
    normalised = normalise(name)
    status, body = fetch(f"https://pypi.org/pypi/{normalised}/json")
    if status == _HTTP_NOT_FOUND:
        return Registry(exists=False, latest=None, normalised=normalised)
    if status != _HTTP_OK:
        msg = f"PyPI answered {status} for '{normalised}'"
        raise SpikeError(msg)
    latest: str = json.loads(body)["info"]["version"]
    return Registry(exists=True, latest=latest, normalised=normalised)


def vetted(name: str, project_root: Path, allow: Collection[str]) -> bool:
    """Whether the project's dependency files name `name`, or the user allowed it."""
    if normalise(name) in {normalise(allowed) for allowed in allow}:
        return True
    return pins(project_root, [name])[name] is not None


def write(
    spike_dir: Path,
    hypothesis: str,
    code: str,
    deps: dict[str, str],
    exclude_newer: str,
) -> Path:
    """Write `spike.py` pinning each dependency to its exact version; return its path.

    Rewriting a spike drops its old `result.json`, which no longer describes it.
    """
    spike_dir.mkdir(parents=True, exist_ok=True)
    header = Template(_TEMPLATE.read_text(encoding="utf-8")).substitute(
        dependencies=json.dumps(
            [f"{name}=={version}" for name, version in deps.items()]
        ),
        exclude_newer=json.dumps(exclude_newer),
        hypothesis=" ".join(hypothesis.split())
        .replace("\\", "\\\\")
        .replace('"', '\\"'),
    )
    script = spike_dir / "spike.py"
    script.write_text(f"{header}\n{code.rstrip()}\n", encoding="utf-8")
    spec = {"hypothesis": hypothesis, "deps": deps, "exclude_newer": exclude_newer}
    (spike_dir / "spike.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (spike_dir / "result.json").unlink(missing_ok=True)
    return script


def run(
    spike_dir: Path,
    project_root: Path,
    *,
    container: bool = False,
    allow: Collection[str] = (),
    runtimes: Sequence[str] = RUNTIMES,
) -> SpikeResult:
    """Run the spike and record it in `result.json`.

    It runs in a container when `container` is set or any dependency is not
    `vetted`; then the first of `runtimes` that answers `info` is used, and
    with none the spike is refused with `SpikeError` and nothing is run.
    """
    spec = _spec(spike_dir)
    unvetted = [name for name in spec["deps"] if not vetted(name, project_root, allow)]
    result = _execute(
        spike_dir, container=container or bool(unvetted), runtimes=runtimes
    )
    record = {
        **spec,
        **asdict(result),
        "allow": sorted(allow),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (spike_dir / "result.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    return result


def rerun(
    spikes_root: Path,
    project_root: Path,
    runtimes: Sequence[str] = RUNTIMES,
    only: Collection[str] | None = None,
) -> list[Change]:
    """Re-execute recorded spikes, or those named in `only`; return those that changed.

    A spike runs where it last ran, except that one recorded on the host whose
    dependencies are no longer `vetted` in `project_root`, given the names
    allowed when it was recorded, reruns in a container. The recorded
    `result.json` is left alone, so it stays the baseline a lesson's claims
    were made against.
    """
    changes: list[Change] = []
    for result_file in sorted(spikes_root.glob("*/result.json")):
        spike_dir = result_file.parent
        if only is not None and spike_dir.name not in only:
            continue
        recorded = json.loads(result_file.read_text(encoding="utf-8"))
        deps = recorded.get("deps", {})
        allow = recorded.get("allow", [])
        unvetted = any(not vetted(name, project_root, allow) for name in deps)
        now = _execute(
            spike_dir,
            container=recorded["where"] == "container" or unvetted,
            runtimes=runtimes,
        )
        if (now.stdout, now.returncode) != (recorded["stdout"], recorded["returncode"]):
            changes.append(
                Change(
                    spike_dir.name,
                    recorded["stdout"],
                    recorded["returncode"],
                    now.stdout,
                    now.returncode,
                )
            )
    return changes


def _spec(spike_dir: Path) -> dict[str, Any]:
    spec_file = spike_dir / "spike.json"
    if not spec_file.is_file():
        msg = f"no spike in {spike_dir}"
        raise SpikeError(msg)
    spec: dict[str, Any] = json.loads(spec_file.read_text(encoding="utf-8"))
    return spec


def _execute(
    spike_dir: Path, *, container: bool, runtimes: Sequence[str]
) -> SpikeResult:
    if container:
        runtime = next((name for name in runtimes if _runtime_works(name)), None)
        if runtime is None:
            msg = "unvetted package and no container runtime"
            raise SpikeError(msg)
        return _timed(
            [
                runtime, "run", "--rm", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:exec",  # noqa: S108
                "-e", "UV_CACHE_DIR=/tmp/uv", "-e", "HOME=/tmp",
                "-v", f"{spike_dir.resolve()}:/spike:ro", "-w", "/spike", "--read-only",
                CONTAINER_IMAGE,
                "uv", "run", "--no-project", "--script", "/spike/spike.py",
            ],
            where="container",
        )  # fmt: skip
    env = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "HOME"} or name.startswith("UV_")
    }
    env["UV_CACHE_DIR"] = str(spike_dir.resolve() / ".uv-cache")
    return _timed(
        ["uv", "run", "--no-project", "--isolated", "--script", "spike.py"],
        where="host",
        cwd=spike_dir,
        env=env,
    )


def _timed(
    command: list[str],
    *,
    where: Literal["host", "container"],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> SpikeResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=SPIKE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"the spike ran longer than {SPIKE_TIMEOUT_SECONDS} s"
        raise SpikeError(msg) from error
    duration = round(time.monotonic() - started, 3)
    return SpikeResult(
        completed.stdout, completed.stderr, completed.returncode, duration, where
    )


def _runtime_works(runtime: str) -> bool:
    if shutil.which(runtime) is None:
        return False
    try:
        info = subprocess.run(  # noqa: S603
            [runtime, "info"],
            capture_output=True,
            timeout=RUNTIME_INFO_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return info.returncode == 0
