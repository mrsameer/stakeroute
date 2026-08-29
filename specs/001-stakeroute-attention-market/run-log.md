# Run Log: StakeRoute Quickstart Validation

**Date**: 2026-08-29
**Purpose**: Real, recorded output for every quickstart scenario (T089). No number below is
invented — each is copied from an actual `curl` response or `docker compose` session captured
while writing this log. Where a run was repeated, both runs are shown.

## Environment

- Local: `uv run uvicorn stakeroute.dashboard.main:app` (in-process `MemoryTransport`, no broker).
- Docker: `docker compose up --build` (real NATS JetStream, three separate processes: `simulator`,
  `worker`, `dashboard`), used specifically for V4.
- Test suite: 53 tests, `uv run pytest` — all green at the time of this run.

---

## V1 — Baseline routing (SC-001, SC-004)

```
POST /api/scenario/run_normal {"seed": 42}
-> {"seed":42,"routed":2,"withheld_count":3}
```

```json
GET /api/queue
{
  "attention_budget": 2, "slots_used": 2, "withheld_count": 3,
  "routed": [
    {"hypothesis_id": "h-payment-failure", "statement": "payment_gateway_failure",
     "probability": 0.6484923123662907, "impact_minor_units": 800000000,
     "rank": 1, "independent_evidence_groups": 6, "discounted_report_count": 0,
     "reason": "rank 1 of 5; within budget of 2"},
    {"hypothesis_id": "h-database-saturation", "statement": "database_saturation",
     "probability": 0.31789142795889774, "impact_minor_units": 150000000,
     "rank": 2, "independent_evidence_groups": 6, "discounted_report_count": 0,
     "reason": "rank 2 of 5; within budget of 2"}
  ]
}
```

**Result**: PASS. 5 candidate hypotheses generated (2 real + 3 minor), budget of 2, exactly 2
routed, 3 withheld, genuine payment incident at rank 1.

---

## V2 — Sybil attack (SC-002) — the money moment

```
POST /api/scenario/inject_sybils {"count": 50, "target": "h-database-saturation"}
-> {"injected":50,"target":"h-database-saturation"}
```

```json
GET /api/comparison
{
  "strategies": {
    "stakeroute": [
      {"rank":1,"hypothesis_id":"h-payment-failure","probability":0.6484923123662907},
      {"rank":2,"hypothesis_id":"h-database-saturation","probability":0.539224211984956}
    ],
    "majority_vote": [
      {"rank":1,"hypothesis_id":"h-database-saturation","probability":0.9107142857142857},
      {"rank":2,"hypothesis_id":"h-payment-failure","probability":0.8333333333333334}
    ],
    "highest_confidence": [
      {"rank":1,"hypothesis_id":"h-payment-failure","probability":0.9353335458660186},
      {"rank":2,"hypothesis_id":"h-database-saturation","probability":0.9291436575196209}
    ]
  },
  "ground_truth": {"h-payment-failure": 1, "h-database-saturation": 0}
}
```

**Result**: PASS. After 50 Sybils back the false hypothesis, **majority vote flips** to rank the
false `h-database-saturation` first (0.91). **StakeRoute holds**: the true `h-payment-failure`
stays at rank 1. Reputation weighting alone (Sybils start at the 0.1 floor) is what resists it —
`highest_confidence` happens to survive this particular attack too, since no single Sybil's
self-reported confidence exceeds the honest specialists'; it is `majority_vote` this scenario is
built to break.

---

## V3 — Correlated evidence (SC-003)

```
GET /api/hypotheses/h-database-saturation/explain -> aggregated_probability: 0.539224211984956

POST /api/scenario/inject_correlated
  {"count": 20, "cluster": "database-observability", "target": "h-database-saturation"}
-> {"injected":20,"cluster":"database-observability","target":"h-database-saturation"}

GET /api/hypotheses/h-database-saturation/explain -> aggregated_probability: 0.5515471427367656
```

**Result**: PASS. Delta = **1.23 percentage points** after 20 additional correlated forecasts —
well inside the ≤5pp bound. All 20 injected contributions appear in `/explain` sharing one
`cluster_size: 20`, confirming the independence discount (`1/√20 ≈ 0.224`) applied collectively
rather than each counting as fresh confirmation.

---

## V4 — Worker death and recovery (SC-005) — the architecture moment

Run against the real stack: `docker compose up --build` (nats + worker + simulator + dashboard).

