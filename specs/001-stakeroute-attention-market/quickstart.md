# Quickstart & Validation: StakeRoute

**Feature**: 001-stakeroute-attention-market

This is the validation guide. Each scenario maps to a success criterion in
[spec.md](./spec.md) and is the acceptance evidence for it.

## Prerequisites

- Python 3.12, `uv` (never `pip` — Constitution, Development Workflow)
- Docker with Compose v2 (for NATS and the kill-and-recover scenario)

## Setup

```bash
uv sync
```

## Fast loop — the mechanism, no infrastructure

The deterministic core has no transport or storage dependency (D-001), so the mechanism suite
runs with nothing else up. This is the loop used while building.

```bash
uv run pytest tests/unit -q
```

```bash
uv run ruff format . && uv run ruff check . && uv run pyright
```

## Full stack

```bash
docker compose up --build
```

Brings up `nats`, `worker`, `dashboard`, `simulator`. Dashboard at http://localhost:8000.

---

## Validation scenarios

### V1 — Baseline routing (SC-001, SC-004)

```bash
curl -X POST localhost:8000/api/scenario/run_normal -d '{"seed": 42}'
```

**Expect**: `GET /api/queue` returns exactly 2 routed hypotheses with an attention budget of 2,
`withheld_count > 0`, and the genuine payment incident at `rank: 1`.

---

### V2 — Sybil attack (SC-002) — the money moment

```bash
curl -X POST localhost:8000/api/scenario/inject_sybils \
  -d '{"count": 50, "target": "h-database-saturation"}'
```

**Expect** from `GET /api/comparison`: `majority_vote` now ranks the false database hypothesis
first, while `stakeroute` still ranks the payment incident in its top 2.

**Fails if** StakeRoute also flips — meaning reputation weighting or the independence discount is
not biting hard enough against 50 new low-reputation identities.

---

### V3 — Correlated evidence (SC-003)

```bash
curl -X POST localhost:8000/api/scenario/inject_correlated \
  -d '{"count": 20, "cluster": "database-observability"}'
```

**Expect**: the affected hypothesis's aggregated probability moves by **≤ 5 percentage points**
despite 20 additional supporting forecasts. Record the before and after values; the delta is the
measurement.

---

### V4 — Worker death and recovery (SC-005) — the architecture moment

```bash
docker compose kill worker
```

Let the simulator keep publishing for ~15 seconds, then:

```bash
docker compose start worker
```

**Expect**: processing resumes from the unacknowledged backlog; no signal is lost. Verify no
double settlement:

```bash
sqlite3 data/stakeroute.db \
  "SELECT forecast_id, COUNT(*) c FROM settlements GROUP BY forecast_id HAVING c > 1;"
```

**Expect**: zero rows. A non-empty result is a hard failure of Principle III.

---

### V5 — Settlement (SC-008, SC-009)

```bash
curl -X POST localhost:8000/api/scenario/resolve \
  -d '{"hypothesis_id": "h-payment-failure", "outcome": 1}'
```

**Expect** from `GET /api/agents`: agents whose forecasts beat the prior gain reputation and
credits; agents whose forecasts were worse than the prior lose both. Then confirm the loss cap:

```bash
sqlite3 data/stakeroute.db \
  "SELECT s.id FROM settlements s JOIN forecasts f ON f.id = s.forecast_id
   WHERE s.credit_delta < -f.stake;"
```

**Expect**: zero rows.

---

### V6 — Reproducibility (SC-006)

Run V1 twice with the same seed against a fresh database, capturing rankings and final balances
each time.

**Expect**: byte-identical rankings and identical integer balances. Any divergence points at an
unseeded RNG, an unsorted iteration, or a clock read inside the core (D-009).

---

### V7 — Metrics are real (SC-012, FR-034)

**Expect** `GET /api/metrics` to return non-null values for all five metrics after a completed
run, with `measured_over_events` matching the actual event count. A hard-coded constant here is a
constitutional violation, and `null` is the correct value for anything not yet measured.

---

### V8 — Model unavailability (FR-040)

Run the stack with hypothesis-generation inference disabled — which is the **default** (D-010).

**Expect**: ranking and settlement operate normally. This is the verification that no model sits
on the decision path.

---

## Full demo rehearsal

The five-minute run (SC-010), in order: V1 → V2 → V4 → V5, narrating the trade-offs from the
README's "What this is not" section at the close. Rehearse from `docker compose down -v` so the
clean-start path is the one that has been tested.
