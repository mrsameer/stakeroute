# Contract: The Model Boundary

**Constitution**: Principle I (non-negotiable), Principle V. **Research**: D-011, D-012, D-016, D-020.

**Requirements**: FR-119 – FR-131, FR-143.

This is the contract that keeps a non-deterministic component next to a system whose central claim
is reproducibility. It has one rule from which the rest follows:

> The model produces **prose and selections**. It never produces a number that anything reads.

Everything below is machinery for enforcing that rule and for surviving the model's absence.

---

## `ModelClient` Protocol

```python
@runtime_checkable
class ModelClient(Protocol):
    async def complete(
        self, purpose: str, prompt: str, schema: type[T], timeout_s: float
    ) -> ModelResult[T]:
        """Return a validated result, or a rejection. Never raises for a model-side failure."""
        ...

    def state(self) -> ModelState:
        """Current capability state, for the operator surface (FR-124)."""
        ...
```

`ModelResult` is either `Accepted(value: T, interaction_id: str, latency_ms: int)` or
`Rejected(reason: RejectionReason, interaction_id: str, latency_ms: int)`. A caller that forgets to
handle `Rejected` gets a type error, not a silent `None`.

**Implementations**:

| Implementation | Behaviour | Used by |
|---|---|---|
| `GeminiClient` | Vertex AI call, redacted prompt, configured timeout | live real mode |
| `NullModelClient` | Always returns `Rejected(MODEL_DISABLED)`, `state() == 'unconfigured'` | FR-126, SC-113, and every test that is not about the model |
| `RecordedModelClient` | Serves `model_interactions` rows by request hash; **raises** on a miss | replay (FR-130) |

`RecordedModelClient` raising rather than falling through to the network is the mechanism behind
SC-107's zero-request guarantee. There is no code path from replay to a socket.

## `ModelState`

```
'ok' | 'degraded' | 'ceiling_reached' | 'disabled' | 'unconfigured'
```

Each state carries `unavailable_capabilities: list[str]` — FR-124 requires naming what is
consequently unavailable, not merely reporting that something is wrong. The only two values that
list ever contains are `"hypothesis_proposal"` and `"prose_explanation"`. Ranking and settlement are
never on it, because they never call the model.

---

## Response schemas

The model is asked for exactly two things. Both are validated before anything downstream exists.

### `purpose='proposal'` — propose a hypothesis from observations

```json
{
  "statement": "The pytest suite has been failing on tests/integration/test_expiry.py since the most recent commit",
  "cited_observation_ids": ["a3f1…", "b7c2…"],
  "condition_name": "test_failing",
  "condition_params": {"node_id": "tests/integration/test_expiry.py::test_expiry_returns_stake"}
}
```

**Validation** (all failures produce `Rejected` and a `model_interactions` row with
`accepted = 0`):

| Rule | Rejection reason |
|---|---|
| Parses as the declared schema | `MALFORMED_SHAPE` |
| `statement` non-empty, ≤ 500 chars | `MALFORMED_SHAPE` |
| `cited_observation_ids` non-empty | `NO_CITATIONS` |
| Every cited id exists in `events` for this tenant | `UNKNOWN_CITATION` |
| Every cited id falls inside the proposal window | `CITATION_OUT_OF_WINDOW` |
| `condition_name` is `null` or a registry entry | `UNKNOWN_CONDITION` |
| `condition_params` validate against that entry's signature | `INVALID_CONDITION_PARAMS` |
| Response is not a refusal | `REFUSAL` |

**Note what is absent from the schema**: there is no `impact`, no `urgency`, no `review_cost`, and
no `probability`. Those are computed by `core/estimates.py` from the cited observations (D-016). The
schema is the enforcement — the model is never asked for a number, so a number cannot arrive.

### `purpose='forecast'` — an agent's judgement over its bundle

```json
{
  "probability": 0.72,
  "stake": 20,
  "rationale": "Three consecutive collector polls show the mount above 94% with the trend steepening; no cleanup process appears in the observations I can see."
}
```

**Validation**:

