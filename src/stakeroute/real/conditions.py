"""The closed checkable-condition registry (FR-148, D-017, contracts/observations.md).

Each entry is a pure predicate over freshly sampled host state — ``psutil``,
a subprocess, or the filesystem, queried right now — never over anything
recorded in ``events`` or ``observation_sources``. That is what makes a
resolution an independent verification rather than a restatement of the
evidence the agents already saw. The model selects an entry by name and
supplies its parameters; nothing outside this module can be invoked as a
"condition" (D-017).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

_LOG_TAIL_LINES = 200
_LOG_LEVEL_PATTERN = re.compile(r"\b(ERROR|CRITICAL)\b")
_PYTEST_TIMEOUT_S = 120
_SUBPROCESS_TIMEOUT_S = 5


@dataclass(frozen=True, slots=True)
class ConditionResult:
    """What one re-check returned — recorded verbatim (FR-148)."""

    result: bool
    detail: str


def _process_absent(params: dict) -> ConditionResult:
    name = params["name"]
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] == name:
                return ConditionResult(
                    False, f"process {name!r} is running (pid {proc.pid})"
                )
        except psutil.Error:
            continue
    return ConditionResult(True, f"no process named {name!r} found")


def _disk_free_below(params: dict) -> ConditionResult:
    mount = params["mount"]
    pct = float(params["pct"])
    usage = psutil.disk_usage(mount)
    free_pct = 100.0 - usage.percent
    return ConditionResult(
        free_pct < pct, f"{mount} has {free_pct:.1f}% free (threshold {pct}%)"
    )


def _cpu_saturated(params: dict) -> ConditionResult:
    threshold = float(params["threshold"])
    window_s = float(params["window_s"])
    mean_cpu = psutil.cpu_percent(interval=min(window_s, 1.0))
    return ConditionResult(
        mean_cpu > threshold,
        f"mean cpu over {window_s}s is {mean_cpu:.1f}% (threshold {threshold}%)",
    )


def _memory_pressure(params: dict) -> ConditionResult:
    threshold_pct = float(params["threshold_pct"])
    used_pct = psutil.virtual_memory().percent
    return ConditionResult(
        used_pct > threshold_pct,
        f"memory used {used_pct:.1f}% (threshold {threshold_pct}%)",
    )


def _test_failing(params: dict) -> ConditionResult:
    node_id = params["node_id"]
    runner = shutil.which("uv")
    if runner is None:
        return ConditionResult(
            True, f"'uv' not on PATH — cannot re-run {node_id}, treated as failing"
        )
    try:
        completed = subprocess.run(
            [runner, "run", "pytest", node_id, "-q"],
            capture_output=True,
            text=True,
            timeout=_PYTEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return ConditionResult(
            True, f"{node_id} did not complete within the re-check timeout"
        )
    return ConditionResult(
        completed.returncode != 0, f"{node_id} exit code {completed.returncode}"
    )


def _container_exited(params: dict) -> ConditionResult:
    name = params["name"]
    docker = shutil.which("docker")
    if docker is None:
        return ConditionResult(True, "docker binary not present — no container running")
    try:
        completed = subprocess.run(
            [docker, "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ConditionResult(True, f"docker inspect failed: {exc}")
    if completed.returncode != 0:
        return ConditionResult(True, f"container {name!r} not found")
    running = completed.stdout.strip() == "true"
    return ConditionResult(not running, f"container {name!r} running={running}")


def _log_error_rate_above(params: dict) -> ConditionResult:
    """The source records no per-line timestamp, so the rate is
    approximated as the error count within the tail of the current log
    file rather than a true per-minute figure — a stated limitation."""
    logger = params["logger"]
    rate_per_min = float(params["rate_per_min"])
    log_path = Path(os.environ.get("STAKEROUTE_APP_LOG_PATH", "data/stakeroute.log"))
    if not log_path.exists():
        return ConditionResult(False, f"{log_path} does not exist")
    lines = log_path.read_text(errors="replace").splitlines()[-_LOG_TAIL_LINES:]
    count = sum(
        1 for line in lines if logger in line and _LOG_LEVEL_PATTERN.search(line)
    )
    return ConditionResult(
        count > rate_per_min,
        f"{count} error line(s) for {logger!r} in the last {len(lines)} log "
        f"lines (threshold {rate_per_min}/min)",
    )


@dataclass(frozen=True, slots=True)
class ConditionSpec:
    param_names: frozenset[str]
    run: Callable[[dict], ConditionResult]


CONDITIONS: dict[str, ConditionSpec] = {
    "process_absent": ConditionSpec(frozenset({"name"}), _process_absent),
    "disk_free_below": ConditionSpec(frozenset({"mount", "pct"}), _disk_free_below),
    "cpu_saturated": ConditionSpec(
        frozenset({"threshold", "window_s"}), _cpu_saturated
    ),
    "memory_pressure": ConditionSpec(frozenset({"threshold_pct"}), _memory_pressure),
    "test_failing": ConditionSpec(frozenset({"node_id"}), _test_failing),
    "container_exited": ConditionSpec(frozenset({"name"}), _container_exited),
    "log_error_rate_above": ConditionSpec(
        frozenset({"logger", "rate_per_min"}), _log_error_rate_above
    ),
}


def run_condition(name: str, params: dict) -> ConditionResult:
    """Look up and run a registry entry — the only way any caller touches
    host state through this module (D-017)."""
    return CONDITIONS[name].run(params)