```
12:01:23  events_before_kill = 543
          duplicate_settlements_before_kill = 0
12:01:23  docker compose kill worker        -> Container stake-route-worker-1 Killed
12:01:38  (15s later) docker compose ps -a worker
          -> stake-route-worker-1  Exited (137) 19 seconds ago
          stream messages while worker down: 571   (up from 543 — simulator kept publishing)
12:01:47  docker compose start worker       -> Container stake-route-worker-1 Started
12:02:06  worker log: "worker: existing state found (tenant=acmepay); resuming"
          worker log: "worker: applied N forecast(s)..." (backlog catch-up)
          events_after_recovery = 595
          duplicate_settlements_after_recovery = 0
          forecasts_after_recovery = 15  (unchanged — no double-application)
```

**Result**: PASS. Zero lost signals (event count only ever grew: 543 → 571 → 595). **Zero
duplicate settlements**, verified directly:

```sql
SELECT forecast_id, COUNT(*) c FROM settlements GROUP BY forecast_id HAVING c > 1;
-- 0 rows
```

**Two real bugs were found and fixed while first attempting this run**, not merely covered by a
test in hindsight:

1. `transport/jetstream.py`'s `subscribe()` looped internally until a subject went quiet. Once the
   simulator's post-burst trickle kept `signals.raw` non-empty, that loop never returned control to
   the worker's outer loop, so `forecasts.created` and `outcomes.resolved` were **never serviced at
   all** — a real starvation bug, reproduced live, not hypothetical. Fixed by bounding `subscribe()`
   to one fetch batch per call.
2. `worker/run_worker.py` called `reset_tenant()` unconditionally at every startup, which would
   silently wipe all pre-kill progress on every recovery — defeating the durability claim on the
   very demo built to prove it. Fixed to seed only on a genuine first start; a restart now logs
   `"existing state found; resuming"` and resumes instead.

A third issue — `sqlite3.OperationalError: database is locked` under multi-process access — was
resolved by making the worker the sole writer (the simulator publishes events only), reusing one
JetStream pull-subscription per subject instead of rebinding it every poll, and widening the
`Repository` retry window. This is exactly the SQLite multi-writer fragility D-004 already
discloses; the fix keeps the demo path within a single practical writer, which is what D-004 always
described as the working configuration.

---

## V5 — Settlement (SC-008, SC-009)

```
POST /api/scenario/resolve {"hypothesis_id": "h-payment-failure", "outcome": 1}
POST /api/scenario/resolve {"hypothesis_id": "h-database-saturation", "outcome": 0}
```

Selected agents from `GET /api/agents` after resolution:

| Agent | Reputation | Last settlement | Note |
|---|---|---|---|
| Payment Specialist | 0.50 → **0.87** | **+2** | Forecast 0.94 on the true incident — beat the prior |
| Security Specialist | 0.65 → **0.80** | **+1** | Forecast 0.84 on the true incident |
| Malicious Agent | 0.30 → **0.10** (floor) | **−18** | Forecast 0.15 on the true incident — confidently wrong |
| Sybils (50×) | 0.10 (unchanged, already at floor) | −1 to −4 each | Backed the false hypothesis; small stakes limited the loss |

**Result**: PASS. Every agent whose forecast improved on the prior gained reputation and credits;
every agent worse than the prior lost both (SC-008). Loss-cap check:

```sql
SELECT s.id FROM settlements s JOIN forecasts f ON f.id = s.forecast_id
WHERE s.credit_delta < -f.stake;
-- 0 rows
```

No agent's loss exceeded its stake (SC-009).

---

## V6 — Reproducibility (SC-006)

