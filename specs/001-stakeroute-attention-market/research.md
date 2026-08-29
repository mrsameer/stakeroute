# Phase 0 Research: StakeRoute

**Feature**: 001-stakeroute-attention-market
**Date**: 2026-08-29
**Input**: [spec.md](./spec.md), [constitution](../../.specify/memory/constitution.md)

All technology direction supplied with the feature request was treated as a proposal to
evaluate, not a decision to ratify. Two proposals were rejected. Every decision below is
recorded with what it costs, because Constitution Principle V requires the trade-off to be
stated rather than discovered by a judge.

---

## D-001: Deterministic core as a pure library

**Decision**: The mechanism lives in `src/stakeroute/core/` as pure, synchronous, side-effect-free
functions over plain dataclasses. No I/O, no async, no database handles, no clock reads, no
unseeded randomness. Transport and storage depend on the core; the core depends on nothing.

**Rationale**: Constitution Principle I requires the decision path to be deterministic and
reproducible. That is a property of code that has no ambient inputs. Making the core pure is
the cheapest possible enforcement — a violation becomes a type error or an import error rather
than a subtle test flake. It also means the entire mechanism is unit-testable in milliseconds
with no containers, which matters when the mechanism must be correct before anything else is
built (Principle VI).

**Alternatives considered**:
- *Core as a service layer with injected repositories*. Rejected: still permits a clock or RNG
  to leak in through a dependency, and makes the fast test suite depend on mock plumbing.
- *Core methods on ORM models*. Rejected: couples the mechanism to storage, which is exactly
  the coupling that would make "is this reproducible?" unanswerable.

**Cost**: The core cannot lazily load anything. All inputs to a ranking pass must be gathered by
the caller and handed in as a snapshot. This is a real ergonomic tax and is accepted.

---

## D-002: Integer credit ledger

**Decision**: Agent credits are integers in whole credit units. Stakes are integers. Settlement
deltas are integers, computed by scaling the real-valued score improvement and rounding
half-to-even at the single point where the ledger is written. Floating point never touches a
stored balance.

**Rationale**: Principle III requires that replaying an event stream produce identical final
balances. Floating-point accumulation does not guarantee that under reordering, and reordering
is exactly what redelivery causes. An integer ledger makes replay-equality exact rather than
approximate. It also removes an entire category of demo-day embarrassment (a balance reading
`99.99999999999999`).

**Alternatives considered**:
- *`Decimal` balances*. Rejected: solves representation but not order-dependence, and is slower.
- *Float balances with tolerance-based assertions*. Rejected: "our ledger reconciles to within
  epsilon" is not a claim worth defending in front of an infrastructure engineer.

**Cost**: Settlement resolution is quantised. With a 1–50 stake range and a scale factor of 100,
the smallest representable settlement is 1/100 of a credit, which is far below any visible
effect. Accepted.

---

## D-003: NATS JetStream for durable ingestion, behind a transport interface

**Decision**: Use NATS JetStream as the signal bus, accessed only through a narrow
`SignalTransport` protocol. A second in-process driver implementing the same protocol backs the
test suite. Subjects: `signals.raw`, `forecasts.created`, `hypotheses.updated`, `outcomes.resolved`.

**Rationale**: The kill-and-recover demonstration (User Story 4, SC-005) needs genuine
at-least-once delivery with redelivery of unacknowledged messages. An in-memory queue cannot
demonstrate that and claiming it does would violate Principle V. JetStream persists messages,
redelivers on unacknowledged consumer death, and supports multiple workers sharing a consumer —
which is the honest answer to "how does this scale out?"

The transport interface exists for a specific reason beyond tidiness: the test suite must not
require Docker. Mechanism tests that need a broker running are mechanism tests that stop being
run under time pressure.

**Alternatives considered**:
- *Redis Streams*. Comparable consumer-group semantics; rejected because it adds no capability
  here and JetStream's acknowledgement model maps more directly to the idempotency argument.
- *Kafka*. Rejected: operationally heavy for a one-day build, and "Kafka because scale" is the
  exact non-argument the design is trying not to make.
