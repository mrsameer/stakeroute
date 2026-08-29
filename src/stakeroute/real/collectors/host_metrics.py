"""``psutil``: CPU, memory, per-mount disk, process-table deltas (D-015).

``psutil`` is a hard dependency so this source is never ``absent`` — it is
always available, on both the macOS development target and the Linux
containers Compose runs. Poll interval: ``COLLECTOR_POLL_INTERVAL_S`` (2s).
"""

from __future__ import annotations

import time

import psutil

from stakeroute.real.collectors import CollectorPollResult, RawObservation


def _severity_from_pct(pct: float) -> float:
    return min(max(pct / 100.0, 0.0), 1.0)


class HostMetricsCollector:
    source_id = "host.metrics"
    display_name = "Host Metrics"

    def __init__(self, silence_threshold_ms: int) -> None:
        self.silence_threshold_ms = silence_threshold_ms
        self._known_pids: set[int] = set(psutil.pids())

    async def poll(self) -> CollectorPollResult:
        now_ms = int(time.time() * 1000)
        bucket = now_ms // 1000
        observations: list[RawObservation] = []

        cpu_pct = psutil.cpu_percent(interval=None)
        observations.append(
            RawObservation(
                source_event_id=f"cpu:{bucket}",
                observed_at_ms=now_ms,
                payload={"metric": "cpu_pct", "value": cpu_pct, "unit": "pct"},
                severity=_severity_from_pct(cpu_pct),
            )
        )

        memory = psutil.virtual_memory()
        observations.append(
            RawObservation(
                source_event_id=f"mem:{bucket}",
                observed_at_ms=now_ms,
                payload={
                    "metric": "memory_used_pct",
                    "value": memory.percent,
                    "unit": "pct",
                },
                severity=_severity_from_pct(memory.percent),
            )
        )

        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            observations.append(
                RawObservation(
                    source_event_id=f"disk:{part.mountpoint}:{bucket}",
                    observed_at_ms=now_ms,
                    payload={
                        "metric": "disk_used_pct",
                        "mount": part.mountpoint,
                        "value": usage.percent,
                        "unit": "pct",
                    },
                    severity=_severity_from_pct(usage.percent),
                )
            )

        current_pids = set(psutil.pids())
        for pid in sorted(current_pids - self._known_pids):
            try:
                name = psutil.Process(pid).name()
            except psutil.Error:
                name = "unknown"
            observations.append(
                RawObservation(
                    source_event_id=f"proc-start:{pid}:{bucket}",
                    observed_at_ms=now_ms,
                    payload={
                        "metric": "process_started",
                        "process_name": name,
                        "pid": pid,
                    },
                    severity=0.1,
                )
            )
        for pid in sorted(self._known_pids - current_pids):
            observations.append(
                RawObservation(
                    source_event_id=f"proc-exit:{pid}:{bucket}",
                    observed_at_ms=now_ms,
                    payload={"metric": "process_exited", "pid": pid},
                    severity=0.1,
                )
            )
        self._known_pids = current_pids

        return CollectorPollResult(observations=tuple(observations))
