"""``docker events --since``, where a runtime is present (D-015).

``absent`` with ``absent_reason='docker daemon unreachable'`` where none is
— the common developer-laptop case, and a validation scenario in its own
right (quickstart V4), not a failure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time

from stakeroute.real.collectors import CollectorPollResult, RawObservation

_HIGH_SEVERITY_ACTIONS = frozenset({"die", "kill", "oom"})


class ContainerEventsCollector:
    source_id = "container.events"
    display_name = "Container Events"

    def __init__(self, silence_threshold_ms: int) -> None:
        self.silence_threshold_ms = silence_threshold_ms
        self._last_polled_at_s: int | None = None

    async def poll(self) -> CollectorPollResult:
        docker_binary = shutil.which("docker")
        if docker_binary is None:
            return CollectorPollResult(
                absent=True, absent_reason="docker binary not present"
            )

        now_s = int(time.time())
        # since == until makes `docker events` treat the window as
        # open-ended and block on the live stream instead of returning a
        # closed (possibly empty) range immediately — always keep the
        # window at least one second wide, even on the very first poll.
        since_s = (
            self._last_polled_at_s if self._last_polled_at_s is not None else now_s - 1
        )
        until_s = max(now_s, since_s + 1)
        self._last_polled_at_s = until_s

        try:
            result = subprocess.run(
                [
                    docker_binary,
                    "events",
                    "--since",
                    str(since_s),
                    "--until",
                    str(until_s),
                    "--format",
                    "{{json .}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return CollectorPollResult(
                absent=True,
                absent_reason=("docker events did not respond within the poll timeout"),
            )
        except OSError as exc:
            return CollectorPollResult(
                absent=True, absent_reason=f"docker daemon unreachable: {exc}"
            )

        if result.returncode != 0:
            return CollectorPollResult(
                absent=True, absent_reason="docker daemon unreachable"
            )

        now_ms = int(time.time() * 1000)
        observations: list[RawObservation] = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue

            action = event.get("Action", "")
            actor = event.get("Actor", {})
            attributes = actor.get("Attributes", {})
            container_name = attributes.get("name", actor.get("ID", "unknown"))
            image = attributes.get("image", "")
            exit_code_raw = attributes.get("exitCode")
            exit_code = int(exit_code_raw) if exit_code_raw not in (None, "") else None
            event_id = (
                event.get("id") or f"{action}:{container_name}:{event.get('time')}"
            )

            observations.append(
                RawObservation(
                    source_event_id=f"docker:{event_id}",
                    observed_at_ms=now_ms,
                    payload={
                        "action": action,
                        "container_name": container_name,
                        "image": image,
                        "exit_code": exit_code,
                    },
                    severity=0.6 if action in _HIGH_SEVERITY_ACTIONS else 0.2,
                )
            )

        return CollectorPollResult(observations=tuple(observations))