- *A SQLite-backed outbox queue*. Genuinely tempting — it would remove a container. Rejected
  because the redelivery behaviour would then be our own code, and "our custom queue redelivers
  correctly" is a much weaker claim than pointing at a broker's documented guarantee.

**Cost**: One infrastructure container on demo day, and a hard dependency on it being up. Startup
ordering is handled by Compose health checks.

---

## D-004: SQLite over Postgres, with a Postgres-portable schema

**Decision**: SQLite in WAL mode as the demo store, accessed through SQL that avoids
SQLite-specific syntax. `event_id` carries a `UNIQUE` constraint; inserts use
`INSERT ... ON CONFLICT DO NOTHING`, which is valid in both engines.

**Rationale**: Principle VI says demo path first, and every container is a demo-day failure mode.
The durability story that matters lives in JetStream, not in the database — the database's only
job in the architecture argument is to make the economic effect idempotent, and a `UNIQUE`
constraint does that identically in both engines. Removing the Postgres container removes a
network dependency, a credentials path, a startup race, and a connection-pool tuning question,
in exchange for nothing that the architecture story needs.

**Alternatives considered**:
- *Postgres*. The stronger production answer and only marginally more Compose configuration.
  Rejected on demo-day risk, not on capability.

**Cost — stated explicitly, because it is the weakest point of this decision**: SQLite serialises
writers. The multi-worker scale-out story is therefore *described* rather than *demonstrated* —
the demo runs one worker. If a judge asks whether we can run four workers against this store, the
honest answer is: JetStream already supports the consumer sharing, the idempotency constraint
already makes it safe, and the store is the piece that would need to become Postgres. That is a
one-line Compose change and a connection-string change, and it is not done today. This limitation
MUST appear in the README under Principle V.

---

## D-005: `event_id` construction and the commit-then-acknowledge ordering

**Decision**:

```
event_id = sha256(tenant_id | source | source_event_id | floor(timestamp_ms / 1000))
```

Processing order is strictly: receive → begin transaction → insert event (ignore on conflict) →
apply economic effect → commit → acknowledge to JetStream.

**Rationale**: At-least-once delivery means a worker that dies after committing but before
acknowledging will see the message again. Committing before acknowledging guarantees the *only*
failure mode is a duplicate, never a loss. The uniqueness constraint then makes the duplicate a
no-op. Acknowledging first would invert this into silent data loss, which is unrecoverable.

The one-second timestamp bucket collapses genuine re-emissions of the same observation while
keeping distinct observations distinct at the resolution the simulator produces.

**Alternatives considered**:
- *Broker-level exactly-once semantics*. Rejected: pushes the guarantee somewhere we cannot
  inspect, and the interesting engineering claim is precisely that we do not need it.
- *Acknowledge-then-process*. Rejected: trades duplicates for losses, which is strictly worse.

**Cost**: Two observations from the same source with the same source id inside one second are
treated as one. Documented as intended deduplication, not a defect.

---

## D-006: Weighted-average aggregation, not log-odds

**Decision**: Aggregate as `P(h) = Σ αᵢ · pᵢ`, where `αᵢ` normalises
`wᵢ = reputationᵢ × √stakeᵢ × independenceᵢ`, and `independenceᵢ = 1 / √(cluster_size)`.

**Rationale**: Constitution Principle II is explicit that a mechanism the team can defend beats a
stronger one it cannot. Log-odds pooling is the better estimator and is the documented v2
direction, but it requires defending clamping behaviour, prior sensitivity, and why the result can
exceed every individual forecast — under time pressure, in front of judges. The weighted average
gives up estimator quality and buys back a mechanism that can be derived on a whiteboard in
thirty seconds.

The square root on stake is deliberate: influence must grow with conviction but sub-linearly, so
a single well-capitalised agent cannot buy proportional influence (FR-014).

**Alternatives considered**:
- *Log-odds pooling*. Deferred to v2 and named as such in the demo's trade-off segment.
- *Noisy-OR accumulation*. Rejected: appropriate for independent evidence, and the entire premise
  here is that the evidence is *not* independent.

