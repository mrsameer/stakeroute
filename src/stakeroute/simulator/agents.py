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


# The population named by T027: payment specialist, security specialist,
# general, noisy, malicious (inverted), and a newly-created low-reputation
# agent. The malicious agent is a permanent member of the baseline
# population, not an attack-time injection — it is what keeps the honest
# population's own majority-vote tally short of a perfect, unbeatable 1.0,
# which is what a real population with one bad-faith participant looks
# like.
PAYMENT_SPECIALIST = AgentProfile(
    "payment-agent-1", "Payment Specialist", accuracy=0.90, starting_reputation=0.70
)
SECURITY_SPECIALIST = AgentProfile(
    "security-agent-1", "Security Specialist", accuracy=0.85, starting_reputation=0.65
)
GENERAL_AGENT = AgentProfile(
    "general-agent-1", "General Agent", accuracy=0.70, starting_reputation=0.50
)
NOISY_AGENT = AgentProfile(
    "noisy-agent-1", "Noisy Agent", accuracy=0.55, starting_reputation=0.40
)
MALICIOUS_AGENT = AgentProfile(
    "malicious-agent-1",
    "Malicious Agent",
    accuracy=0.50,
    starting_reputation=0.30,
    malicious=True,
)
NEW_AGENT = AgentProfile(
    "new-agent-1", "New Agent", accuracy=0.65, starting_reputation=0.15
)

HONEST_PROFILES: tuple[AgentProfile, ...] = (
    PAYMENT_SPECIALIST,
    SECURITY_SPECIALIST,
    GENERAL_AGENT,
    NOISY_AGENT,
    MALICIOUS_AGENT,
    NEW_AGENT,
)

# The two lowest-conviction, most plausible-to-be-wrong profiles — used to
# back the simulator's low-confidence "minor" candidate hypotheses. The
# malicious agent is deliberately excluded here: its role is targeted
# deception against the real hypotheses, not noise on irrelevant ones.
MINOR_HYPOTHESIS_PROFILES: tuple[AgentProfile, ...] = (NOISY_AGENT, NEW_AGENT)


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
