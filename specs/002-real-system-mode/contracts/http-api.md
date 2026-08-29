# Contract: HTTP API Additions — Real System Mode

**Process**: `dashboard` (FastAPI). **Research**: D-018, D-019, D-020, D-022.

**Requirements**: FR-109, FR-124, FR-125, FR-139 – FR-142, FR-144.

Feature 001's endpoints are unchanged in shape. This document covers the additions and the three
existing responses that gain fields. No authentication, as before — out of scope.

**One global rule**: every endpoint that returns data takes an explicit `tenant` query parameter
(default `hostops` in real mode, `acmepay` in simulated mode) and **refuses a request naming more
than one**. Cross-tenant aggregation must require new code, not a missing filter (D-018, SC-117).

---

## `GET /api/mode`

The answer to FR-139 and SC-110 — an operator must be able to tell, in one interaction, what they
are looking at.

```json
{
  "mode": "real",
  "tenant": "hostops",
  "since_ms": 1756449000000,
  "model": {
    "state": "degraded",
    "detail": "3 consecutive timeouts",
    "unavailable_capabilities": ["hypothesis_proposal"],
    "usage": { "calls_this_hour": 41, "ceiling": 60 }
  },
  "sources": [
    { "id": "host.metrics",     "state": "live",   "last_seen_ms": 1756449998000, "set_aside_count": 0 },
    { "id": "app.logs",         "state": "quiet",  "last_seen_ms": 1756449102000, "set_aside_count": 0 },
    { "id": "repo.vcs_tests",   "state": "silent", "last_seen_ms": 1756441000000, "set_aside_count": 0 },
    { "id": "container.events", "state": "absent", "last_seen_ms": null,
      "absent_reason": "docker daemon unreachable" }
  ]
}
```

`mode` is one of `real | sim | replay` and is never inferred — it is read from the records
themselves (D-019). `sources` distinguishes `quiet` (collector ran, host idle) from `silent`
(collector has stopped reporting) from `absent` (source does not exist here), which is the whole of
FR-141.

`model.unavailable_capabilities` names what is lost rather than leaving it to be inferred from an
absence (FR-124). It never contains ranking or settlement.

---

## `GET /api/queue` — additions

Existing fields unchanged. Real-mode entries gain:

```json
{
  "attention_budget": 2,
  "slots_used": 1,
  "withheld_count": 3,
  "mode": "real",
  "routed": [
    {
      "hypothesis_id": "h-7f3a",
      "statement": "The pytest suite has been failing on tests/integration/test_expiry.py since the most recent commit",
      "probability": 0.74,
      "priority": 61.8,
      "rank": 1,
      "estimates": {
        "impact_minor_units": { "value": 250000, "basis": "3 failing test nodes across 1 module; estimator=impact_from_test_failures", "confirmed": false },
        "urgency":            { "value": 0.62,   "basis": "failure age 41m, trend steady; estimator=urgency_from_recency", "confirmed": false },
        "review_cost":        { "value": 1.0,    "basis": "single-module scope; estimator=cost_from_scope", "confirmed": false }
      },
      "cited_observation_count": 6,
      "condition": { "name": "test_failing", "params": { "node_id": "tests/integration/test_expiry.py::test_expiry_returns_stake" } },
      "duplicate_of": null
    }
  ],
  "flagged_duplicates": [
    { "hypothesis_id": "h-9c21", "probable_duplicate_of": "h-7f3a", "basis": "same bound condition and parameters" }
  ]
}
```

`estimates` carries `basis` and `confirmed` on every value — FR-108 requires the basis recorded and
FR-109 requires an estimated attribute be visibly distinguishable from an operator-confirmed one.
`confirmed: false` is not a defect; it is the honest default.

`withheld_count` remains mandatory (Principle IV). `flagged_duplicates` is the visible half of
FR-110 — a merged duplicate simply does not appear twice, a flagged one is shown as flagged.

---

## `GET /api/hypotheses/{id}/trace`

FR-140 — trace a queued hypothesis to the real observations behind it and the rationales that
supported it. Complements `/explain`, which stays exactly as feature 001 defined it (the weight
arithmetic).

```json
{
  "hypothesis_id": "h-7f3a",
  "mode": "real",
  "proposal": {
    "proposal_id": "p-22b8",
    "interaction_id": "mi-4410",
    "statement": "…",
    "model_name": "gemini-…"
  },
  "observations": [
    { "event_id": "a3f1…", "source": "repo.vcs_tests", "observed_at_ms": 1756449102000,
      "payload": { "test_node_id": "…", "test_outcome": "failed" },
      "redactions_applied": ["path"] }
  ],
  "forecasts": [
    { "agent_id": "vcs-reasoner", "probability": 0.81, "stake": 20,
      "rationale": "The failing node appeared in the same commit that touched expiry handling…",
      "evidence_scope": ["repo.vcs_tests"],
      "bundle_ref": "fb-9911" }
  ],
  "resolution": {
    "determination": "automatic",
    "check_name": "test_failing",
    "check_result": "failed",
    "checked_at_ms": 1756452600000,
    "arrived_at_ms": 1756452600000,
    "corrections": []
  }
}
```

