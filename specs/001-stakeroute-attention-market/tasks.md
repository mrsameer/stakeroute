---

description: "Task list for StakeRoute — attention market for autonomous AI agents"
---

# Tasks: StakeRoute — Attention Market for Autonomous AI Agents

**Input**: Design documents from `/specs/001-stakeroute-attention-market/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts), [quickstart.md](./quickstart.md)

**Tests**: Required, not optional. The constitution's Development Workflow section mandates unit
tests for all mechanism code before it is wired into the pipeline, plus dedicated idempotency and
Sybil tests. Test tasks below are therefore first-class, not conditional.

## Phase ordering note — read before starting

The spec prioritises user stories P1 → P3. Constitution **Principle VI** fixes a different build
order: mechanism → simulation → baseline → attack → measurement → failure recovery → UI. Where the
two disagree, the constitution supersedes (Governance). Two deliberate consequences:

1. **US5 (P3, measurement) runs before US4 (P2, failure recovery).** Per plan.md: metrics computed
   over an in-process run are what tell us the mechanism works *before* infrastructure can obscure
   whether a failure is mechanical or operational.
2. **All browser UI is deferred to Phase 8.** Story phases 3–7 deliver headless, API-and-test
   testable increments. Each story's independent test is satisfied via its API and its test suite;
   the UI is presentation over facts already proven.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1–US5, mapping to spec.md user stories

---

## Phase 1: Setup

**Purpose**: Project skeleton and toolchain. No mechanism logic.

- [x] T001 Initialize uv project in `pyproject.toml` — Python 3.12; runtime deps `fastapi`, `uvicorn`, `nats-py`, `pydantic`; dev deps `pytest`, `anyio`, `ruff`, `pyright`
- [x] T002 [P] Configure `ruff` (line-length 88, import sorting) and `pyright` (strict) sections in `pyproject.toml`
- [x] T003 [P] Create package tree `src/stakeroute/{core,storage,transport,worker,dashboard,simulator}/__init__.py`
- [x] T004 [P] Create test tree `tests/unit/`, `tests/integration/`, and `tests/conftest.py` with an `anyio_backend` fixture
- [x] T005 [P] Create `data/` directory with a `.gitkeep`, and add `data/*.db` to `.gitignore` — resolves the missing DB path referenced by quickstart V4/V5
- [x] T006 Create `src/stakeroute/config.py` with `DB_PATH=data/stakeroute.db`, `ATTENTION_BUDGET=2`, `EPOCH_GRANT=100`, `STAKE_MIN=1`, `STAKE_MAX=50`, `REPUTATION_FLOOR=0.1`, `REPUTATION_CEIL=1.0`, `PROBABILITY_EPSILON=0.01`, `SETTLEMENT_SCALE=100`, `LLM_ENABLED=False`

**Checkpoint**: `uv sync` succeeds; `uv run pytest` collects zero tests without error.

---

## Phase 2: Foundational — The Mechanism (BLOCKING)

**Purpose**: The pure deterministic core, its enforcement, storage, transport abstraction, and the
seeded simulator. Constitution Principle VI: nothing in any user story begins until this is green.

**⚠️ CRITICAL**: Phase 2 must complete before ANY user story phase starts.

### Core types and enforcement

- [x] T007 Define frozen dataclasses `AgentSnapshot`, `ForecastSnapshot`, `HypothesisSnapshot`, `AggregationResult`, `RankedHypothesis`, `AllocationResult`, `Settlement` and domain errors `InvalidProbability`, `InvalidStake`, `InsufficientCredits`, `EmptyCluster`, `InvalidOutcome` in `src/stakeroute/core/types.py` per contracts/core-library.md
- [x] T008 [P] Implement `clamp_probability(p)` to `[0.01, 0.99]` in `src/stakeroute/core/types.py`
- [x] T009 [P] Write `tests/unit/test_core_purity.py` — walks `src/stakeroute/core/`, asserts no module imports `stakeroute.storage`, `stakeroute.transport`, `asyncio`, `datetime`, `time`, or `random`. This is the mechanical enforcement of Principle I

### Pure mechanism functions (each paired with its test)

- [x] T010 [P] Implement `independence_factor(cluster_size) -> 1/sqrt(n)` in `src/stakeroute/core/independence.py`
- [x] T011 [P] Write `tests/unit/test_independence.py` — full weight at size 1, monotonic decrease, raises `EmptyCluster` below 1
- [x] T012 Implement `influence_weight()` and `aggregate_probability()` returning per-forecast weight, independence and normalised α in `src/stakeroute/core/market.py`; returns the prior when the forecast set is empty
- [x] T013 Write `tests/unit/test_market.py` — sub-linear stake response, α sums to 1.0, empty set returns prior, explanation present on every result
- [x] T014 Implement `priority_score(probability, impact, urgency, review_cost)` and `allocate_attention(ranked, budget)` with tie-break `(-priority, -impact, hypothesis_id)` in `src/stakeroute/core/ranking.py`
- [x] T015 Write `tests/unit/test_ranking.py` — never returns more than `budget`; high-impact/moderate-probability outranks low-impact/high-probability; withheld count correct; ties deterministic across 100 shuffles
- [x] T016 [P] Implement `brier_score(probability, outcome)` in `src/stakeroute/core/scoring.py`
- [x] T017 [P] Write `tests/unit/test_scoring.py` — known values, raises `InvalidOutcome` for non-binary
- [x] T018 Implement `settle_forecast()` with integer credit delta, half-to-even rounding at `SETTLEMENT_SCALE`, loss floored at `−stake` in `src/stakeroute/core/settlement.py`
- [x] T019 Write `tests/unit/test_settlement.py` — the spec's worked example (prior .30, forecast .90, outcome 1 → improvement .48); loss never exceeds stake across a swept parameter grid; integer return type asserted
- [x] T020 [P] Implement `update_reputation()` with recency weighting and clamp to `[0.1, 1.0]` in `src/stakeroute/core/reputation.py`
- [x] T021 [P] Write `tests/unit/test_reputation.py` — bounds hold under adversarial input, decay makes old performance lose to recent, floor retains a recovery path

### Storage and transport

- [x] T022 Write `src/stakeroute/storage/schema.sql` — all tables from data-model.md, `tenant_id` on every table, `UNIQUE(event_id)`, `UNIQUE(forecast_id)` on settlements, `UNIQUE(hypothesis_id, agent_id)` on forecasts, Postgres-portable DDL only
- [x] T023 Implement `src/stakeroute/storage/repository.py` — connection in WAL mode, schema bootstrap, transactional `insert_event()` using `ON CONFLICT DO NOTHING` returning whether the row was new
- [x] T024 Implement repository writes for agents, hypotheses, forecasts, attention_decisions, outcomes, settlements, epochs in `src/stakeroute/storage/repository.py`
- [x] T025 [P] Define the `SignalTransport` protocol (`publish`, `subscribe`, `ack`) in `src/stakeroute/transport/protocol.py`
- [x] T026 [P] Implement the in-process driver in `src/stakeroute/transport/memory.py` with configurable redelivery, so the test suite never needs Docker

### Simulator

- [x] T027 [P] Implement agent accuracy profiles (payment specialist 90%, security 85%, general 70%, noisy 55%, malicious inverted, new-agent low reputation) in `src/stakeroute/simulator/agents.py`
- [x] T028 Implement seeded world generation in `src/stakeroute/simulator/scenarios.py` — ~500 noise signals, one true payment incident, one misleading database hypothesis, 6 honest agents; takes an explicit `random.Random(seed)`, never the module global
- [x] T029 Write `tests/integration/test_reproducibility.py` — same seed produces identical signals, identical rankings and identical integer balances (SC-006)

**Checkpoint**: `uv run pytest tests/unit -q` green with no infrastructure running. `uv run ruff check . && uv run pyright` clean. The mechanism is now correct before anything is built on it.

---

## Phase 3: User Story 1 — Operator receives only what is worth their attention (P1) 🎯 MVP

**Goal**: A ranked, budget-bounded queue of hypotheses with full traceability, served over HTTP.

**Independent test**: `POST /api/scenario/run_normal` with seed 42, then `GET /api/queue` returns exactly 2 routed hypotheses with the genuine payment incident at rank 1 and a non-zero `withheld_count` (quickstart V1, SC-001, SC-004).

- [x] T030 [US1] Implement hypothesis assembly — group forecasts by hypothesis, compute live `cluster_size` per ranking pass (never incrementally maintained) in `src/stakeroute/worker/pipeline.py`
- [x] T031 [US1] Implement the ranking pass — aggregate, score priority, allocate against `ATTENTION_BUDGET`, persist `attention_decisions` rows with rank, reason and JSON `contributions` in `src/stakeroute/worker/pipeline.py`
- [x] T032 [P] [US1] Implement `GET /api/queue` per contracts/http-api.md, with `withheld_count` as a required response field, in `src/stakeroute/dashboard/main.py`
- [x] T033 [P] [US1] Implement `GET /api/hypotheses/{id}/explain` returning prior, aggregate and per-forecast reputation/probability/stake/cluster/independence/weight/α in `src/stakeroute/dashboard/main.py`
- [x] T034 [US1] Implement `POST /api/scenario/run_normal` accepting a seed, driving the simulator through the in-process transport, in `src/stakeroute/dashboard/main.py`
- [x] T035 [US1] Write `tests/integration/test_queue_routing.py` — asserts exactly `budget` routed, correct rank 1, withheld count non-zero (SC-001, SC-004)
- [x] T036 [US1] Write `tests/integration/test_explain_traceability.py` — asserts every numeric field in `/api/queue` is reconstructible from `/explain`, closing the SC-007 validation gap found by `/speckit-analyze`

**Checkpoint**: US1 independently demonstrable via curl. This is the MVP.

---

## Phase 4: User Story 2 — The mechanism survives a coordinated attack (P1)

**Goal**: Baselines that visibly fail, attack injection that makes them fail, side-by-side comparison.

**Independent test**: Inject 50 Sybils; `GET /api/comparison` shows `majority_vote` ranking the false hypothesis first while `stakeroute` keeps the true incident in its top 2 (quickstart V2, SC-002).

- [x] T037 [P] [US2] Implement `rank_majority_vote()` — one unweighted vote per forecast above 0.5 — in `src/stakeroute/core/baselines.py`
- [x] T038 [P] [US2] Implement `rank_highest_confidence()` — rank by maximum self-reported probability — in `src/stakeroute/core/baselines.py`
- [x] T039 [US2] Write `tests/unit/test_baselines.py` — assert each baseline exhibits its intended failure mode; a baseline that does not break under attack is a broken baseline
- [x] T040 [US2] Persist all three strategies per ranking pass to `attention_decisions.strategy` in `src/stakeroute/worker/pipeline.py`
- [x] T041 [P] [US2] Implement Sybil injection — N new agents at floor reputation, attested flag false, backing a target hypothesis — in `src/stakeroute/simulator/stress.py`
- [x] T042 [P] [US2] Implement correlated-evidence injection — N agents citing one existing `evidence_cluster_id` — in `src/stakeroute/simulator/stress.py`
- [x] T043 [US2] Implement `POST /api/scenario/inject_sybils` and `POST /api/scenario/inject_correlated` in `src/stakeroute/dashboard/main.py`
- [x] T044 [P] [US2] Implement `GET /api/comparison` returning all three strategy rankings plus ground truth in `src/stakeroute/dashboard/main.py`
- [x] T045 [US2] Write `tests/integration/test_sybil.py` — SC-002 as an automated assertion: majority vote flips, StakeRoute holds top 2
- [x] T046 [US2] Write `tests/integration/test_correlated_evidence.py` — SC-003: aggregated probability moves ≤ 5 percentage points after 20 correlated forecasts
- [x] T047 [US2] Write `tests/integration/test_capital_exhaustion.py` — an agent staking maximally on every signal is rejected once its epoch budget is spent (US2 acceptance scenario 3)

**Checkpoint**: The attack demo works headlessly. This is the highest-value phase for judging.

---

## Phase 5: User Story 3 — Outcomes settle and the system remembers (P2)

**Goal**: Ground truth moves credits and reputation, with a complete audit trail.

**Independent test**: Resolve the payment hypothesis true; agents beating the prior gain, agents worse than the prior lose, no loss exceeds its stake (quickstart V5, SC-008, SC-009).

- [x] T048 [US3] Implement stake locking on forecast accept — move `stake` from `available_credits` to `staked_credits` in the same transaction as the forecast write — in `src/stakeroute/storage/repository.py`
- [x] T049 [US3] Implement forecast validation per FR-008 — stake range, available credits, probability range — recording explicit rejections rather than dropping, in `src/stakeroute/worker/pipeline.py`
- [x] T050 [US3] Implement `settle_hypothesis()` — insert settlements, apply integer deltas, update reputations, release stakes, mark resolved, all in one transaction — in `src/stakeroute/worker/settlement_runner.py`
- [x] T051 [US3] Implement expiry handling — hypotheses past `deadline_ms` become `expired`, stakes returned in full, reputation untouched, no outcome inferred (FR-028) — in `src/stakeroute/worker/settlement_runner.py`
- [x] T052 [US3] Implement `POST /api/scenario/resolve` publishing to `outcomes.resolved` in `src/stakeroute/dashboard/main.py`
- [x] T053 [P] [US3] Implement `GET /api/agents` returning reputation, available and staked credits, last forecast, last settlement in `src/stakeroute/dashboard/main.py`
- [x] T054 [US3] Write `tests/integration/test_settlement_flow.py` — SC-008 direction of movement for every agent class; SC-009 loss cap asserted via the quickstart V5 SQL
- [x] T055 [US3] Write `tests/integration/test_expiry.py` — expired hypothesis returns stakes exactly and leaves reputation unchanged

**Checkpoint**: The feedback loop closes. Reputation is now earned rather than assigned.

---

## Phase 6: User Story 5 — The operator can see that the mechanism works (P3)

**Runs before US4 by constitution Principle VI** — measure the mechanism before infrastructure can
confuse a mechanical failure with an operational one.

**Goal**: Five metrics, all computed from recorded run data.

**Independent test**: `GET /api/metrics` returns non-null values for all five with `measured_over_events` matching the real event count (quickstart V7, SC-012).

- [x] T056 [P] [US5] Implement `precision_at_k()` and `false_escalation_rate()` over `attention_decisions` joined to `outcomes` in `src/stakeroute/metrics.py`
- [x] T057 [P] [US5] Implement `time_to_attention()` as `decided_at_ms − created_at_ms` for true incidents in `src/stakeroute/metrics.py`
- [x] T058 [P] [US5] Implement `mean_brier_score()` over settled forecasts in `src/stakeroute/metrics.py`
- [x] T059 [US5] Implement throughput measurement — events ingested per second and ranking-pass lag behind the newest ingested event — in `src/stakeroute/metrics.py`
- [x] T060 [US5] Implement `GET /api/metrics` with **nullable** fields, where `null` means not-yet-measured, plus `measured_over_events` and `run_id`, in `src/stakeroute/dashboard/main.py`
- [x] T061 [US5] Write `tests/integration/test_metrics_are_real.py` — asserts every metric traces to recorded rows and that no constant appears in `metrics.py` output paths (FR-034)
- [x] T062 [US5] Write `tests/integration/test_throughput.py` — sustained-rate run asserting ingest ≥ 1,000 events/sec and ranking lag within the agreed bound, closing the SC-011 validation gap found by `/speckit-analyze`

**Checkpoint**: Claims become measurements.

---

## Phase 7: User Story 4 — Pipeline survives failure without corrupting the economy (P2)

**Goal**: Real durability. JetStream, a real worker process, and a kill that recovers cleanly.

**Independent test**: `docker compose kill worker`, keep publishing, restart, then the settlements duplicate-check query returns zero rows (quickstart V4, SC-005).

- [ ] T063 [US4] Implement the JetStream driver against `SignalTransport` — durable pull consumers, explicit ack, 30s ack wait, stream `STAKEROUTE` — in `src/stakeroute/transport/jetstream.py`
- [ ] T064 [US4] Implement `event_id = sha256(tenant_id|source|source_event_id|floor(ts_ms/1000))` in `src/stakeroute/core/types.py` and apply it at every publish site
- [ ] T065 [US4] Implement the worker consume loop with strict `receive → BEGIN → insert → effect → COMMIT → ACK` ordering in `src/stakeroute/worker/main.py`
- [ ] T066 [US4] Implement subject handlers for `signals.raw`, `forecasts.created` and `outcomes.resolved` per contracts/events.md in `src/stakeroute/worker/main.py`
- [ ] T067 [US4] Implement `hypotheses.updated` publication after each ranking pass, carrying all three strategies and contributions, in `src/stakeroute/worker/pipeline.py`
- [ ] T068 [US4] Write `tests/integration/test_idempotency.py` — redeliver every message class twice via the memory driver; assert one `events` row, one `settlements` row per forecast, identical final balances (SC-005, FR-003)
- [ ] T069 [US4] Write `tests/integration/test_crash_recovery.py` — simulate consumer death before ack; assert redelivery completes the work and applies no second economic effect
- [ ] T070 [US4] Write `docker-compose.yml` — `nats` with a health check, plus `worker`, `dashboard`, `simulator`; worker restart policy set so `docker compose start worker` recovers
- [ ] T071 [US4] Write `Dockerfile` for the three application processes
- [ ] T072 [US4] Execute quickstart V4 end to end and record the duplicate-settlement query result in the run log

**Checkpoint**: The architecture claim is now demonstrable rather than described.

---

## Phase 8: User Interface

Deferred to here by Principle VI. Everything below presents facts already proven by phases 3–7.

- [ ] T073 [US1] Build the single-page shell with the ranked queue, attention budget, per-hypothesis probability, impact, independent-evidence count and withheld count in `src/stakeroute/dashboard/static/index.html`
- [ ] T074 [US1] Implement click-to-expand traceability calling `/explain` in `src/stakeroute/dashboard/static/index.html`
- [ ] T075 [P] [US2] Build the three-strategy comparison panel with ground-truth highlighting in `src/stakeroute/dashboard/static/index.html`
- [ ] T076 [P] [US2] Add the attack control buttons — run normal, inject Sybils, inject correlated evidence, resolve outcome — in `src/stakeroute/dashboard/static/index.html`
- [ ] T077 [P] [US3] Build the agent table showing reputation, stake, forecast and settlement state in `src/stakeroute/dashboard/static/index.html`
- [ ] T078 [P] [US5] Build the metrics strip, rendering `null` as "not measured" rather than as a zero, in `src/stakeroute/dashboard/static/index.html`
- [ ] T079 [US4] Implement `WS /api/live` server push in `src/stakeroute/dashboard/main.py`
- [ ] T080 [US4] Implement client reconnect with exponential backoff and full repaint from `/api/queue`, so the page survives the worker being killed, in `src/stakeroute/dashboard/static/index.html`

**Checkpoint**: The five-minute demo is clickable.

---

## Phase 9: Polish, Honesty, and Rehearsal

Includes remediation for the findings from `/speckit-analyze`.

- [ ] T081 Amend **FR-031** in `specs/001-stakeroute-attention-market/spec.md` — scope the required controls to the four scenario actions and state that component termination is operator-executed out-of-band. Resolves finding F1, where the spec's MUST contradicts contracts/http-api.md
- [x] T082 **DONE** — Added "Team Allocation and Adversarial Review" to `specs/001-stakeroute-attention-market/plan.md` and corrected the Development Workflow gate to cite it. Resolves finding D1. Placed in plan.md rather than spec.md as originally worded: staffing is an execution concern, and spec.md is written for non-technical stakeholders. The single-contributor independence limitation is stated in that section under Principle V
- [ ] T083 [P] Define a measurable threshold for **SC-011** in `spec.md` (ranking-pass lag bound at the stated ingest rate). Resolves ambiguity finding B1; T062 already implements the test
- [ ] T084 [P] Add **FR-044** to `spec.md` authorising one live forecast per agent per hypothesis with replace-on-resubmit. Resolves finding F2 — this mechanism decision currently exists only as a schema constraint
- [ ] T085 [P] Add urgency and review cost to **FR-012** and to the Hypothesis entity in `spec.md`. Resolves finding C1
- [ ] T086 [P] Standardise terminology in `spec.md` — "evidence cluster" in code and data, "evidence group" in UI copy, stated once. Resolves finding F4
- [ ] T087 [P] Produce `architecture.png` — the single diagram: ingest, durable stream, blackboard, agents, attention market, allocator, top-K human queue, settlement engine
- [ ] T088 [P] Extend `README.md` with the run instructions, the measured results actually obtained, and the constitution's Principle V limitations already listed
- [ ] T089 Run the full quickstart V1–V8 from `docker compose down -v` and record real outputs in `specs/001-stakeroute-attention-market/run-log.md` — no fabricated figures (FR-034)
- [ ] T090 Rehearse the five-minute demo: V1 → V2 → V4 → V5, closing on the three unproven claims. Time it; SC-010 is a hard cap
- [ ] T091 Final gate — `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest` all clean before freeze

---

## Dependencies

```
Phase 1 Setup
    ↓
Phase 2 Foundational (BLOCKING — mechanism correct before anything is built on it)
    ↓
Phase 3 US1 (P1) ──→ MVP boundary
    ↓
Phase 4 US2 (P1)   [needs US1's ranking pass to compare against]
    ↓
Phase 5 US3 (P2)   [needs US2's agent population to settle]
    ↓
Phase 6 US5 (P3)   [needs US3's outcomes to compute precision and Brier]
    ↓
Phase 7 US4 (P2)   [swaps the memory transport for JetStream under a proven pipeline]
    ↓
Phase 8 UI         [presents what phases 3–7 already proved]
    ↓
Phase 9 Polish
```

**Story independence**: US1 stands alone. US2 requires US1's ranking pass. US3 requires an agent
population with forecasts. US5 requires settled outcomes. US4 is transport-layer and could in
principle run at any point after Phase 2 — it is placed late only because Principle VI puts failure
recovery after measurement.

## Parallel Opportunities

- **Phase 1**: T002–T005 all parallel
- **Phase 2**: T010/T011 with T016/T017 with T020/T021 — three independent mechanism functions and
  their tests. T025/T026 parallel with T027. The heaviest parallel window in the plan
- **Phase 4**: T037 with T038; T041 with T042
- **Phase 6**: T056, T057, T058 all parallel
- **Phase 8**: T075–T078 are separate UI panels
- **Phase 9**: T083–T088 all parallel

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (T001–T036).** That yields a working attention market with a
ranked, budget-bounded, fully traceable queue. It is demonstrable and defensible on its own.

**Highest judging value per hour is Phase 4.** If the day compresses, the order to protect is:
Phase 2 → Phase 3 → Phase 4 → Phase 7 → Phase 8. Phases 5, 6 and 9 are where to take the cut, in
that order — but note that cutting Phase 6 means the metrics strip shows "not measured", which is
the honest rendering and is preferable to inventing numbers (FR-034).

**Do not begin Phase 8 while any earlier phase is red.** Principle VI exists precisely because UI
work is the most tempting and least valuable thing to do under time pressure.

### Solo-build reality check

This is a confirmed single-contributor build in one day. 91 tasks will not all land, and planning
as though they will is how a demo ends up half-finished in every direction instead of finished in
one. Plan to the protected path and treat everything else as upside:

**Protected path — 65 tasks**: Phase 1 (6) → Phase 2 (23) → Phase 3 (7) → Phase 4 (11) →
Phase 7 (10) → Phase 8 (8). That yields the full five-minute demo: routing, the attack that breaks
the baselines, durable recovery, and a clickable screen.

**Phase 7 is the single largest schedule risk.** NATS plus Compose plus a Dockerfile is the part
most likely to consume an unplanned two hours, and it is the only part whose failure is
operational rather than logical. Timebox it. If it overruns, the fallback is not to skip the
durability story but to *demonstrate it differently*: T068 and T069 already prove idempotency and
crash recovery through the in-process driver with configurable redelivery, so the claim can be
shown as a passing test suite alongside the architecture diagram. That is weaker than killing a
live container, and it is far better than a broken `docker compose up` in front of judges.

**Cut order if the day compresses further**: Phase 9 polish beyond T087–T090, then Phase 6
(measurement), then Phase 5 (settlement). Note what each cut costs — dropping Phase 6 means the
metrics strip reads "not measured", which is the honest rendering under FR-034; dropping Phase 5
means reputation never visibly updates, which removes the demo's closing beat and weakens the
argument that influence is *earned*. Phase 5 is therefore the last thing to cut, not the first.

**What not to do**: do not compress Phase 2. The mechanism and its tests are the contribution, and
they are also what makes every later phase debuggable. A solo builder who skips the fast test loop
pays for it with interest during the afternoon.
