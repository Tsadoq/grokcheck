"""Spikes: registry checks, vetting and isolated runs of one-question scripts."""

import json
import shutil
from pathlib import Path

import pytest
from grokcheck import spikes
from grokcheck.spikes import (
    SpikeError,
    SpikeResult,
    check_name,
    rerun,
    run,
    vetted,
    write,
)

_EXCLUDE_NEWER = "2026-01-01T00:00:00Z"

_UV_LOCK = """\
version = 1

[[package]]
name = "httpx"
version = "0.28.1"
source = { registry = "https://pypi.org/simple" }
"""


class _FakeFetch:
    """Stands in for PyPI: records each URL and answers with one canned reply."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.body = body
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.urls.append(url)
        return self.status, self.body


def test_run_isolates_in_uv_script_env_and_refuses_unvetted_without_container(
    tmp_path: Path,
) -> None:
    """A stdlib spike runs on the host through uv; an unvetted one never does."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH, so the host run cannot be exercised")
    spike_dir = tmp_path / "spikes" / "answer"
    write(spike_dir, "print gives 42", "print(6 * 7)", {}, _EXCLUDE_NEWER)

    result = run(spike_dir, tmp_path)

    if result.stdout.strip() != "42":
        pytest.fail(f"spike stdout {result.stdout!r}, stderr {result.stderr!r}")
    if not (spike_dir / ".uv-cache").exists():
        pytest.fail("uv did not use the per-spike cache directory")
    if result.where != "host":
        pytest.fail(f"a vetted spike ran in {result.where!r}, not on the host")

    unvetted_dir = tmp_path / "spikes" / "left-pad"
    write(unvetted_dir, "it pads", "print(1)", {"left-pad-py": "1.0"}, _EXCLUDE_NEWER)
    if vetted("left-pad-py", tmp_path, set()):
        pytest.fail("left-pad-py is in no lockfile, so it must be unvetted")

    with pytest.raises(SpikeError, match="no container runtime"):
        run(unvetted_dir, tmp_path, runtimes=())
    if (unvetted_dir / "result.json").exists():
        pytest.fail("a refused spike still wrote result.json")


def test_check_name_normalises_and_treats_404_as_absent_and_vetted_reads_lockfile(
    tmp_path: Path,
) -> None:
    """Names are PEP 503 normalised, 404 means absent, the lockfile means vetted."""
    missing = _FakeFetch(404)
    if check_name("Left_Pad.py", missing).exists is not False:
        pytest.fail("a 404 from PyPI must mean the package does not exist")
    if missing.urls != ["https://pypi.org/pypi/left-pad-py/json"]:
        pytest.fail(f"queried {missing.urls}, not the normalised name")

    found = _FakeFetch(200, json.dumps({"info": {"version": "1.2.3"}}).encode())
    if check_name("left-pad-py", found).latest != "1.2.3":
        pytest.fail("latest must come from info.version of the PyPI reply")

    (tmp_path / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    if vetted("httpx", tmp_path, set()) is not True:
        pytest.fail("httpx is in uv.lock, so it is vetted")
    if vetted("left-pad-py", tmp_path, set()) is not False:
        pytest.fail("left-pad-py is in no lockfile and not allowed, so unvetted")


def test_rerun_skips_unnamed_spikes_and_keeps_unvetted_ones_off_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unnamed spike is not run; a host spike is kept there only while vetted.

    `run` records the allowed names, and rerun vets against them.
    """
    containers: dict[str, bool] = {}

    def fake_execute(spike_dir: Path, *, container: bool, **_: object) -> SpikeResult:
        containers[spike_dir.name] = container
        return SpikeResult("1\n", "", 0, 0.0, "container" if container else "host")

    monkeypatch.setattr(spikes, "_execute", fake_execute)
    spikes_root = tmp_path / ".grokcheck" / "spikes"
    deps = {"left-pad-py": "1.0"}
    for ident in ("pads", "allowed", "unnamed"):
        write(spikes_root / ident, "it pads", "print(1)", deps, _EXCLUDE_NEWER)
    run(spikes_root / "allowed", tmp_path, allow={"left-pad-py"})
    recorded = json.loads((spikes_root / "allowed" / "result.json").read_text("utf-8"))
    if recorded["allow"] != ["left-pad-py"]:
        pytest.fail(f"result.json records allow as {recorded.get('allow')}")
    for ident in ("pads", "unnamed"):
        host = {"deps": deps, "stdout": "1\n", "returncode": 0, "where": "host"}
        (spikes_root / ident / "result.json").write_text(json.dumps(host), "utf-8")
    containers.clear()

    rerun(spikes_root, tmp_path, runtimes=(), only=["pads", "allowed"])

    if containers != {"pads": True, "allowed": False}:
        pytest.fail(f"rerun ran {containers}, as name: in a container")
