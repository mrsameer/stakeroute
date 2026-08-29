"""FR-148, D-017: every registry entry is a pure predicate over freshly
sampled host state, never over anything recorded in ``events`` or
``observation_sources`` — that is what makes a resolution an independent
verification rather than a restatement of evidence the agents already saw.
"""

from __future__ import annotations

import inspect

import pytest

from stakeroute.real import conditions


def test_registry_covers_exactly_the_seven_contract_conditions() -> None:
    assert set(conditions.CONDITIONS) == {
        "process_absent",
        "disk_free_below",
        "cpu_saturated",
        "memory_pressure",
        "test_failing",
        "container_exited",
        "log_error_rate_above",
    }


def test_no_registry_entry_accepts_a_repository_or_tenant_argument() -> None:
    """Structural proof of purity: nothing in the registry can be handed a
    ``Repository`` (or anything else naming recorded state) — its only
    input is the model-supplied parameter dict."""
    for name, spec in conditions.CONDITIONS.items():
        params = list(inspect.signature(spec.run).parameters)
        assert params == ["params"], f"{name} accepts unexpected arguments: {params}"


class _FakeProcess:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self.info = {"name": name}


def test_process_absent_true_when_no_matching_process(monkeypatch) -> None:
    monkeypatch.setattr(
        conditions.psutil,
        "process_iter",
        lambda fields: iter([_FakeProcess(1, "other")]),
    )
    result = conditions.run_condition("process_absent", {"name": "ghost"})
    assert result.result is True


def test_process_absent_false_when_matching_process_runs(monkeypatch) -> None:
    monkeypatch.setattr(
        conditions.psutil,
        "process_iter",
        lambda fields: iter([_FakeProcess(7, "stakeroute-worker")]),
    )
    result = conditions.run_condition("process_absent", {"name": "stakeroute-worker"})
    assert result.result is False
    assert "7" in result.detail


def test_process_absent_is_a_pure_function_of_current_state(monkeypatch) -> None:
    monkeypatch.setattr(
        conditions.psutil,
        "process_iter",
        lambda fields: iter([_FakeProcess(1, "other")]),
    )
    first = conditions.run_condition("process_absent", {"name": "ghost"})
    second = conditions.run_condition("process_absent", {"name": "ghost"})
    assert first == second


class _FakeUsage:
    def __init__(self, percent: float) -> None:
        self.percent = percent


def test_disk_free_below_true_when_free_space_under_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        conditions.psutil, "disk_usage", lambda mount: _FakeUsage(percent=97.0)
    )
    result = conditions.run_condition("disk_free_below", {"mount": "/", "pct": 5.0})
    assert result.result is True


def test_disk_free_below_false_when_plenty_free(monkeypatch) -> None:
    monkeypatch.setattr(
        conditions.psutil, "disk_usage", lambda mount: _FakeUsage(percent=10.0)
    )
    result = conditions.run_condition("disk_free_below", {"mount": "/", "pct": 5.0})
    assert result.result is False


def test_cpu_saturated_true_above_threshold(monkeypatch) -> None:
    monkeypatch.setattr(conditions.psutil, "cpu_percent", lambda interval: 99.0)
    result = conditions.run_condition(
        "cpu_saturated", {"threshold": 90.0, "window_s": 1.0}
    )
    assert result.result is True


def test_cpu_saturated_false_below_threshold(monkeypatch) -> None:
    monkeypatch.setattr(conditions.psutil, "cpu_percent", lambda interval: 5.0)
    result = conditions.run_condition(
        "cpu_saturated", {"threshold": 90.0, "window_s": 1.0}
    )
    assert result.result is False


def test_memory_pressure_true_above_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        conditions.psutil, "virtual_memory", lambda: _FakeUsage(percent=95.0)
    )
    result = conditions.run_condition("memory_pressure", {"threshold_pct": 90.0})
    assert result.result is True


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_test_failing_true_on_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(conditions.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(
        conditions.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(returncode=1),
    )
    result = conditions.run_condition("test_failing", {"node_id": "tests/x.py::t"})
    assert result.result is True


def test_test_failing_false_on_zero_exit(monkeypatch) -> None:
    monkeypatch.setattr(conditions.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(
        conditions.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(returncode=0),
    )
    result = conditions.run_condition("test_failing", {"node_id": "tests/x.py::t"})
    assert result.result is False


def test_container_exited_true_when_not_running(monkeypatch) -> None:
    monkeypatch.setattr(conditions.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        conditions.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(returncode=0, stdout="false\n"),
    )
    result = conditions.run_condition("container_exited", {"name": "web"})
    assert result.result is True


def test_container_exited_false_when_running(monkeypatch) -> None:
    monkeypatch.setattr(conditions.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        conditions.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(returncode=0, stdout="true\n"),
    )
    result = conditions.run_condition("container_exited", {"name": "web"})
    assert result.result is False


def test_log_error_rate_above_true_over_threshold(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("\n".join(["ERROR db unreachable"] * 5))
    monkeypatch.setenv("STAKEROUTE_APP_LOG_PATH", str(log_path))
    result = conditions.run_condition(
        "log_error_rate_above", {"logger": "db", "rate_per_min": 2}
    )
    assert result.result is True


def test_log_error_rate_above_false_under_threshold(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("INFO all fine")
    monkeypatch.setenv("STAKEROUTE_APP_LOG_PATH", str(log_path))
    result = conditions.run_condition(
        "log_error_rate_above", {"logger": "db", "rate_per_min": 2}
    )
    assert result.result is False


def test_unknown_condition_name_raises_rather_than_guessing() -> None:
    with pytest.raises(KeyError):
        conditions.run_condition("not_a_real_condition", {})
