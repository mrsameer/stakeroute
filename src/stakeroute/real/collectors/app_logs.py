"""The system's own log output (D-015). ``absent`` when the log path is
unwritable — the one collector whose absence would be a real operational
problem, not a laptop's missing container runtime.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from stakeroute.real.collectors import CollectorPollResult, RawObservation

_LEVEL_PATTERN = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")

_SEVERITY_BY_LEVEL = {
    "DEBUG": 0.05,
    "INFO": 0.1,
    "WARNING": 0.4,
    "ERROR": 0.8,
    "CRITICAL": 1.0,
}


class AppLogsCollector:
    source_id = "app.logs"
    display_name = "Application Logs"

    def __init__(self, log_path: str, silence_threshold_ms: int) -> None:
        self.silence_threshold_ms = silence_threshold_ms
        self._log_path = Path(log_path)
        self._offset = 0

    async def poll(self) -> CollectorPollResult:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._log_path.exists():
                self._log_path.touch()
            if not os.access(self._log_path, os.R_OK):
                return CollectorPollResult(
                    absent=True, absent_reason="log path unreadable"
                )
        except OSError as exc:
            return CollectorPollResult(
                absent=True, absent_reason=f"log path unwritable: {exc}"
            )

        now_ms = int(time.time() * 1000)
        observations: list[RawObservation] = []
        with self._log_path.open("r", errors="replace") as handle:
            handle.seek(self._offset)
            for i, raw_line in enumerate(handle):
                line = raw_line.rstrip("\n")
                if not line:
                    continue
                level_match = _LEVEL_PATTERN.search(line)
                level = level_match.group(1) if level_match else "INFO"
                observations.append(
                    RawObservation(
                        source_event_id=(
                            f"log:{hash(line) & 0xFFFFFFFF}:{now_ms // 1000}:{i}"
                        ),
                        observed_at_ms=now_ms,
                        payload={
                            "level": level,
                            "logger": "stakeroute",
                            "message": line,
                        },
                        severity=_SEVERITY_BY_LEVEL.get(level, 0.1),
                    )
                )
            self._offset = handle.tell()

        return CollectorPollResult(observations=tuple(observations))
