"""Agent accuracy profiles for the scenario simulator.

Not part of the deterministic core — this module deliberately uses
``random.Random`` to generate synthetic forecasts, but always via an
explicitly injected instance (D-009), never the module-level global.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from stakeroute.core.types import clamp_probability


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """A simulated agent's identity and calibration behaviour."""

    agent_id: str
    display_name: str
    accuracy: float
    starting_reputation: float
    malicious: bool = False
    attested: bool = True


HONEST_PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(
        "payment-agent-1", "Payment Specialist", accuracy=0.90, starting_reputation=0.70
    ),
    AgentProfile(
        "security-agent-1",
        "Security Specialist",
        accuracy=0.85,
        starting_reputation=0.65,
    ),
    AgentProfile(
        "general-agent-1", "General Agent A", accuracy=0.70, starting_reputation=0.50
    ),
    AgentProfile(
        "general-agent-2", "General Agent B", accuracy=0.70, starting_reputation=0.50
    ),
    AgentProfile(
        "noisy-agent-1", "Noisy Agent", accuracy=0.55, starting_reputation=0.40
    ),
    AgentProfile("new-agent-1", "New Agent", accuracy=0.65, starting_reputation=0.15),
)


def forecast_probability(
    rng: random.Random, ground_truth: bool, profile: AgentProfile
) -> float:
    """Draw a forecast probability consistent with ``profile``'s behaviour.

    An honest agent's forecast is centred near its accuracy toward the
    ground truth. A malicious agent is deliberately, confidently wrong —
    centred near the *opposite* of the ground truth regardless of its
    stated accuracy, which is what the attack demo (User Story 2) exploits.
    """
    if profile.malicious:
        base = 0.10 if ground_truth else 0.90
    else:
        base = profile.accuracy if ground_truth else (1.0 - profile.accuracy)
    noise = rng.uniform(-0.05, 0.05)
    return clamp_probability(base + noise)
