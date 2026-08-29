# Implementation Plan: Real System Mode

**Branch**: `002-real-system-mode` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Constitution**: v1.1.0 — gate passes; see [Constitution Check](#constitution-check)

**Input**: Feature specification from `/specs/002-real-system-mode/spec.md`

## Summary

Feature 001 proved the mechanism against inputs the evaluator manufactured. Its agents are
handed the ground truth and sample near it (`simulator/agents.py::forecast_probability`), so
the project's strongest claim — that influence is *earned* from demonstrated calibration — is
computed correctly over numbers that were never at risk of being wrong.

This feature replaces the manufactured inputs with real ones: observations collected from the
host and repository, hypotheses proposed from those observations, agents that reason over a
disjoint slice of evidence with the outcome withheld, and outcomes determined by re-checking a
condition of the machine.

The technical approach turns on one decision: **the model names situations; deterministic code
prices them** (D-016). A language model may propose a hypothesis statement and cite the
observations behind it, but every number that reaches `priority_score` — impact, urgency, review
cost — is computed by a pure function in `stakeroute.core` from those cited observations. This is
what lets FR-108 ("each proposal carries an estimated impact") and FR-119 ("ranking must not read
any model output as a numeric input") both hold, rather than trading one for the other.

The second decision is the answer to the determinism risk: **reproducibility moves from
regenerable to replayable** (D-012). A live run cannot be regenerated, because a model does not
repeat itself. It can be replayed, because every non-deterministic input — model responses,
observations, and clock reads — is durably recorded before anything consumes it, and the decision
path already takes its timestamp as an argument and its core is already import-linted pure. Replay
drives the recorded inputs into a scratch database and compares, so a divergence is surfaced
rather than absorbed.

## Technical Context

**Language/Version**: Python 3.12 (unchanged).

**Primary Dependencies**: Existing — FastAPI, `nats-py`, `pydantic`, `uvicorn`, stdlib `sqlite3`.
Added — `google-genai` (Gemini via Vertex AI, D-011) and `psutil` (host and process metrics,
D-015). Git, test-run and container-event collection shell out to `git`, `pytest` and `docker`
rather than adding clients; an absent binary is an absent source, which FR-141 already requires be
visible.

**Storage**: SQLite as before, same Postgres-portable DDL. Six new tables (`observation_sources`,
`model_interactions`, `proposals`, `attribute_estimates`, `resolutions`, `replay_runs`) and a
`mode` column on the record tables. No change to the two uniqueness constraints the idempotency
guarantee rests on; three new ones are added on the same pattern.

**Testing**: `pytest` with `anyio`. Every feature-001 test must continue to pass unchanged
(SC-108) — that is a gate on this feature, not a hope. New unit tests for the pure additions
(estimates, decay, duplicate detection, response validation, redaction); new integration tests for
model-absence, replay equality, evidence-scope enforcement, and redaction of recorded requests.
The model is faked in all tests but one; a single opt-in live-credential test is marked and
skipped by default.

**Target Platform**: The host it runs on — macOS during development, Linux containers in Compose.
Collectors must degrade to "source absent" rather than fail where a platform lacks a source.

**Project Type**: Single Python project. Three application processes, unchanged (D-024): in real
mode the ingestor process takes the simulator's slot, with the reasoning agents running inside it.

**Performance Goals**: Ranking pass unchanged (<100ms at demo scale). Model calls are entirely off
the decision path and bounded by a configured per-request timeout (default 10s) and a per-interval
ceiling. Collector poll interval 2s. Ingest throughput target from feature 001 is unaffected — real
observation volume is orders of magnitude below the simulator's synthetic rate.

**Constraints**: No model inference on the aggregation, ranking or settlement path — enforced by
extending the existing import-linting purity test, not by assurance. Replay must issue zero
outbound model requests. Nothing leaves the host unredacted. Real and simulated records must not
share a tenant.

**Scale/Scope**: One host, one repository, 4 observation sources, 3–4 reasoning agents on disjoint
scopes, 2 tenants (`acmepay` simulated, `hostops` real), operation measured over hours to days
rather than a single scripted epoch.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Gate | Pre-Phase 0 | Post-Phase 1 | Justification |
|---|---|---|---|
| **I. Deterministic Decision Path** (non-negotiable) | PASS | PASS | The model proposes statements and renders prose only. Every number reaching `priority_score` is computed by a pure function in `core/estimates.py` from cited observations (D-016). Enforced by extending `test_core_purity.py` to forbid `stakeroute.model` imports anywhere under `core/`, plus a decision-path test that runs a full ranking pass with the model client set to a stub that raises on any call. |
| **II. Explainability Over Sophistication** | PASS | PASS | Attribute estimates carry their basis as a required field, not an optional one (`attribute_estimates.basis`). Duplicate detection is a stated rule over cited observations and bound conditions (D-023), not an embedding similarity score no one can derive on a whiteboard. |
| **III. Exactly-Once Economics** | PASS | PASS | Real observations reuse the existing `compute_event_id` and `UNIQUE(event_id)`. `UNIQUE(hypothesis_id, resolution_seq)` on resolutions and `UNIQUE(interaction_id)` on model interactions extend the same pattern. Redelivered outcomes and corrections are new rows, never overwrites (FR-135, FR-136). |
| **IV. Attention Is a Budgeted Resource** | PASS | PASS | `allocate_attention` is unchanged and still cannot return more than `budget`. Real evidence adds a second budget — the model usage ceiling (D-020) — which reports consumption and what it set aside rather than degrading opaquely (FR-105, FR-125). |
| **V. State What You Have Not Proved** | PASS | PASS | Five new limitations are deliverable tasks, not documentation housekeeping: shared-model correlation is undiscounted (FR-143), real-vs-simulated provenance on every figure (FR-144), "real" means unfabricated not production (FR-145), the observer loads the machine it observes (FR-149), and the demonstration domain is the host not a payment platform (FR-150). Calibration below the minimum resolved count reports insufficiency, not a number (D-022). |
| **VI. Demo Path First** | PASS | PASS | Phase order below is mechanism (pure estimators, decay, validation) → real evidence → reasoning agents → model-absence → measurement → replay → UI. The induced-fault demonstration (SC-116) is the last mechanism deliverable, ahead of any interface work. |
| **Additional Constraints** | PASS (under v1.1.0) | PASS (under v1.1.0) | Failed under constitution v1.0.0; the two contradicted constraints were amended in v1.1.0 (see below). Against the amended text: the *replayable* guarantee is named and met (D-012); ranking and settlement survive the model's absence and the system starts unconfigured (US3, FR-120, FR-126); degradation is surfaced by name and every interaction recorded (D-020, D-023). `tenant_id`, bounded values, deterministic tie-breaks and the three-process shape are unchanged and satisfied. |
| **Development Workflow** | PASS | PASS | `uv add google-genai psutil`; `pyright` and `ruff` at 88 columns; `pytest` with `anyio`. Adversarial review targets this feature's new surface: evidence-scope escape, unredacted egress, replay divergence absorbed silently, and duplicate settlement on redelivered outcomes. |

### Constitution amendment — landed as v1.1.0

Recorded here rather than in Complexity Tracking deliberately. Complexity Tracking is for a
violation that is *justified and accepted*; these two constraints were ones the project should
**change**, and changing them is a governance act under the constitution's amendment procedure,
not a plan-level waiver.

Both were amended in constitution **v1.1.0** (2026-08-29) before any implementation began:

1. **"Simulation only: No live third-party integrations. All external systems are simulated."**
   → **"External dependencies."** The blanket prohibition is narrowed to what it was actually
   protecting: no blockchain, no token issuance, no on-chain settlement, and **no dependency whose
   absence stops the decision path**. That last clause is the load-bearing one, and US3, FR-120 and
   FR-126 are what satisfy it. The amended text additionally requires that a permitted integration
   surface its degradation by name and record every interaction — obligations this plan already met
   at D-020 and in contracts/model-boundary.md.

2. **"Reproducibility."** Split into two named guarantees. *Regenerable* is unchanged in substance
   and still binds the seeded simulation. *Replayable* is new and binds this feature: record every
   non-deterministic input before consumption, replay from those inputs alone to byte-identical
   results, make no outbound request during replay, and surface divergence rather than overwriting
   the record. That is D-012 restated as a constitutional obligation. Claiming the regenerable
   guarantee for a merely replayable run is now explicitly prohibited, which is what FR-144
   enforces in the documentation.

Principle V also gained two obligations in the same amendment — shared-model correlation between
reasoners must be stated (this plan's D-013 cost, FR-143), and every displayed figure must name its
provenance as measured or simulated (FR-144). Both were already deliverables here; they are now
constitutional.

**Principle I is not in conflict** and no amendment to it is sought. It already permits model
inference to propose candidates and render explanations, and already requires ranking and
settlement to survive the model's absence. Feature 001 could not test that requirement, because
there was no model to remove. US3 is the first thing that actually proves it.

## Project Structure

### Documentation (this feature)

```text
specs/002-real-system-mode/
├── plan.md              # This file
├── research.md          # Phase 0 output — D-011 through D-024
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── model-boundary.md      # ModelClient protocol, response schemas, rejection taxonomy
│   ├── observations.md        # Source contract, envelope, dedup, redaction allow-list
│   └── http-api.md            # New and changed endpoints
├── checklists/
└── tasks.md             # /speckit-tasks output — NOT created here
```

### Source Code (repository root)

Additions to the existing single-project layout. Nothing under `core/` gains an import it did not
have; the new core modules are pure functions over snapshot types, guarded by the existing purity
test.

```text
src/stakeroute/
├── core/                        # pure mechanism — unchanged, plus:
│   ├── estimates.py             # NEW  impact/urgency/review-cost from cited observations (D-016)
│   ├── decay.py                 # NEW  reputation decay over real elapsed ms (D-021)
│   └── duplicates.py            # NEW  deterministic near-duplicate detection (D-023)
├── model/                       # NEW  the model boundary — never imported by core/
│   ├── protocol.py              # ModelClient Protocol
│   ├── gemini.py                # Vertex AI adapter (D-011)
│   ├── null.py                  # no-model operation (FR-126)
│   ├── validation.py            # response shape/range validation (FR-122)
│   ├── recorder.py              # durable interaction log (FR-123)
│   └── budget.py                # per-interval usage ceiling (D-020)
├── real/                        # NEW  real evidence and real mode
│   ├── collectors/
│   │   ├── host_metrics.py      # psutil: cpu, memory, disk, process table
│   │   ├── app_logs.py          # the system's own log output
│   │   ├── vcs_tests.py         # git history and test-run results
│   │   └── container_events.py  # docker events where present, else absent source
│   ├── redaction.py             # allow-list + redaction at the ingestion boundary (D-014)
│   ├── scopes.py                # declared evidence access scopes (FR-114)
│   ├── reasoners.py             # agents that reason over a bundle (D-013)
│   ├── proposal.py              # hypothesis proposal from observations (FR-107)
│   ├── conditions.py            # checkable-condition registry (D-017)
│   ├── resolution.py            # automatic outcome determination (FR-148)
│   └── run_ingestor.py          # process entrypoint — takes the simulator's slot in real mode
├── replay/
│   └── replay.py                # record-driven replay and divergence report (D-012)
├── simulator/                   # unchanged — SC-108 forbids touching its behaviour
├── worker/                      # ranking/settlement — unchanged decision path
├── storage/                     # schema additions only
└── dashboard/                   # mode banner, source liveness, model status, trace view

tests/
├── unit/                        # estimates, decay, duplicates, validation, redaction, scopes
└── integration/                 # model-absent, replay-equality, evidence-scope, redaction-egress,
                                 # automatic-resolution, tenant-separation, feature-001-unchanged
```

**Structure Decision**: The existing single project is kept. Three application processes are
retained (D-024) — in real mode `real/run_ingestor.py` occupies the slot
`simulator/run_simulator.py` holds in simulated mode, with the reasoning agents running inside it
as coroutines rather than as separate services. The Complexity Tracking table is therefore empty
of process-count entries.

The one structural rule worth stating: `stakeroute.core` must never import `stakeroute.model`.
`core/estimates.py` exists specifically so that the numbers feeding `priority_score` are computed
inside the module the purity test already guards.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified.

Empty. The two Additional Constraints failures recorded above were resolved by amending the
constitution to v1.1.0 before implementation, not by waiving them. Nothing in this plan proceeds
against the constitution as written.
