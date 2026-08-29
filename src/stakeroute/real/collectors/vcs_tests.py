"""``git log``, ``git status``, and the most recent test-run result (D-015).

``absent`` when ``git`` is not on ``PATH`` or the configured path is not a
repository. 30s poll — commit cadence and test runs are both far slower
than host metrics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from stakeroute.real.collectors import CollectorPollResult, RawObservation


class VcsTestsCollector:
    source_id = "repo.vcs_tests"
    display_name = "Version Control & Tests"

    def __init__(
        self,
        repo_path: str,
        silence_threshold_ms: int,
        test_results_path: str | None = None,
    ) -> None:
        self.silence_threshold_ms = silence_threshold_ms
        self._repo_path = repo_path
        self._test_results_path = test_results_path
        self._last_commit_sha: str | None = None
        self._last_test_results_mtime: float | None = None

    async def poll(self) -> CollectorPollResult:
        git_binary = shutil.which("git")
        if git_binary is None:
            return CollectorPollResult(
                absent=True, absent_reason="git binary not present"
            )

        try:
            log_result = subprocess.run(
                [git_binary, "-C", self._repo_path, "log", "-1", "--format=%H|%s"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CollectorPollResult(
                absent=True, absent_reason=f"git unavailable: {exc}"
            )

        if log_result.returncode != 0:
            return CollectorPollResult(
                absent=True, absent_reason="not a git repository"
            )

        now_ms = int(time.time() * 1000)
        observations: list[RawObservation] = []

        line = log_result.stdout.strip()
        if line:
            commit_sha, _, subject = line.partition("|")
            if commit_sha and commit_sha != self._last_commit_sha:
                diff_result = subprocess.run(
                    [
                        git_binary,
                        "-C",
                        self._repo_path,
                        "diff",
                        "--name-only",
                        f"{commit_sha}~1",
                        commit_sha,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                files_changed = [
                    f for f in diff_result.stdout.strip().splitlines() if f
                ]
                observations.append(
                    RawObservation(
                        source_event_id=f"commit:{commit_sha}",
                        observed_at_ms=now_ms,
                        payload={
                            "commit_sha": commit_sha,
                            "subject": subject,
                            "files_changed": files_changed,
                        },
                        severity=0.3,
                    )
                )
                self._last_commit_sha = commit_sha

        observations.extend(self._poll_test_results(now_ms))
        return CollectorPollResult(observations=tuple(observations))

    def _poll_test_results(self, now_ms: int) -> list[RawObservation]:
        if not self._test_results_path:
            return []
        path = Path(self._test_results_path)
        if not path.exists():
            return []
        mtime = path.stat().st_mtime
        if mtime == self._last_test_results_mtime:
            return []
        self._last_test_results_mtime = mtime

        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return []

        observations: list[RawObservation] = []
        for test in data.get("tests", []):
            node_id = test.get("nodeid")
            outcome = test.get("outcome")
            if not node_id or not outcome:
                continue
            duration_ms = int(float(test.get("duration", 0.0)) * 1000)
            observations.append(
                RawObservation(
                    source_event_id=f"test:{node_id}:{mtime}",
                    observed_at_ms=now_ms,
                    payload={
                        "test_node_id": node_id,
                        "test_outcome": outcome,
                        "duration_ms": duration_ms,
                    },
                    severity=1.0 if outcome == "failed" else 0.1,
                )
            )
        return observations
