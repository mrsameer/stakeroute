"""Redaction at the ingestion boundary, before any durable write (D-014).

Two-step, one boundary: (1) an allow-list drops any field a source was not
declared to carry, (2) five rewrite rules run over every surviving string
value. Nothing unredacted is ever stored, displayed, or transmitted — this
is the single place SC-115's scan has to be clean over.

**Cost, stated (D-014)**: an agent sees ``<redacted:path>`` rather than the
actual path that filled up. The checkable conditions (``real/conditions.py``)
operate on the host directly, not on redacted text, so outcome
determination loses nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "host.metrics": frozenset(
        {"metric", "value", "mount", "process_name", "pid", "unit"}
    ),
    "app.logs": frozenset({"level", "logger", "message", "exception_type"}),
    "repo.vcs_tests": frozenset(
        {
            "commit_sha",
            "subject",
            "files_changed",
            "test_node_id",
            "test_outcome",
            "duration_ms",
        }
    ),
    "container.events": frozenset({"action", "container_name", "image", "exit_code"}),
}

# Requires at least one full "segment/" before the final segment, so a
# plain fraction like "50/50" in a string value is not mistaken for a
# filesystem path (Principle II: a rule that over-fires is as much a
# defect as one that under-fires).
_ABS_PATH_PATTERN = re.compile(r"(?<![\w/])/(?:[\w.\-]+/)+[\w.\-]*")

_ENV_VAR_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=(\S+)")

_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),  # long hex run
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),  # long base64 run
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """A redacted payload and which rules fired producing it — the
    operator surface renders this rather than leaving redaction silent."""

    payload: dict
    rules_fired: tuple[str, ...]
    dropped_field_count: int


def _redact_string(text: str, home_dir: str, username: str) -> tuple[str, set[str]]:
    fired: set[str] = set()

    for pattern in _CREDENTIAL_PATTERNS:

        def _cred_sub(match: re.Match) -> str:
            fired.add("credential")
            return "<redacted:credential>"

        text = pattern.sub(_cred_sub, text)

    def _path_sub(match: re.Match) -> str:
        fired.add("path")
        path = match.group(0)
        if home_dir and path.startswith(home_dir):
            return "<redacted:path>"
        if username and username in path:
            return "<redacted:path>"
        basename = path.rsplit("/", 1)[-1]
        return f"<redacted:path>/{basename}" if basename else "<redacted:path>"

    text = _ABS_PATH_PATTERN.sub(_path_sub, text)

    if username:

        def _user_sub(match: re.Match) -> str:
            fired.add("user")
            return "<redacted:user>"

        text = re.sub(rf"\b{re.escape(username)}\b", _user_sub, text)

    def _env_sub(match: re.Match) -> str:
        fired.add("env")
        return f"{match.group(1)}=<redacted:env>"

    text = _ENV_VAR_PATTERN.sub(_env_sub, text)

    return text, fired


def _redact_value(
    value: object, home_dir: str, username: str
) -> tuple[object, set[str]]:
    if isinstance(value, str):
        return _redact_string(value, home_dir, username)
    if isinstance(value, list):
        fired: set[str] = set()
        redacted_list = []
        for item in value:
            redacted_item, item_fired = _redact_value(item, home_dir, username)
            redacted_list.append(redacted_item)
            fired |= item_fired
        return redacted_list, fired
    return value, set()


def redact_event(
    source: str, raw_payload: dict, home_dir: str, username: str
) -> RedactionResult:
    """Apply the allow-list then the five rewrite rules to one source's raw
    payload, before it is ever durably written."""
    allowed = ALLOWED_FIELDS.get(source, frozenset())
    fired: set[str] = set()
    dropped = 0
    result: dict = {}

    for key, value in raw_payload.items():
        if key not in allowed:
            dropped += 1
            continue
        redacted_value, value_fired = _redact_value(value, home_dir, username)
        fired |= value_fired
        result[key] = redacted_value

    return RedactionResult(
        payload=result, rules_fired=tuple(sorted(fired)), dropped_field_count=dropped
    )
