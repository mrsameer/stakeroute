# Contract: HTTP + WebSocket API

**Process**: `dashboard` (FastAPI). **Research**: D-007.
No authentication — explicitly out of scope per the spec.

---

## `GET /api/queue`

The operator's screen. Satisfies FR-030, FR-020, FR-022.

```json
{
  "attention_budget": 2,
  "slots_used": 2,
  "withheld_count": 2,
  "routed": [
    {
      "hypothesis_id": "h-payment-failure",
      "statement": "payment_gateway_failure",
      "probability": 0.81,
      "impact_minor_units": 800000000,
      "priority": 94.2,
      "rank": 1,
      "independent_evidence_groups": 6,
      "discounted_report_count": 42,
      "reason": "6 independent evidence groups; 42 correlated reports discounted",
      "age_ms": 41000
    }
  ]
}
```

`withheld_count` is mandatory in the response body, not optional metadata — Principle IV
prohibits silent suppression.

---

## `GET /api/hypotheses/{id}/explain`

The Principle II drill-down. Satisfies FR-017.

```json
{
  "hypothesis_id": "h-payment-failure",
  "prior_probability": 0.30,
  "aggregated_probability": 0.81,
  "contributions": [
    { "agent_id": "payment-agent-1", "reputation": 0.94, "probability": 0.89, "stake": 30,
      "cluster": "payment-gateway-telemetry", "cluster_size": 1,
      "independence": 1.0, "weight": 4.87, "alpha": 0.41 }
  ]
}
```

Every number in `/api/queue` must be reconstructible from this response. That is the acceptance
test for FR-017, and it is checkable mechanically.

---

## `GET /api/comparison`

Side-by-side strategy rankings over the identical event stream. Satisfies FR-023, FR-032, SC-002.

```json
{
  "strategies": {
    "stakeroute":         [{ "rank": 1, "hypothesis_id": "h-payment-failure", "probability": 0.81 }],
    "majority_vote":      [{ "rank": 1, "hypothesis_id": "h-database-saturation", "probability": 0.83 }],
    "highest_confidence": [{ "rank": 1, "hypothesis_id": "h-database-saturation", "probability": 0.98 }]
  },
  "ground_truth": { "h-payment-failure": 1, "h-database-saturation": 0 }
}
```

---

## `GET /api/agents`

```json
{
  "agents": [
    { "id": "payment-agent-1", "reputation": 0.94, "available_credits": 70,
      "staked_credits": 30, "last_forecast": 0.89, "last_settlement": 11, "attested": true }
  ]
}
```

---

## `GET /api/metrics`

Satisfies FR-033, FR-034, SC-012. Every field is nullable, and `null` means **not yet measured**.
Fabricating a value here is a constitutional violation, so the schema makes "unmeasured" a
first-class state rather than forcing a placeholder number.

```json
{
  "precision_at_k": 1.0,
  "false_escalation_rate": 0.0,
  "time_to_attention_ms": 41000,
  "mean_brier_score": 0.0834,
  "events_per_second": 1240.5,
  "measured_over_events": 1000,
  "run_id": "run-3f9a"
}
```

---

## `POST /api/scenario/{action}`

Demo controls. Satisfies FR-031, FR-036.

| Action | Body | Effect |
|---|---|---|
| `run_normal` | `{ "seed": 42 }` | Baseline scenario: ~500 noise signals, 1 true incident, 1 false competitor |
| `inject_sybils` | `{ "count": 50, "target": "h-database-saturation" }` | New low-reputation agents backing the false hypothesis |
| `inject_correlated` | `{ "count": 20, "cluster": "database-observability" }` | Duplicate-evidence agents in one existing cluster |
| `resolve` | `{ "hypothesis_id": "...", "outcome": 1 }` | Publishes to `outcomes.resolved`, triggering settlement |

Worker termination is **not** an endpoint. It is `docker compose kill worker`, run from a terminal
during the demo, because a kill switch the application controls is not a credible failure test.

---

## `WS /api/live`

Server-push updates. Payloads mirror `hypotheses.updated`. The client reconnects with exponential
backoff and repaints from `GET /api/queue` on reconnect — the page must survive the worker being
killed, which is the point of the User Story 4 demonstration.
