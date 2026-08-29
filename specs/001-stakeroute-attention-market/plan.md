# Implementation Plan: StakeRoute — Attention Market for Autonomous AI Agents

**Branch**: `001-stakeroute-attention-market` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-stakeroute-attention-market/spec.md`

## Summary

StakeRoute makes escalation to a human cost something. Agents stake finite reputation capital
behind probabilistic claims; correlated evidence is discounted rather than counted as independent
corroboration; a fixed human review budget is spent on the highest expected-value hypotheses; and
settlement against ground truth moves influence toward agents that are demonstrably calibrated.

The technical approach centres on one decision: **the mechanism is a pure library with no
transport or storage dependency** (D-001). Everything else — the broker, the store, the API, the
dashboard — is plumbing arranged around it. That inversion is what makes the deterministic
decision path (Principle I) enforceable rather than aspirational, and it is what lets the entire
mechanism be tested in milliseconds before any infrastructure exists.

The durability story is deliberately small and specific: JetStream gives at-least-once delivery,
which *creates* duplicates, which is precisely why the economic ledger must be idempotent. That
causal chain — guarantee → consequence → design response — is the architecture argument, and it is
demonstrable by killing a container rather than describable on a slide.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI (API + WebSocket), `nats-py` (JetStream client), `pydantic`
(envelope validation at the boundary only — never inside the core), `uvicorn`. Standard-library
`sqlite3`. No frontend framework, no bundler (D-007).

**Storage**: SQLite in WAL mode, schema written to be Postgres-portable (D-004). `UNIQUE(event_id)`
and `UNIQUE(forecast_id)` are the two constraints the idempotency guarantee rests on.

**Testing**: `pytest` with `anyio` for async. Unit tests for the core require no infrastructure;
integration tests use an in-process transport driver rather than a live broker.

**Target Platform**: Linux containers via Docker Compose; developed on macOS.

**Project Type**: Single Python project, three application processes plus one infrastructure
container.

**Performance Goals**: Sustain the simulator's signal rate (target ≥ 1,000 events/sec ingest)
without the operator queue falling behind. Ranking pass under 100ms for the demo's hypothesis and
agent counts.

**Constraints**: Decision path fully deterministic and reproducible for a given seed. Integer
ledger. No model inference on the decision path. Demo must run end-to-end in under 5 minutes from
a clean start.

**Scale/Scope**: ~1,000 signals, 4–8 hypotheses, 6 honest agents plus up to 70 injected adversarial
or correlated agents, 1 tenant, 2 human review slots.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Gate | Pre-Phase 0 | Post-Phase 1 | Justification |
|---|---|---|---|
| **I. Deterministic Decision Path** (non-negotiable) | PASS | PASS | Core is a pure library (D-001); model inference confined to hypothesis candidate generation and explanation rendering, flagged off by default (D-010). Enforced by an import-linting test, not convention. |
| **II. Explainability Over Sophistication** | PASS | PASS | `aggregate_probability` returns its own explanation as part of the result type, making the unexplained form unrepresentable. Weighted average chosen over log-odds precisely on this ground (D-006). |
| **III. Exactly-Once Economics** | PASS | PASS | `UNIQUE(event_id)` and `UNIQUE(forecast_id)`; commit-then-acknowledge ordering (D-005); integer ledger makes replay-equality exact rather than approximate (D-002). |
| **IV. Attention Is a Budgeted Resource** | PASS | PASS | `allocate_attention` cannot return more than `budget` entries; `withheld_count` is a required field in the `/api/queue` response body. |
| **V. State What You Have Not Proved** | PASS | PASS | Three limitations already in README; metrics fields are nullable so "unmeasured" is representable without fabricating a number. The SQLite write-concurrency limitation (D-004) is added to that list. |
| **VI. Demo Path First** | PASS | PASS | Phase ordering below follows mechanism → simulation → baseline → attack → measurement → recovery → UI exactly. |
| **Additional Constraints** | PASS | PASS | `tenant_id` on every table from the first migration; seeded RNG with deterministic tie-breaks (D-009); probabilities clamped off 0 and 1; loss floored at `−stake`; three application processes (D-008). |
| **Development Workflow** | PASS | PASS | `uv` only; `pyright`, `ruff` at 88 columns; `pytest` with `anyio`; adversarial reviewer assigned in the spec's team allocation. |

**Violations requiring justification**: none. Complexity Tracking is empty.

**One gate deserves explicit note.** Principle V is satisfied only if D-004's cost is actually
published. The plan therefore treats "add the SQLite write-concurrency limitation to the README"
as a deliverable task, not documentation housekeeping — without it, the D-004 trade-off is
undisclosed and the gate fails.

## Project Structure

### Documentation (this feature)

```text
specs/001-stakeroute-attention-market/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 — 10 recorded decisions with costs
├── data-model.md        # Phase 1 — entities, constraints, state transitions
├── quickstart.md        # Phase 1 — 8 validation scenarios mapped to success criteria
├── contracts/
│   ├── core-library.md  # The pure mechanism's public surface
│   ├── events.md        # JetStream subjects and consumer obligations
│   └── http-api.md      # Dashboard API and WebSocket
├── checklists/
│   └── requirements.md  # Spec quality validation
└── tasks.md             # Phase 2 — created by /speckit-tasks, NOT by this command
```

### Source Code (repository root)

```text
src/stakeroute/
├── core/                    # PURE. No I/O, no async, no clock, no RNG. (D-001)
│   ├── types.py             # Frozen snapshot dataclasses, domain errors
│   ├── independence.py      # Evidence-cluster discount
│   ├── market.py            # Influence weights, aggregation + explanation
│   ├── ranking.py           # Priority score, attention allocation
│   ├── scoring.py           # Brier score
│   ├── settlement.py        # Integer credit settlement, loss cap
│   ├── reputation.py        # Bounded update with decay
│   └── baselines.py         # Majority vote, highest confidence
├── storage/
│   ├── schema.sql           # Postgres-portable DDL, tenant_id everywhere
│   └── repository.py        # Transactional writes, ON CONFLICT DO NOTHING
├── transport/
│   ├── protocol.py          # SignalTransport interface
│   ├── jetstream.py         # NATS driver
│   └── memory.py            # In-process driver for tests (no Docker)
├── worker/
│   └── main.py              # Consume → commit → ack pipeline
├── dashboard/
│   ├── main.py              # FastAPI: API, WebSocket, static
│   └── static/index.html    # Single page, vanilla JS, no build step
├── simulator/
│   ├── scenarios.py         # Seeded world generation
│   ├── agents.py            # Accuracy profiles incl. adversarial and new
│   └── stress.py            # Sybil and correlated-evidence injection
└── metrics.py               # precision@K, false-escalation, TTA, Brier, throughput

