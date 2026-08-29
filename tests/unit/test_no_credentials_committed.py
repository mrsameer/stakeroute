"""Guard against a credential ever being tracked by git (SC-112, first half).

This is deliberately a filesystem/git-metadata check, not a network call —
it must run in the fast unit loop with no model, no infrastructure.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

CREDENTIAL_PATTERNS = [
    re.compile(r"\"type\"\s*:\s*\"authorized_user\""),
    re.compile(r"\"refresh_token\"\s*:"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"ghp_[0-9A-Za-z]{36}"),
    re.compile(r"sk-[0-9A-Za-z]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# Test fixtures that deliberately contain synthetic, credential-*shaped*
# strings to exercise the redaction rules themselves
# (tests/unit/test_redaction.py, contracts/observations.md's rule 5). These
# are fake — the point of that test file is that such strings must be
# redacted before they ever reach a real payload — so they are excluded
# here rather than the scan being weakened for every other file.
_KNOWN_SYNTHETIC_FIXTURES = {"tests/unit/test_redaction.py"}


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_no_git_tracked_file_contains_a_credential_shaped_string() -> None:
    offenders: list[str] = []
    for rel_path in _tracked_files():
        if rel_path in _KNOWN_SYNTHETIC_FIXTURES:
            continue
        path = REPO_ROOT / rel_path
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in CREDENTIAL_PATTERNS):
            offenders.append(rel_path)
    assert not offenders, f"credential-shaped content tracked by git: {offenders}"


def test_vertex_credentials_file_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "vertex-ai-credentials.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "vertex-ai-credentials.json must be git-ignored "
        f"(check-ignore exited {result.returncode}: {result.stderr})"
    )


def test_wildcard_credentials_pattern_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "any-name-credentials.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "*-credentials.json must be git-ignored"
