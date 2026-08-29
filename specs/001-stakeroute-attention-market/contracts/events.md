# Contract: Event Subjects (NATS JetStream)

**Constitution**: Principle III. **Research**: D-003, D-005.

Stream `STAKEROUTE`, subjects below, file-backed, at-least-once delivery, explicit acknowledgement,
30s ack wait. Every consumer is a durable pull consumer so redelivery after worker death is the
broker's behaviour, not ours.

**Universal envelope** — every message on every subject:

```json
{
  "tenant_id": "acmepay",
  "event_id": "<sha256 hex>",
  "emitted_at_ms": 1756450000000,
  "payload": { }
}
```

`event_id` is computed per D-005 and is the deduplication key at every consumer.

---

## `signals.raw` — simulator → worker

```json
{
  "source": "payments",
  "source_event_id": "pay-4471",
  "observed_at_ms": 1756449998000,
  "provenance": { "system": "acmepay-gateway", "collector": "sim", "raw_ref": "batch-12" },
  "payload": { "metric": "auth_failure_rate", "value": 0.31, "host": "gw-03" }
}
```

**Consumer obligation**: insert into `events` with `ON CONFLICT DO NOTHING`. A zero-row result
means this signal is already applied — acknowledge and apply no effect.

---

## `forecasts.created` — simulator → worker

```json
{
  "hypothesis_id": "h-payment-failure",
  "agent_id": "payment-agent-1",
  "probability": 0.89,
  "stake": 30,
  "evidence_cluster_id": "payment-gateway-telemetry",
  "evidence_refs": ["<event_id>", "<event_id>"],
  "expires_at_ms": 1756453200000
}
```

**Consumer obligation**: validate against FR-008 (stake range, available credits, probability
range) and reject explicitly with a recorded reason rather than dropping. On accept, move `stake`
from `available_credits` to `staked_credits` inside the same transaction that writes the forecast.

---

## `hypotheses.updated` — worker → dashboard

Emitted after each ranking pass. Carries all three strategies so the comparison is transported,
not recomputed in the UI.

```json
{
  "hypothesis_id": "h-payment-failure",
  "strategies": {
    "stakeroute":         { "probability": 0.81, "priority": 94.2, "rank": 1, "routed": true },
    "majority_vote":      { "probability": 0.44, "priority": 51.0, "rank": 3, "routed": false },
    "highest_confidence": { "probability": 0.98, "priority": 88.1, "rank": 2, "routed": false }
  },
  "contributions": [
    { "agent_id": "payment-agent-1", "probability": 0.89, "stake": 30,
      "cluster": "payment-gateway-telemetry", "cluster_size": 1,
      "independence": 1.0, "weight": 4.87, "alpha": 0.41 }
  ],
  "reason": "6 independent evidence groups; 42 correlated reports discounted",
  "withheld_count": 2
}
```

---

## `outcomes.resolved` — simulator or operator → worker

```json
{
  "hypothesis_id": "h-payment-failure",
  "outcome": 1,
  "resolved_by": "operator",
  "resolved_at_ms": 1756452000000
}
```

**Consumer obligation** — the strictest in the system. Settlement runs inside one transaction:
insert `settlements` rows with `UNIQUE(forecast_id)`, apply integer credit deltas, update
reputations, release stakes, mark the hypothesis `resolved`. Commit. *Then* acknowledge. A
redelivered resolution conflicts on `UNIQUE(forecast_id)` and applies nothing.

---

## Ordering guarantee

Processing order is fixed and is the whole idempotency argument:

```
receive → BEGIN → insert event (ignore on conflict) → apply effect → COMMIT → ACK
```

Acknowledging before committing would convert duplicates into losses. The chosen order means the
only possible failure is a duplicate, and the uniqueness constraints make a duplicate a no-op.
*Satisfies FR-003, FR-004, SC-005.*