**Cost**: The aggregate is bounded by the range of individual forecasts, so genuinely independent
corroboration cannot push confidence above its most confident member. This is a real loss of
sensitivity and is the first thing to change in v2.

---

## D-007: FastAPI single process serving API, WebSocket, and the page — no frontend build

**Decision**: One FastAPI process serves the JSON API, a WebSocket channel for live updates, and a
single static `index.html` with vanilla JavaScript. No npm, no bundler, no framework.

**Rationale**: The spec's out-of-scope list already kills auth, accounts and polish. A frontend
build step contributes nothing to any success criterion and introduces a toolchain that can fail
at 6pm. Vanilla JS against a WebSocket is entirely sufficient for a ranked list, an agent table,
and five buttons.

**Alternatives considered**:
- *React/Vite*. Rejected: build toolchain risk for zero criterion coverage.
- *Streamlit*. Genuinely faster to write; rejected because the kill-worker demo needs the page to
  survive backend death and reconnect, and Streamlit's execution model fights that.

**Cost**: The UI will look plain. This is aligned with the judging criteria, not a concession.

---

## D-008: Process topology — three application processes

**Decision**: `simulator`, `worker`, `dashboard`. Plus one `nats` infrastructure container.
Docker Compose with health checks and a restart policy on the worker to support
`docker compose kill worker`.

**Rationale**: The constitution caps this at three and requires justification beyond it. Three is
sufficient: the simulator produces signals and agent forecasts, the worker owns the deterministic
pipeline and the ledger, the dashboard reads and presents. Splitting further would demonstrate
service proliferation, not architectural maturity.

**Cost**: None identified.

---

## D-009: Determinism mechanics

**Decision**: Every scenario takes an explicit integer seed threaded into a per-scenario
`random.Random` instance — never the module-level global. Priority ties break on
`(-priority, -impact, hypothesis_id)`. Iteration over agents and forecasts is always over sorted
identifiers, never over dictionary or set order. The core receives timestamps as parameters and
never calls a clock.

**Rationale**: SC-006 requires identical rankings and balances on re-run. Every item above is a
known source of non-determinism that would otherwise silently break that guarantee.

**Cost**: Slightly noisier function signatures, since time must be passed explicitly. Accepted as
the enforcement mechanism for Principle I.

---

## D-010: Where model inference is permitted

**Decision**: Optional and confined to two places, both off the decision path — clustering raw
evidence into hypothesis candidates, and rendering a human-readable explanation of an already-
computed ranking. Both sit behind a feature flag defaulting to off. The demo runs with it off.

**Rationale**: Principle I is non-negotiable and cannot be waived by the complexity escape hatch.
Running the demo with inference disabled makes the claim verifiable rather than asserted: the
system demonstrably produces its rankings without a model in the loop. If a model is unavailable,
FR-040 is satisfied trivially because nothing on the path called it.

**Alternatives considered**:
- *LLM-generated hypothesis statements in the live demo*. Rejected: introduces latency and
  non-reproducibility into the one run that must be reproducible.

**Cost**: Hypothesis candidates come from deterministic clustering rules, which are less flexible
than a model would be. For the simulated domain this is sufficient.

---

## Resolved unknowns

| Unknown from Technical Context | Resolution |
|---|---|
| Message bus choice | D-003 — NATS JetStream behind a transport protocol |
| Storage engine | D-004 — SQLite, Postgres-portable schema, limitation stated |
| Aggregation formulation | D-006 — weighted average; log-odds deferred to v2 |
| Ledger numeric type | D-002 — integer credit units |
| Frontend approach | D-007 — no build step, vanilla JS |
| Idempotency key and ordering | D-005 — sha256 composite, commit before acknowledge |
| Determinism enforcement | D-009 — seeded RNG, sorted iteration, injected time |
| LLM boundary | D-010 — off the decision path, flagged off by default |

No `NEEDS CLARIFICATION` markers remain.