Ran the identical scenario (seed 42) twice against two fresh databases and compared `/api/queue`
with the wall-clock-derived `age_ms` field excluded (that field is expected to differ between two
runs made at different real times — it is not part of the mechanism's output).

**Result**: PASS — **byte-identical** on every other field: probability, priority, rank, reason,
and hypothesis ordering matched exactly across both runs.

---

## V7 — Metrics are real (SC-012, FR-034)

Before any resolution:

```json
{"precision_at_k": null, "false_escalation_rate": null, "time_to_attention_ms": null,
 "mean_brier_score": null, "events_per_second": 568.43, "measured_over_events": 515,
 "measured_over": {"precision_at_k": 0, "mean_brier_score": 0, "events_per_second": 515}}
```

After both hypotheses resolved:

```json
{"precision_at_k": 0.5, "false_escalation_rate": 0.5, "time_to_attention_ms": 0.0,
 "mean_brier_score": 0.21085723001564283, "events_per_second": 82.69,
 "measured_over_events": 517,
 "measured_over": {"precision_at_k": 2, "false_escalation_rate": 2,
                    "time_to_attention_ms": 1, "mean_brier_score": 12, "events_per_second": 517}}
```

**Result**: PASS. Every ranking-quality metric reads `null` — not a placeholder zero — until
ground truth exists, then populates from real recorded rows, each with a nonzero
`measured_over` count proving where it came from.

---

## V8 — Model unavailability (FR-040)

```
config.py: LLM_ENABLED = False   (the default)
grep -rn "openai|anthropic|llm|LLM" src/stakeroute/core/ src/stakeroute/worker/
-> no matches besides the LLM_ENABLED constant itself
```

**Result**: PASS, and stronger than the spec requires — there is no model call anywhere in
`core/` or `worker/` to disable in the first place. Ranking and settlement demonstrably operate
with zero model dependency, by construction rather than by a runtime flag being left off.

---

## Demo timing (SC-010)

The V1 → V2 → V5 API path (reset, run, inject Sybils, compare, resolve both outcomes, read
agents) completed in **~1 second** of actual computation. The V4 kill-and-recover cycle in the
Docker stack — kill, a deliberate 15s wait while the simulator keeps publishing, restart, confirm
recovery — took **~45 seconds** end to end. A presenter narrating all four scenarios comfortably
fits inside the 5-minute cap; the bottleneck is narration and the deliberate V4 pause, not
computation.

## Cost of attack (post-hoc economic analysis)

Added after the 91-task build, priced against the recorded seed-42 baseline run. Every input
is a number the ranking pass already wrote to the ledger — honest influence weight, aggregate
probability, vote tally, hypothesis parameters — so this reads the run rather than re-simulating
it.

```
COST OF ATTACK — what it takes to buy rank 1

  defender : payment_gateway_failure (64.8%)
  target   : database_saturation (31.8%, 6 honest forecasts, weight 13.34)

  strategy                     identities  credits   lost  verdict
  -------------------------------------------------------  ------------------------
  highest confidence                    1        0      0  free — identities only
  majority vote                        25        0      0  free — identities only
  StakeRoute (as ranked)                —        —      —  not purchasable
  StakeRoute (market only)             19      950    836  950 credits at risk

  identities needed, by reputation the attacker already holds:
    rep 0.10   19  ███████████████████
    rep 0.25    8  ████████
    rep 0.50    4  ████
    rep 0.75    3  ███
    rep 1.00    2  ██

  New identities start at the reputation floor (0.1), so that is what a Sybil flood gets. Each attacking identity is assumed to assert the probability ceiling and to cite its own distinct evidence group unless stated otherwise — the cheapest attack available, not the most convenient one to defend against.
```

**Reading it honestly.** The "as ranked" row is *not purchasable* mainly because the false
hypothesis concerns a lower-impact subsystem, and the allocator weights impact and urgency. That
is a policy defence any strategy could adopt, so claiming it as evidence for the market would be
overstating the result. The "market only" row removes that advantage — pricing the attack against
a target of equal impact — and is the defensible claim: 19 floor-reputation identities, 950
credits committed, 836 destroyed at settlement, versus 25 identities and zero capital to flip
majority vote.

**Verified against the pipeline, not the algebra.** The closed form predicts 25 identities to flip
majority vote. `tests/integration/test_cost_of_attack.py` injects exactly 25 Sybils through the
real ingestion path and asserts rank 1 flips, then injects 24 into a separate world and asserts it
does not. The unit suite does the same for StakeRoute's own aggregation: it builds the prescribed
Sybil population, runs it through `aggregate_probability`, and checks both that the count suffices
and that one fewer does not.

**What it does not cover.** A single epoch only. An adversary who forecasts honestly to earn
reputation and then spends it is not defended against; the frontier table is that attack's price
list — 2 identities at ceiling reputation versus 19 at the floor.

---

## Final gate

```
uv run ruff format .   -> no changes
uv run ruff check .    -> All checks passed!
uv run pyright         -> 0 errors, 0 warnings, 0 informations
uv run pytest -q       -> 81 passed
```