| Rule | Rejection reason |
|---|---|
| Parses as the declared schema | `MALFORMED_SHAPE` |
| `0.0 <= probability <= 1.0` | `PROBABILITY_OUT_OF_RANGE` |
| `STAKE_MIN <= stake <= STAKE_MAX` | `STAKE_OUT_OF_RANGE` |
| `stake <= agent.available_credits` | `INSUFFICIENT_CREDITS` |
| `rationale` non-empty | `MALFORMED_SHAPE` |
| Rationale cites only in-scope evidence | `EVIDENCE_SCOPE_VIOLATION` |
| Response is not a refusal | `REFUSAL` |

An accepted probability is then clamped by the existing `clamp_probability` before storage — the
model's raw value is recorded in `model_interactions`, the clamped value becomes the forecast. The
distinction matters: rejecting an out-of-range probability (FR-122) and clamping a valid one
(feature 001's D-009) are different rules and both apply.

`EVIDENCE_SCOPE_VIOLATION` and `INSUFFICIENT_CREDITS` are recorded in `rejected_forecasts` as well
as `model_interactions` (FR-116), reusing the table feature 001 already built for exactly this.

---

## Timeout and the decision path

```
MODEL_TIMEOUT_S = 10.0          # per request, configured
MODEL_CEILING_CALLS_PER_HOUR    # configured; exhaustion → 'ceiling_reached'
```

**The invariant**: no decision-path operation waits on a model response — ever, not even for the
timeout. This is stronger than "requests time out", and it is structural rather than a matter of
tuning:

- Model calls are made **only** in the ingestor process (D-024), during proposal and forecast
  production.
- The worker's `run_ranking_pass` and `settle_hypothesis` hold no `ModelClient` reference at all.
- The decision path reads `forecasts` and `hypotheses` rows that were already durably written
  (FR-128).

A slow model therefore delays *new proposals arriving*. It cannot delay a ranking pass, because the
ranking pass is not downstream of it. SC-105 is satisfied by architecture, and the test asserts it
by running a full pass with a client that sleeps past the timeout.

## Recording

Every call writes exactly one `model_interactions` row before its result is used, including
timeouts and transport failures (`response = NULL`, `accepted = 0`,
`rejection_reason = 'TIMEOUT' | 'TRANSPORT_FAILURE'`).

`request` holds the prompt **as sent**, after redaction (D-014). This is what SC-115 is verified
over — scanning the stored column for absolute paths, account names, environment values and
credential-shaped strings across 100% of rows.

Credentials appear in zero rows: the adapter never places a token in a prompt, and the SDK's auth
header is not part of the recorded request (FR-127, SC-112).

---

## Enforcement tests

These are contract tests, not aspirations. Each maps to a requirement:

| Test | Asserts | Requirement |
|---|---|---|
| `test_core_purity` (extended) | no module under `core/` imports `stakeroute.model` | FR-119 |
| `test_ranking_with_exploding_client` | full pass completes with a client that raises on any call | FR-120, SC-104 |
| `test_settlement_with_model_unreachable` | resolved outcomes settle, credits and reputation move | FR-120, SC-104 |
| `test_ranking_matches_with_and_without_model` | identical rankings either way | SC-104 |
| `test_slow_model_does_not_delay_pass` | pass duration unaffected by a client sleeping past timeout | FR-121, SC-105 |
| `test_malformed_responses_all_rejected` | each rejection reason produces a row and zero downstream effects | FR-122, SC-106 |
| `test_replay_makes_no_requests` | `model_requests_made == 0` | FR-130, SC-107 |
| `test_no_credentials_in_recorded_requests` | credential-shaped scan over `model_interactions` | FR-127, SC-112 |
| `test_starts_with_no_model_configured` | queue, ranking and settlement all work with `NullModelClient` | FR-126, SC-113 |

---

## Stated limitation (FR-143)

**Agents sharing one underlying model are not independent reasoners.** The evidence-independence
discount keys on the evidence group a forecast relied on, not on the reasoner that produced it. Two
agents holding disjoint evidence but calling the same model may fail in the same direction for
reasons the mechanism does not discount. This is a genuine new limitation introduced by this
feature. It is documented, not engineered around, and it must appear in the README and in demo
narration alongside the three limitations feature 001 already states.
