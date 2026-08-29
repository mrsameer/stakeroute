"""Attack injection for the demo surface (FR-036, User Story 2).

Both attacks are generated the same way the baseline scenario is — via an
explicitly passed ``random.Random`` (D-009) — so an attack run is as
reproducible as the baseline it is layered onto.
"""

from __future__ import annotations

import random

from stakeroute.core.reputation import REPUTATION_FLOOR
from stakeroute.core.types import clamp_probability
from stakeroute.simulator.agents import AgentProfile
from stakeroute.simulator.scenarios import ForecastSpec


def inject_sybils(
    rng: random.Random,
    count: int,
    target_hypothesis_id: str,
    confidence: float = 0.9,
) -> tuple[tuple[AgentProfile, ...], tuple[ForecastSpec, ...]]:
    """Generate ``count`` new, unattested, floor-reputation agents
    confidently backing ``target_hypothesis_id``.

    Each Sybil cites its own distinct evidence cluster — a flood of
    *identities*, not (yet) of *correlated evidence*. See
    ``inject_correlated`` for the latter attack.
    """
    agents = []
    forecasts = []
    for i in range(count):
        agent_id = f"sybil-{target_hypothesis_id}-{i}"
        agents.append(
            AgentProfile(
                agent_id=agent_id,
                display_name=f"Sybil {i}",
                accuracy=0.5,
                starting_reputation=REPUTATION_FLOOR,
                malicious=False,
                attested=False,
            )
        )
        probability = clamp_probability(confidence + rng.uniform(-0.03, 0.03))
        stake = rng.randint(1, 5)
        forecasts.append(
            ForecastSpec(
                hypothesis_id=target_hypothesis_id,
                agent_id=agent_id,
                probability=probability,
                stake=stake,
                evidence_cluster_id=f"{agent_id}-evidence",
                source_event_id=f"{agent_id}-forecast",
            )
        )
    return tuple(agents), tuple(forecasts)


def inject_correlated(
    rng: random.Random,
    count: int,
    cluster_id: str,
    target_hypothesis_id: str,
    confidence: float = 0.80,
) -> tuple[tuple[AgentProfile, ...], tuple[ForecastSpec, ...]]:
    """Generate ``count`` new, attested agents that all cite the SAME
    ``cluster_id``.

    Unlike ``inject_sybils``, these agents are attested — the attack is
    entirely at the evidence layer, not the identity layer: duplicate
    corroboration of one already-counted source, which the independence
    discount (FR-015) must dampen collectively rather than let each new
    report count as fresh confirmation. Reputation and stake are kept
    modest and uniform across the batch deliberately, so the discount being
    exercised is the evidence-cluster one (independence_factor), not the
    reputation floor that already defends against Sybils.
    """
    agents = []
    forecasts = []
    for i in range(count):
        agent_id = f"correlated-{cluster_id}-{i}"
        agents.append(
            AgentProfile(
                agent_id=agent_id,
                display_name=f"Correlated Reporter {i}",
                accuracy=0.7,
                starting_reputation=0.2,
                malicious=False,
                attested=True,
            )
        )
        probability = clamp_probability(confidence + rng.uniform(-0.03, 0.03))
        stake = rng.randint(1, 2)
        forecasts.append(
            ForecastSpec(
                hypothesis_id=target_hypothesis_id,
                agent_id=agent_id,
                probability=probability,
                stake=stake,
                evidence_cluster_id=cluster_id,
                source_event_id=f"{agent_id}-forecast",
            )
        )
    return tuple(agents), tuple(forecasts)