tests/
├── unit/                    # Core mechanism — no infrastructure required
│   ├── test_market.py
│   ├── test_ranking.py
│   ├── test_settlement.py
│   ├── test_reputation.py
│   └── test_core_purity.py  # Import-lint: core must not import storage/transport
├── integration/
│   ├── test_idempotency.py  # Redelivery produces no second settlement
│   ├── test_sybil.py        # SC-002 as an automated assertion
│   └── test_reproducibility.py  # SC-006: same seed, same balances
└── conftest.py

docker-compose.yml           # nats + worker + dashboard + simulator
architecture.png             # The single diagram for the demo
```

**Structure Decision**: Single Python project. The `core/` boundary is the load-bearing structural
choice — it is a dependency rule, not a naming convention, and `tests/unit/test_core_purity.py`
enforces it mechanically. `storage/` and `transport/` depend on `core/`; `core/` depends on
neither. Three application entry points (`worker`, `dashboard`, `simulator`) satisfy D-008.

## Phased Build Order

Constitution Principle VI fixes this ordering. Nothing in a later phase begins while an earlier
phase is incomplete.

| Phase | Deliverable | Gate to proceed |
|---|---|---|
| 1. Mechanism | `core/` + unit tests | Mechanism suite green with no infrastructure running |
| 2. Simulation | Seeded scenario world, agent accuracy profiles | Same seed reproduces identical worlds |
| 3. Baseline | Majority-vote and highest-confidence rankers | Three strategies rank the same event stream |
| 4. Attack | Sybil and correlated-evidence injection | SC-002 and SC-003 pass as automated tests |
| 5. Measurement | `metrics.py`, real numbers only | Five metrics computed from recorded run data |
| 6. Failure recovery | JetStream, storage, worker, idempotency | SC-005 passes: kill, restart, zero double settlement |
| 7. UI | Dashboard, WebSocket, demo controls | V1–V5 in quickstart runnable from the browser |
| 8. Rehearsal | Architecture diagram, README limitations, demo run | Full run under 5 minutes from `docker compose down -v` |

Phase 5 preceding phase 6 is deliberate: metrics computed over an in-process run are what tell us
the mechanism works *before* infrastructure can obscure whether a failure is mechanical or
operational.

## Complexity Tracking

No constitutional violations. This table is intentionally empty.