Every element is a recorded row. `redactions_applied` makes the redaction boundary visible to the
operator rather than silent (D-014).

---

## `POST /api/estimates/{hypothesis_id}/confirm`

FR-109 — an operator corrects or confirms an estimated attribute.

```json
{ "attribute": "impact", "value": 900000, "note": "this module gates the release" }
```

Inserts a **new** `attribute_estimates` row with `confirmed_by_operator = 1` and sets
`superseded_by` on the prior row. Estimates are never updated in place, so the correction history
is auditable (data-model.md). Returns the new estimate and the ranking effect it had.

---

## `GET /api/metrics` — additions

Every field remains nullable, and `null` still means **not yet measured**. Real mode adds a second
reason for `null`: too few resolved outcomes (FR-118, FR-142, D-022).

```json
{
  "tenant": "hostops",
  "mode": "real",
  "precision_at_k": null,
  "false_escalation_rate": null,
  "mean_brier_score": null,
  "measured_over_outcomes": 3,
  "minimum_for_calibration": 10,
  "insufficient": true,
  "insufficient_reason": "3 resolved outcomes; 10 required",
  "provenance": "measured-real"
}
```

`insufficient: true` is the machine-readable form of "the system does not yet know", and the
surface must render it as such rather than as a zero. `provenance` is `measured-real |
measured-simulated` and is required on every metrics response (FR-144, SC-109) — a figure that
cannot say which world it came from is not displayable.

---

## `GET /api/agents` — additions

```json
{
  "agents": [
    { "id": "metrics-reasoner", "reputation": 0.71,
      "available_credits": 62, "staked_credits": 38,
      "evidence_scope": ["host.metrics"],
      "measured_calibration": null,
      "resolved_forecast_count": 4,
      "insufficient": true }
  ]
}
```

**There is no `accuracy` field.** FR-117 forbids displaying a configured accuracy figure in real
mode; `measured_calibration` is derived from settlements on read and is `null` below the minimum
(SC-114). The absence of that field is the requirement being met, so it should not be added back
"for parity with the simulator".

---

## `POST /api/replay`

FR-129 – FR-131, SC-107.

```json
{ "window_start_ms": 1756440000000, "window_end_ms": 1756460000000 }
```

Runs the recorded window into a scratch database and compares (D-012). Response:

```json
{
  "replay_run_id": "rp-1a2b",
  "identical": false,
  "records_compared": 184,
  "model_requests_made": 0,
  "first_divergence": {
    "record": "attention_decisions",
    "id": "h-7f3a@1756449900000",
    "field": "priority",
    "expected": 61.8,
    "actual": 61.4
  }
}
```

`model_requests_made` is counted, not asserted — it is the evidence for SC-107. The source database
is never written to except for the `replay_runs` row itself, so a divergence leaves evidence of
itself rather than overwriting what it disagreed with (FR-131).

---

## `GET /api/model/interactions`

FR-123 — the model boundary must be inspectable, not merely recorded.

```json
{
  "interactions": [
    { "id": "mi-4410", "purpose": "proposal", "accepted": true,  "latency_ms": 2840,
      "requested_at_ms": 1756449100000, "model_name": "gemini-…" },
    { "id": "mi-4411", "purpose": "forecast", "accepted": false, "latency_ms": 10002,
      "rejection_reason": "TIMEOUT", "agent_id": "logs-reasoner" }
  ],
  "totals": { "accepted": 38, "rejected": 4, "by_reason": { "TIMEOUT": 3, "PROBABILITY_OUT_OF_RANGE": 1 } }
}
```

Requests and responses are retrievable per-interaction at `/api/model/interactions/{id}`. Both are
already redacted (they are stored redacted, D-014), and credentials appear in neither (FR-127,
SC-112).

---

## `POST /api/scenario/{action}` — unchanged

The feature 001 demo controls keep working against `acmepay` exactly as before (SC-108, FR-132).
They are rejected with `409` when the requested tenant is `hostops`: the seeded scenario cannot be
injected into real mode, which is the tenancy boundary doing its job.

Real mode has **no** equivalent injection endpoint. A fault is induced by the operator on the host
— fill a volume, saturate a core, kill a process, break a test — for the same reason feature 001
made worker termination a terminal command rather than an endpoint: a fault the application can
trigger is not a credible demonstration of a fault (SC-116).
