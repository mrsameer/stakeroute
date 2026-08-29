"""A model usage ceiling that degrades capability, never the decision path
(D-020, FR-125).

Ranking and settlement never call the model at all, so exhausting this
budget cannot touch them — it can only stop proposal and forecast
production from producing anything new to rank.
"""

from __future__ import annotations

from stakeroute.model.protocol import (
    CAPABILITY_HYPOTHESIS_PROPOSAL,
    CAPABILITY_PROSE_EXPLANATION,
    ModelState,
    RejectionReason,
)

_PURPOSE_CAPABILITY = {
    "proposal": CAPABILITY_HYPOTHESIS_PROPOSAL,
    "forecast": CAPABILITY_HYPOTHESIS_PROPOSAL,
    "explanation": CAPABILITY_PROSE_EXPLANATION,
}

CONSECUTIVE_FAILURES_FOR_DEGRADED = 3


def capability_for_purpose(purpose: str) -> str:
    """Map a ``model_interactions.purpose`` value to the capability it
    consumes. Raises on any purpose outside the closed set — there is no
    third capability this boundary can report (contracts/model-boundary.md)."""
    try:
        return _PURPOSE_CAPABILITY[purpose]
    except KeyError:
        raise ValueError(f"unknown model purpose: {purpose!r}") from None


def compute_model_state(
    calls_this_interval: int, ceiling: int, consecutive_failures: int
) -> tuple[ModelState, str, tuple[str, ...]]:
    """The single ``ModelState`` transition rule (FR-124), shared by every
    caller that needs to report it — ``GeminiClient.state()`` (in-process,
    live) and the dashboard's ``GET /api/mode`` (a separate process,
    derived from recorded ``model_interactions`` rows) must never
    disagree about what "ceiling reached" or "degraded" means.

    The returned ``unavailable_capabilities`` only ever names
    ``hypothesis_proposal`` and/or ``prose_explanation`` — ranking and
    settlement are never on it, because they never call the model.
    """
    if calls_this_interval >= ceiling:
        return (
            "ceiling_reached",
            f"{calls_this_interval}/{ceiling} calls this hour",
            (CAPABILITY_HYPOTHESIS_PROPOSAL, CAPABILITY_PROSE_EXPLANATION),
        )
    if consecutive_failures >= CONSECUTIVE_FAILURES_FOR_DEGRADED:
        return (
            "degraded",
            f"{consecutive_failures} consecutive timeouts/failures",
            (CAPABILITY_HYPOTHESIS_PROPOSAL,),
        )
    return "ok", "", ()


def rejection_for_state(state: ModelState) -> RejectionReason | None:
    """Map a capability-degraded state directly to the rejection a caller
    should report *before* even attempting a call (D-020) — this is what
    lets ceiling exhaustion degrade capability and report consumption
    instead of failing opaquely, wired into ``real/proposal.py`` and
    ``real/reasoners.py`` ahead of every prompt they would otherwise
    build for nothing.
    """
    if state == "ceiling_reached":
        return "CEILING_REACHED"
    if state in ("unconfigured", "disabled"):
        return "MODEL_DISABLED"
    return None


class ModelBudget:
    """Tracks calls within a rolling one-hour window against a configured
    ceiling."""

    WINDOW_MS = 60 * 60 * 1000

    def __init__(self, ceiling: int) -> None:
        self._ceiling = ceiling
        self._call_times_ms: list[int] = []

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - self.WINDOW_MS
        self._call_times_ms = [t for t in self._call_times_ms if t > cutoff]

    def calls_this_interval(self, now_ms: int) -> int:
        self._prune(now_ms)
        return len(self._call_times_ms)

    def has_capacity(self, now_ms: int) -> bool:
        return self.calls_this_interval(now_ms) < self._ceiling

    def record_call(self, now_ms: int) -> None:
        self._prune(now_ms)
        self._call_times_ms.append(now_ms)

    def ceiling(self) -> int:
        return self._ceiling
