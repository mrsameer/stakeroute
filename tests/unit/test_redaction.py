"""Tests for ingestion-boundary redaction (FR-146, SC-115, contracts/observations.md).

Redaction runs once, at the ingestion boundary, before any durable write
(D-014) — this file is the audit of that one boundary: the per-source
allow-list, and all five rewrite rules.
"""

from __future__ import annotations

from stakeroute.real.redaction import redact_event

HOME_DIR = "/Users/alice"
USERNAME = "alice"


def _redact(source: str, payload: dict) -> tuple[dict, tuple[str, ...]]:
    result = redact_event(source, payload, home_dir=HOME_DIR, username=USERNAME)
    return result.payload, result.rules_fired


# -- allow-list ---------------------------------------------------------


def test_allow_listed_fields_survive() -> None:
    payload, _ = _redact(
        "host.metrics",
        {"metric": "cpu_pct", "value": 91.2, "mount": "/data", "unit": "pct"},
    )
    assert payload == {
        "metric": "cpu_pct",
        "value": 91.2,
        "mount": "/data",
        "unit": "pct",
    }


def test_fields_outside_the_allow_list_are_dropped() -> None:
    payload, _ = _redact(
        "host.metrics",
        {"metric": "cpu_pct", "value": 91.2, "hostname": "workstation-7"},
    )
    assert "hostname" not in payload
    assert payload["metric"] == "cpu_pct"


def test_allow_list_is_per_source() -> None:
    payload, _ = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "stakeroute.worker",
            "message": "boom",
            "pid": 123,
        },
    )
    assert "pid" not in payload  # pid is allowed for host.metrics, not app.logs
    assert payload["level"] == "ERROR"


# -- rule 1: home-directory paths ----------------------------------------


def test_home_directory_path_is_fully_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "x",
            "message": "failed to open /Users/alice/secret.txt",
        },
    )
    assert "/Users/alice" not in payload["message"]
    assert "<redacted:path>" in payload["message"]
    assert "path" in rules


# -- rule 2: other absolute paths ----------------------------------------


def test_non_home_absolute_path_is_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "x",
            "message": "cannot write to /var/log/app/output.log",
        },
    )
    assert "/var/log" not in payload["message"]
    assert "<redacted:path>" in payload["message"]
    assert "path" in rules


# -- rule 3: the current user's account name ------------------------------


def test_bare_username_outside_a_path_is_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {"level": "INFO", "logger": "x", "message": "process owned by alice restarted"},
    )
    assert "alice" not in payload["message"]
    assert "<redacted:user>" in payload["message"]
    assert "user" in rules


# -- rule 4: KEY=value environment shapes ---------------------------------


def test_env_var_shaped_value_is_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "DEBUG",
            "logger": "x",
            "message": "DATABASE_URL=postgres://secret@host/db",
        },
    )
    assert "postgres://secret" not in payload["message"]
    assert "DATABASE_URL=<redacted:env>" in payload["message"]
    assert "env" in rules


# -- rule 5: credential-shaped strings ------------------------------------


def test_github_token_is_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "x",
            "message": "auth failed with token ghp_abcdefghijklmnopqrstuvwxyz012345",
        },
    )
    assert "ghp_" not in payload["message"]
    assert "<redacted:credential>" in payload["message"]
    assert "credential" in rules


def test_google_api_key_is_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "x",
            "message": "key=AIzaSyD-abcdefghijklmnopqrstuvwxyz0123456",
        },
    )
    assert "AIza" not in payload["message"]
    assert "credential" in rules


def test_pem_block_is_redacted() -> None:
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBVgIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEA\n"
        "-----END PRIVATE KEY-----"
    )
    payload, rules = _redact(
        "app.logs", {"level": "ERROR", "logger": "x", "message": f"leaked: {pem}"}
    )
    assert "BEGIN PRIVATE KEY" not in payload["message"]
    assert "credential" in rules


def test_jwt_is_redacted() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    payload, rules = _redact(
        "app.logs", {"level": "ERROR", "logger": "x", "message": f"bearer {jwt}"}
    )
    assert jwt not in payload["message"]
    assert "credential" in rules


def test_long_hex_run_is_redacted() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "x",
            "message": "session=" + "a1b2c3d4e5f6" * 4,
        },
    )
    assert "credential" in rules


# -- rules fired is recorded ------------------------------------------------


def test_rules_fired_is_empty_when_nothing_matches() -> None:
    _, rules = _redact(
        "host.metrics", {"metric": "cpu_pct", "value": 50.0, "unit": "pct"}
    )
    assert rules == ()


def test_rules_fired_includes_every_rule_that_matched() -> None:
    payload, rules = _redact(
        "app.logs",
        {
            "level": "ERROR",
            "logger": "x",
            "message": (
                "alice's process at /Users/alice/app.log failed; SECRET_TOKEN=abcd1234"
            ),
        },
    )
    assert "user" in rules
    assert "path" in rules
    assert "env" in rules


def test_non_string_values_pass_through_unredacted() -> None:
    payload, _ = _redact(
        "host.metrics", {"metric": "cpu_pct", "value": 91.2, "pid": 1234}
    )
    assert payload["value"] == 91.2
    assert payload["pid"] == 1234
