"""Tests that agents holding disjoint evidence can reason differently
(SC-101, FR-112, FR-117).

The negative check matters as much as the positive one: no configured
accuracy constant exists anywhere under ``src/stakeroute/real/`` — an
agent's calibration in real mode is measured from settlements, never
declared (D-013, contracts/model-boundary.md's stated limitation aside).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from stakeroute.model.protocol import Accepted, ModelStateReport
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import RawObservation, ingest_raw_observation
from stakeroute.real.reasoners import run_agent_forecast
from stakeroute.real.scopes import EvidenceAccessScope, build_evidence_bundle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"
HOME_DIR = "/Users/x"
USERNAME = "x"

REAL_DIR = Path(__file__).parent.parent.parent / "src" / "stakeroute" / "real"


class StubModelClient:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Accepted(value=self._response, interaction_id="raw", latency_ms=2)

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


async def test_disjoint_evidence_yields_differing_probabilities_and_rationales(
    real_repo,
) -> None:
    now_ms = 1_000_000
    for agent_id in ("host-reasoner", "logs-reasoner"):
        real_repo.upsert_agent(
            agent_id=agent_id,
            tenant_id=TENANT_ID,
            display_name=agent_id,
            reputation=0.5,
            available_credits=100,
            staked_credits=0,
            attested=True,
            created_at_ms=0,
        )
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id=TENANT_ID,
        statement="Something is wrong",
        prior_probability=0.5,
        impact_minor_units=1,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=now_ms + 60_000,
        status="open",
        created_at_ms=now_ms,
    )
    ingest_raw_observation(
        real_repo,
        TENANT_ID,
        "host.metrics",
        RawObservation("cpu:1", now_ms - 1000, {"metric": "cpu_pct", "value": 30}, 0.2),
        HOME_DIR,
        USERNAME,
    )
    ingest_raw_observation(
        real_repo,
        TENANT_ID,
        "app.logs",
        RawObservation(
            "log:1",
            now_ms - 1000,
            {"level": "ERROR", "logger": "x", "message": "disk full"},
            0.9,
        ),
        HOME_DIR,
        USERNAME,
    )
    real_repo.commit()

    host_scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    logs_scope = EvidenceAccessScope(
        agent_id="logs-reasoner", source_ids=frozenset({"app.logs"}), label="logs"
    )
    host_bundle = build_evidence_bundle(
        real_repo, TENANT_ID, host_scope, "h1", "Something is wrong", 0, now_ms, now_ms
    )
    logs_bundle = build_evidence_bundle(
        real_repo, TENANT_ID, logs_scope, "h1", "Something is wrong", 0, now_ms, now_ms
    )

    # Two different stub models stand in for two agents genuinely
    # disagreeing because they hold different evidence — CPU looks calm,
    # logs look bad.
    host_model = ModelInteractionRecorder(
        real_repo,
        StubModelClient(
            {"probability": 0.15, "stake": 5, "rationale": "cpu load is low, 30%"}
        ),
        TENANT_ID,
        "real",
        "stub",
    )
    logs_model = ModelInteractionRecorder(
        real_repo,
        StubModelClient(
            {
                "probability": 0.85,
                "stake": 20,
                "rationale": "repeated disk-full errors in the logs",
            }
        ),
        TENANT_ID,
        "real",
        "stub",
    )

    host_result = await run_agent_forecast(
        real_repo, TENANT_ID, host_bundle, host_model, now_ms, 5.0, now_ms + 60_000
    )
    logs_result = await run_agent_forecast(
        real_repo, TENANT_ID, logs_bundle, logs_model, now_ms, 5.0, now_ms + 60_000
    )

    assert host_result is not None
    assert logs_result is not None
    assert host_result.probability != logs_result.probability

    forecasts = {
        f["agent_id"]: f for f in real_repo.list_forecasts_for_hypothesis("h1")
    }
    assert forecasts["host-reasoner"]["rationale"]
    assert forecasts["logs-reasoner"]["rationale"]
    assert (
        forecasts["host-reasoner"]["rationale"]
        != forecasts["logs-reasoner"]["rationale"]
    )


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_configured_accuracy_constant_exists_under_real() -> None:
    offenders = []
    for path in sorted(REAL_DIR.rglob("*.py")):
        if path.name.startswith("._"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "accuracy" in target.id.lower():
                        offenders.append(f"{path}:{target.id}")
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if "accuracy" in node.target.id.lower():
                    offenders.append(f"{path}:{node.target.id}")
    assert not offenders, f"configured accuracy constant found under real/: {offenders}"


def test_no_import_of_simulator_agent_profile_under_real() -> None:
    # simulator.agents.AgentProfile.accuracy is the configured figure
    # feature 001 uses; real/ must never read it.
    for path in sorted(REAL_DIR.rglob("*.py")):
        if path.name.startswith("._"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        imported = _imported_names(tree)
        assert not any(name.startswith("stakeroute.simulator") for name in imported), (
            f"{path} imports from stakeroute.simulator"
        )
