---

description: "Task list for Real System Mode — real evidence, reasoning agents, and a model boundary that survives its own dependency"
---

# Tasks: Real System Mode

**Input**: Design documents from `/specs/002-real-system-mode/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts), [quickstart.md](./quickstart.md)

**Constitution**: v1.1.0. The gate passes; see [plan.md](./plan.md#constitution-check). Two obligations
from the v1.1.0 amendment are load-bearing here and are tasks, not intentions: the decision path must
survive the model's absence (Phase 5), and the *replayable* guarantee must be met and named as such
(Phase 8).

**Tests**: Required, not optional. Principle III and the Development Workflow section mandate unit
tests for mechanism code before it is wired into the pipeline, plus dedicated idempotency tests. Every
new pure module here (`estimates`, `decay`, `duplicates`) is mechanism code, and every new economic
path (resolutions, corrections, post-expiry arrivals) needs its own idempotency test.

## Phase ordering note — read before starting

Three deliberate departures from a straight P1 → P2 walk:

1. **US2 runs before US1, though both are P1.** Agents cannot reason over evidence that does not
   exist yet, and hypotheses cannot be forecast before they are proposed. Principle VI's build order
   (mechanism → real evidence → reasoning → model-absence → measurement → replay → UI) supersedes
   spec priority where the two disagree, and plan.md fixes exactly this sequence.
2. **All browser UI is deferred to Phase 9.** Every story phase delivers an API-and-test testable
   increment; the UI presents facts already proven. This is the same discipline feature 001 used.
3. **SC-108 is a gate, not a hope.** T017 lands in the foundational phase and every checkpoint below
   re-runs it. If the seeded simulation stops reproducing its recorded results, work stops.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1–US6, mapping to spec.md user stories

---

## Phase 1: Setup

**Purpose**: Dependencies, package skeleton, configuration, and the credential guard. No mechanism logic.

- [X] T001 Add runtime dependencies with `uv add google-genai psutil`, updating `pyproject.toml` and `uv.lock` (D-011, D-015)
- [X] T002 [P] Create package tree `src/stakeroute/model/__init__.py`, `src/stakeroute/real/__init__.py`, `src/stakeroute/real/collectors/__init__.py`, `src/stakeroute/replay/__init__.py`
- [X] T003 [P] Add real-mode constants to `src/stakeroute/config.py`: `REAL_TENANT_ID="hostops"`, `MODEL_TIMEOUT_S=10.0`, `MODEL_CEILING_CALLS_PER_HOUR`, `MIN_RESOLVED_FOR_CALIBRATION=10`, `COLLECTOR_POLL_INTERVAL_S=2.0`, `SOURCE_SILENCE_THRESHOLD_MS`, `PROPOSAL_INTERVAL_S`, `OBSERVATIONS_PER_INTERVAL_LIMIT`, `DUPLICATE_JACCARD_THRESHOLD`, `REPUTATION_HALF_LIFE_MS`
- [X] T004 [P] Add mode and model selection to `src/stakeroute/config.py`: `STAKEROUTE_MODE` (`real|sim|replay`, default `sim`) and `STAKEROUTE_MODEL` (`gemini|none|recorded`, default `none`); read the credential file **path** from `GOOGLE_APPLICATION_CREDENTIALS` and never its contents (FR-127)
- [X] T005 [P] Add `tests/unit/test_no_credentials_committed.py` asserting no git-tracked file contains a credential-shaped string and that `vertex-ai-credentials.json` is ignored (SC-112, first half)
- [X] T006 [P] Extend `tests/conftest.py` with `real_repo` (a `hostops`-seeded repository), `null_model` (a `NullModelClient`), and `frozen_clock` fixtures

**Checkpoint**: `uv sync` succeeds; `uv run pytest` collects and passes with no behaviour change.

---

## Phase 2: Foundational — Schema, Pure Mechanism, Model Boundary (BLOCKING)

**Purpose**: Everything both P1 stories rest on. Principle VI: no story work begins until this is green.

**⚠️ CRITICAL**: Phase 2 must complete before ANY user story phase starts.

### Schema and storage

- [X] T007 Add `mode TEXT NOT NULL DEFAULT 'sim'` to `events`, `hypotheses`, `forecasts`, `attention_decisions`, `outcomes` and `settlements` in `src/stakeroute/storage/repository.py` — the default is what keeps every feature-001 row and query path unchanged (D-019, SC-108)
- [X] T008 [P] Create `observation_sources` table in `src/stakeroute/storage/repository.py` per data-model.md, with `state IN ('live','quiet','silent','absent')` and a `state='absent' ⇒ absent_reason NOT NULL` validation
- [X] T009 [P] Create `model_interactions` table and `idx_model_interactions_tenant_time` in `src/stakeroute/storage/repository.py`, with `accepted=0 ⇒ rejection_reason NOT NULL` and `accepted=1 ⇒ response NOT NULL`
- [X] T010 [P] Create `proposals` table in `src/stakeroute/storage/repository.py` with `interaction_id NOT NULL`, `status IN ('pending','promoted','rejected','merged')`
- [X] T011 [P] Create `attribute_estimates` table in `src/stakeroute/storage/repository.py` with `basis NOT NULL`, `estimator NOT NULL`, `UNIQUE(hypothesis_id, attribute, created_at_ms)` and a `superseded_by` self-reference
- [X] T012 [P] Create `resolutions` table in `src/stakeroute/storage/repository.py` with `UNIQUE(dedup_key)` and `UNIQUE(hypothesis_id, resolution_seq)` — the same idempotency pattern as `events.event_id` (Principle III)
- [X] T013 [P] Create `replay_runs` table in `src/stakeroute/storage/repository.py` with `model_requests_made` recorded rather than asserted
- [X] T014 Add `hypotheses.proposal_id`, `hypotheses.condition_name`, `hypotheses.condition_params`, `forecasts.evidence_bundle`, `forecasts.rationale`, `forecasts.interaction_id` in `src/stakeroute/storage/repository.py` (depends on T008–T013)
- [X] T015 Add tenant-scoped accessors for the six new tables to `Repository` in `src/stakeroute/storage/repository.py`, using `ON CONFLICT DO NOTHING` on every path that has a uniqueness constraint
- [X] T016 Seed the `hostops` tenant row alongside `acmepay` at schema init in `src/stakeroute/storage/repository.py` (D-018)
- [X] T017 Create `tests/integration/test_feature_001_unchanged.py` asserting the full feature-001 suite passes after the migration and the seeded scenario reproduces the results recorded in `specs/001-stakeroute-attention-market/run-log.md` (SC-108, FR-132)

### Pure mechanism additions (tests first — write these before T021–T024 and watch them fail)

- [X] T018 [P] Write `tests/unit/test_estimates.py` covering impact from observation count and severity, urgency from recency, review cost from scope, and the requirement that every returned estimate carries a non-empty `basis` and an `estimator` name (FR-108, D-016)
- [X] T019 [P] Write `tests/unit/test_decay.py` covering `decay_reputation(current, elapsed_ms, half_life_ms)` — half-life behaviour, zero elapsed is identity, the floor is approached but never becomes absorbing (FR-137, D-021)
- [X] T020 [P] Write `tests/unit/test_duplicates.py` covering exact-condition-match merge, Jaccard-threshold flag inside a time window, and no false positive across unrelated conditions (FR-110, D-023)
- [X] T021 Add `ObservationSnapshot` and `AttributeEstimate` frozen dataclasses to `src/stakeroute/core/types.py` — `AttributeEstimate.basis` is a required field, so an estimate without a derivation is unrepresentable (Principle II)
- [X] T022 Implement `src/stakeroute/core/estimates.py` — pure functions from cited `ObservationSnapshot`s to impact, urgency and review cost, each returning an `AttributeEstimate`. **This is the module that makes FR-108 and FR-119 both hold** (D-016)
- [X] T023 [P] Implement `src/stakeroute/core/decay.py` — additive only; `src/stakeroute/core/reputation.py::update_reputation` MUST NOT be modified (D-021, SC-108)
- [X] T024 [P] Implement `src/stakeroute/core/duplicates.py` — a rule over bound conditions and cited observation sets, derivable on a whiteboard (Principle II, D-023)
- [X] T025 Extend `FORBIDDEN_MODULES` in `tests/unit/test_core_purity.py` with `stakeroute.model`, `stakeroute.real`, `stakeroute.replay`, `subprocess`, `psutil` and `google` — this is the enforcement behind FR-119, not a style rule

### The model boundary (tests first — write T026–T027 before T028–T033)

- [X] T026 [P] Write `tests/unit/test_model_validation.py` covering every rejection reason in contracts/model-boundary.md: `MALFORMED_SHAPE`, `NO_CITATIONS`, `UNKNOWN_CITATION`, `CITATION_OUT_OF_WINDOW`, `UNKNOWN_CONDITION`, `INVALID_CONDITION_PARAMS`, `PROBABILITY_OUT_OF_RANGE`, `STAKE_OUT_OF_RANGE`, `INSUFFICIENT_CREDITS`, `EVIDENCE_SCOPE_VIOLATION`, `REFUSAL` (FR-122)
- [X] T027 [P] Write `tests/unit/test_model_budget.py` covering ceiling exhaustion producing `ceiling_reached` with the specific capability named, and consumption reported against the ceiling (FR-125, D-020)
- [X] T028 Define `ModelClient` Protocol, `ModelResult = Accepted | Rejected`, `ModelState` and `RejectionReason` in `src/stakeroute/model/protocol.py` per contracts/model-boundary.md — `Rejected` must be a type a caller cannot silently ignore
- [X] T029 Implement `src/stakeroute/model/validation.py` — proposal and forecast schema validation. The proposal schema has **no** `impact`, `urgency`, `review_cost` or `probability` field; the schema is the enforcement (D-016)
- [X] T030 [P] Implement `NullModelClient` in `src/stakeroute/model/null.py` — always `Rejected(MODEL_DISABLED)`, `state() == 'unconfigured'` (FR-126, SC-113)
- [X] T031 [P] Implement `src/stakeroute/model/budget.py` — per-interval call ceiling, degrading capability and never the decision path (D-020)
- [X] T032 Implement `ModelInteractionRecorder` in `src/stakeroute/model/recorder.py` — writes exactly one `model_interactions` row **before** any result is used, including timeouts and transport failures (FR-123, FR-128)
- [X] T033 Implement `GeminiClient` in `src/stakeroute/model/gemini.py` — Vertex AI via `google-genai`, per-request timeout, redacted prompt as sent. Nothing outside this module imports the SDK (D-011); no token ever enters a prompt or a log line (FR-127)

### Redaction — one boundary, audited once

- [X] T034 [P] Write `tests/unit/test_redaction.py` covering the per-source allow-list and all five rewrite rules in contracts/observations.md — home-directory paths, other absolute paths, account names, `KEY=value` environment shapes, and credential-shaped strings (FR-146, SC-115)
- [X] T035 Implement `src/stakeroute/real/redaction.py` — allow-list then rewrite, applied at the ingestion boundary before any durable write, recording which rules fired (D-014)

**Checkpoint**: `uv run pytest tests/unit -q` green; `test_core_purity` green with the extended forbidden
list; T017 green. The mechanism is correct before anything real is attached to it.

---

## Phase 3: User Story 2 — The system runs on real observations (Priority: P1) 🎯 MVP

**Goal**: Evidence enters continuously from the host and repository on a live clock, and candidate
hypotheses are proposed from that stream rather than declared in a scenario file.

**Independent Test**: Leave the system running against the host with no scenario invoked, induce a real
fault, and confirm a hypothesis reaches the queue whose statement, impact and urgency appear nowhere in
the codebase or configuration and trace to the specific observed records that produced them (quickstart V2).

### Tests for User Story 2 (REQUIRED — mechanism and economics, Principle III) ⚠️

- [X] T036 [P] [US2] Write `tests/integration/test_real_ingestion.py` — real provenance and timestamps recorded, stable `source_event_id`, out-of-order and past-dated arrivals producing no duplicate economic effect (FR-102, FR-104)
- [X] T037 [P] [US2] Write `tests/unit/test_source_liveness.py` — `live → quiet → silent` transitions, any observation returning a source to `live`, and `absent` as terminal with a required reason (FR-141)
- [X] T038 [P] [US2] Write `tests/integration/test_proposal_pipeline.py` — a proposal cites real `events` rows, an unknown or out-of-window citation rejects the whole proposal, and nothing enters the ranked queue before it is durably recorded (FR-107, FR-111, FR-122)
- [X] T039 [P] [US2] Write `tests/integration/test_tenant_separation.py` — zero real-mode rows carry `acmepay`, zero simulated rows carry `hostops`, and any endpoint naming more than one tenant is refused (SC-117, FR-147)
- [X] T040 [P] [US2] Write `tests/integration/test_redaction_egress.py` — the five-category scan over `events.payload` **and** `model_interactions.request` across 100% of rows (SC-115, FR-146)

### Implementation for User Story 2

- [X] T041 [US2] Implement the collector protocol, the observation envelope builder reusing `compute_event_id`, and per-poll liveness updates in `src/stakeroute/real/collectors/__init__.py` — no new idempotency mechanism is introduced (Principle III)
- [X] T042 [P] [US2] Implement `src/stakeroute/real/collectors/host_metrics.py` — psutil CPU, memory, per-mount disk and process-table deltas, 2s poll
- [X] T043 [P] [US2] Implement `src/stakeroute/real/collectors/app_logs.py` — the system's own log output, `absent` when the log path is unwritable
- [X] T044 [P] [US2] Implement `src/stakeroute/real/collectors/vcs_tests.py` — `git log`, `git status` and the most recent test-run result, 30s poll, `absent` when git is unavailable
- [X] T045 [P] [US2] Implement `src/stakeroute/real/collectors/container_events.py` — `docker events --since` streaming, `absent` with `absent_reason='docker daemon unreachable'` where no runtime is present (this is the common developer-laptop case and is a validation scenario, not a failure)
- [X] T046 [US2] Implement the source liveness state machine and startup absence detection, persisted to `observation_sources` in `src/stakeroute/real/collectors/__init__.py` — a silent source and a quiet one must never render the same (FR-141)
- [X] T047 [US2] Implement the volume policy in `src/stakeroute/real/collectors/__init__.py` — retain the highest-severity observation per `(source, subject)` per interval, still write everything to `events`, and record `set_aside_count` on `observation_sources` (FR-105, Principle IV)
- [X] T048 [US2] Implement `src/stakeroute/real/proposal.py` — build the redacted prompt from an observation window, call `ModelClient`, validate the response, persist a `proposals` row against its `interaction_id` (FR-107)
- [X] T049 [US2] Validate citations against `events` for the tenant and window in `src/stakeroute/real/proposal.py`, rejecting the whole proposal on any failure (FR-122)
- [X] T050 [US2] Wire `core/estimates.py` into `src/stakeroute/real/proposal.py` — compute impact, urgency and review cost from the cited observations and write `attribute_estimates` rows with `basis` and `estimator` (FR-108, D-016)
- [X] T051 [US2] Wire `core/duplicates.py` into `src/stakeroute/real/proposal.py` — merge on exact condition match, flag otherwise, so one real situation never consumes two of two review slots (FR-110)
- [X] T052 [US2] Promote a validated proposal to a `hypotheses` row in `src/stakeroute/real/proposal.py` with `mode='real'`, `proposal_id`, `condition_name` and `condition_params`, only after durable recording (FR-111)
- [X] T053 [US2] Implement `src/stakeroute/real/run_ingestor.py` — collectors and the proposal loop as coroutines in the process slot `simulator/run_simulator.py` occupies, publishing over the transport rather than opening its own write connection (D-024)
- [X] T054 [US2] Add `GET /api/mode` with the `mode`, `tenant` and `sources` blocks to `src/stakeroute/dashboard/main.py` per contracts/http-api.md (FR-139, FR-141)
- [X] T055 [US2] Extend `GET /api/queue` in `src/stakeroute/dashboard/main.py` with `mode`, per-entry `estimates` carrying `basis` and `confirmed`, `cited_observation_count`, `condition`, and `flagged_duplicates` (FR-108, FR-109, FR-110)
- [X] T056 [US2] Add `POST /api/estimates/{hypothesis_id}/confirm` to `src/stakeroute/dashboard/main.py` — inserts a new `attribute_estimates` row and sets `superseded_by` on the prior one; estimates are never updated in place (FR-109)
- [X] T057 [US2] Enforce the explicit single-`tenant` query parameter on every data endpoint in `src/stakeroute/dashboard/main.py`, refusing any request naming more than one — cross-tenant aggregation must require new code, not a missing filter (D-018, SC-117)

**Checkpoint**: Real observations reach the queue with traceable citations and derived, basis-carrying
estimates. Quickstart V2 and V4 pass. T017 still green.

---

## Phase 4: User Story 1 — Agents earn their forecasts (Priority: P1)

**Goal**: An agent sees a declared slice of evidence and nothing else, never the outcome, and can be
wrong in ways nobody configured.

**Independent Test**: Run hypotheses through the real agent population with outcomes withheld; verify no
access path to the outcome exists, that forecasts are not a function of any configured accuracy value, and
that measured calibration differs across the population and appears in no configuration (quickstart V3).

### Tests for User Story 1 (REQUIRED — this story is the feature's central claim) ⚠️

- [X] T058 [P] [US1] Write `tests/unit/test_evidence_bundle.py` — walk the `EvidenceBundle` dataclass fields and assert none could carry an outcome, ground-truth label or accuracy parameter, and that it is `frozen=True` (FR-113, SC-102)
- [X] T059 [P] [US1] Write `tests/integration/test_evidence_scope.py` — a bundle contains only in-scope sources, a rationale citing out-of-scope evidence is rejected into `rejected_forecasts`, and 100% of recorded real-mode bundles are outcome-free (FR-114, FR-116, SC-102)
- [X] T060 [P] [US1] Write `tests/integration/test_agents_reason.py` — probabilities differ across agents holding disjoint evidence, rationales are recorded, and **no configured accuracy constant exists anywhere under `src/stakeroute/real/`** (SC-101, FR-112, FR-117)
- [X] T061 [P] [US1] Write `tests/unit/test_forecast_validation.py` — out-of-range probability, stake outside `STAKE_MIN..STAKE_MAX`, and stake exceeding available credits are each rejected and recorded (FR-116)

### Implementation for User Story 1

- [X] T062 [US1] Define `EvidenceAccessScope`, `EvidenceBundle` and `ForecastProposal` frozen dataclasses in `src/stakeroute/real/scopes.py` per data-model.md — the exclusion in FR-113 is enforced by the type, not by a convention
- [X] T063 [US1] Implement the sole `EvidenceBundle` constructor in `src/stakeroute/real/scopes.py`, querying only sources inside the agent's declared scope. It is the single construction path, which is what makes the claim checkable from one function signature (D-013)
- [X] T064 [P] [US1] Declare the four source-line evidence scopes in `src/stakeroute/config.py` — host metrics, application logs, VCS and tests, container events
- [X] T065 [US1] Implement `src/stakeroute/real/reasoners.py` — `async def forecast(bundle: EvidenceBundle, model: ModelClient) -> ForecastProposal | Rejection`, holding no `Repository` handle, no tenant id and no outcome (D-013)
- [X] T066 [US1] Persist `evidence_bundle`, `rationale` and `interaction_id` on `forecasts` rows in `src/stakeroute/storage/repository.py` — storing the bundle is the difference between an enforced exclusion and a claimed one (FR-113, FR-115)
- [X] T067 [US1] Route `EVIDENCE_SCOPE_VIOLATION`, `PROBABILITY_OUT_OF_RANGE`, `STAKE_OUT_OF_RANGE` and `INSUFFICIENT_CREDITS` into the existing `rejected_forecasts` table as well as `model_interactions`, in `src/stakeroute/real/reasoners.py` and `src/stakeroute/storage/repository.py` (FR-116)
- [X] T068 [US1] Enforce stake rationing against `available_credits` in `src/stakeroute/real/reasoners.py` so a confident agent cannot escalate further until its stakes release (US1 acceptance scenario 5)
- [X] T069 [US1] Derive `measured_calibration` from `settlements` on read in `src/stakeroute/metrics.py`, returning `None` together with the resolved count below `MIN_RESOLVED_FOR_CALIBRATION` (FR-117, FR-118, D-022)
- [X] T070 [US1] Extend `GET /api/agents` in `src/stakeroute/dashboard/main.py` with `evidence_scope`, `measured_calibration`, `resolved_forecast_count` and `insufficient`. **Add no `accuracy` field** — its absence is the requirement being met (FR-117)
- [X] T071 [US1] Add `GET /api/hypotheses/{id}/trace` to `src/stakeroute/dashboard/main.py` returning the proposal, the cited observations with `redactions_applied`, and the forecasts with rationale and scope (FR-140)
- [X] T072 [US1] Wire the agent population into `src/stakeroute/real/run_ingestor.py` as coroutines sharing the collectors' observation stream (D-024)

**Checkpoint**: Agents reason over disjoint evidence and can be wrong. Quickstart V3 passes. T017 still green.

---

## Phase 5: User Story 3 — The market keeps running when the model does not (Priority: P1)

**Goal**: Ranking and settlement are unaffected by the model's slowness, absence or nonsense. Only
proposal and prose stop, and the operator can see plainly that this is what stopped.

**Independent Test**: With hypotheses open and forecasts in flight, make the model endpoint unreachable;
ranking passes complete with identical results, settlement continues, nothing hangs, and the surface names
the lost capability (quickstart V1 and V5).

### Tests for User Story 3 (REQUIRED — Principle I is non-negotiable and untested until now) ⚠️

- [X] T073 [P] [US3] Write `tests/integration/test_model_absent.py` — a full ranking pass with a client that raises on any call, settlement with the model unreachable, and rankings identical with and without the model (FR-120, SC-104)
- [X] T074 [P] [US3] Write `tests/integration/test_slow_model.py` — pass duration unaffected by a client sleeping past the timeout. This asserts the *architecture*, not the tuning: the worker holds no client to wait on (FR-121, SC-105)
- [X] T075 [P] [US3] Write `tests/integration/test_malformed_responses.py` — each rejection reason produces a `model_interactions` row with `accepted = 0` and **zero** downstream forecasts, hypotheses or economic effects (FR-122, SC-106)
- [X] T076 [P] [US3] Write `tests/integration/test_starts_with_no_model.py` — with `STAKEROUTE_MODEL=none`, ingestion, ranking, settlement and the queue all work and `/api/mode` reports `unconfigured` with the unavailable capabilities named (FR-126, SC-113)
- [X] T077 [US3] Write `tests/unit/test_decision_path_model_free.py` — AST-walk `src/stakeroute/worker/` and assert no module imports `stakeroute.model`, so `run_ranking_pass` and `settle_hypothesis` cannot hold a client reference (FR-119, FR-128)

### Implementation for User Story 3

- [X] T078 [US3] Enforce `MODEL_TIMEOUT_S` per request in `src/stakeroute/model/gemini.py`, abandoning within the limit and recording a `TIMEOUT` or `TRANSPORT_FAILURE` row with `response = NULL` (FR-121, FR-123)
- [X] T079 [US3] Implement the `ModelState` transitions (`ok | degraded | ceiling_reached | disabled | unconfigured`) with `unavailable_capabilities` in `src/stakeroute/model/protocol.py` and `src/stakeroute/model/budget.py` — the list contains only `hypothesis_proposal` and `prose_explanation`, never ranking or settlement (FR-124)
- [X] T080 [US3] Add the `model` block to `GET /api/mode` in `src/stakeroute/dashboard/main.py`, naming what is consequently unavailable rather than leaving it to be inferred from an absence, and reporting usage against the ceiling (FR-124, FR-125)
- [X] T081 [US3] Add `GET /api/model/interactions` and `GET /api/model/interactions/{id}` to `src/stakeroute/dashboard/main.py` with accepted/rejected totals by reason — the boundary must be inspectable, not merely recorded (FR-123)
- [X] T082 [US3] Wire `model/budget.py` into `src/stakeroute/real/proposal.py` and `src/stakeroute/real/reasoners.py` so ceiling exhaustion degrades capability and reports consumption instead of failing opaquely (FR-125, D-020)

**Checkpoint**: The system runs with no model at all, and runs identically with a broken one. Quickstart
V1 and V5 pass. This is the constitutional obligation the v1.1.0 amendment was granted against.

---

## Phase 6: User Story 4 — Outcomes arrive from outside the system (Priority: P2)

**Goal**: A hypothesis resolves because a condition of the machine was re-checked, not because the program
looked up an answer it had stored.

**Independent Test**: Resolve a real hypothesis by an external act; the settlement record names the source
and arrival time, settles exactly the forecasts open at that moment, and the outcome value exists nowhere
in the system before it (quickstart V9, step 5).

### Tests for User Story 4 (REQUIRED — settlement is economic; Principle III) ⚠️

- [ ] T083 [P] [US4] Write `tests/unit/test_conditions.py` — each registry entry is a pure predicate over a freshly sampled host state, never over recorded observations (FR-148, D-017)
- [ ] T084 [P] [US4] Write `tests/integration/test_automatic_resolution.py` — `check_name`, `check_params`, `check_result` verbatim and `checked_at_ms` are all recorded, so the outcome is auditable independently of any operator judgement (FR-148, FR-133)
- [ ] T085 [P] [US4] Write `tests/integration/test_resolution_idempotency.py` — a redelivered outcome inserts zero rows, produces exactly one settlement effect, and leaves balances unchanged (FR-135, Principle III)
- [ ] T086 [P] [US4] Write `tests/integration/test_resolution_corrections.py` — a correction is a new `resolution_seq` and never an overwrite (FR-136), and an outcome arriving after expiry is recorded with `settled = 0` and a reason, without re-settling returned stakes (FR-138)

### Implementation for User Story 4

- [ ] T087 [US4] Implement the closed registry in `src/stakeroute/real/conditions.py` — `process_absent`, `disk_free_below`, `cpu_saturated`, `memory_pressure`, `test_failing`, `container_exited`, `log_error_rate_above` per contracts/observations.md. The model selects; it never invents a check (D-017)
- [ ] T088 [US4] Validate `condition_name` against the registry and `condition_params` against that entry's signature in `src/stakeroute/model/validation.py`, rejecting with `UNKNOWN_CONDITION` or `INVALID_CONDITION_PARAMS` (FR-122)
- [ ] T089 [US4] Implement `src/stakeroute/real/resolution.py` — re-run the bound check at the hypothesis deadline, write a `resolutions` row, and write through to `outcomes` on `resolution_seq = 1` so the feature-001 settlement path is untouched (FR-133, FR-148, SC-108)
- [ ] T090 [US4] Settle exactly the forecasts open at `arrived_at_ms` in `src/stakeroute/worker/settlement_runner.py` (FR-134)
- [ ] T091 [US4] Record a post-expiry arrival with `settled = 0` and `not_settled_reason`, surfaced rather than swallowed, in `src/stakeroute/real/resolution.py` (FR-138)
- [ ] T092 [US4] Add the `resolution` block to `GET /api/hypotheses/{id}/trace` and the operator-confirmation path for a hypothesis that bound no condition, recording `determination='operator'` (FR-133, D-017)

**Checkpoint**: Outcomes are observations rather than opinions, and corrections are auditable. T017 still green.

---

## Phase 7: User Story 5 — Reputation accumulates across real time (Priority: P2)

**Goal**: Standing is the accumulated result of everything an agent has forecast, decaying against elapsed
milliseconds rather than a loop counter.

**Independent Test**: Operate across multiple epochs, then verify standings are a function of recorded
settlement history alone, are reproducible from that history, and that at least one agent's ordering
relative to another changed (SC-111).

### Tests for User Story 5 (REQUIRED — credit conservation is economic) ⚠️

- [ ] T093 [P] [US5] Write `tests/integration/test_reputation_over_time.py` — ordering changes at least once from measured performance, recomputing from settlements reproduces the value, and an agent at the floor recovers (SC-111, US5 acceptance scenarios 1–3)
- [ ] T094 [P] [US5] Write `tests/integration/test_budget_rollover.py` — an epoch rollover on a real clock replenishes budgets and returns released stakes while creating and destroying zero credits (FR-137)
- [ ] T095 [P] [US5] Write `tests/integration/test_calibration_insufficiency.py` — below `MIN_RESOLVED_FOR_CALIBRATION`, every calibration and ranking-quality figure is `null` with a count, never a zero and never a placeholder, in 100% of such cases (SC-114, FR-118)

### Implementation for User Story 5

- [ ] T096 [US5] Apply `core/decay.py::decay_reputation` at epoch rollover in `src/stakeroute/worker/main.py`, **real mode only**, passing `elapsed_ms` as an argument so the core still reads no clock (D-021, FR-137)
- [ ] T097 [US5] Implement real-clock epoch rollover and stake release in `src/stakeroute/worker/main.py`, conserving credits across the rollover itself (FR-137)
- [ ] T098 [US5] Scope every function in `src/stakeroute/metrics.py` by `mode` and add `insufficient` and `insufficient_reason` to the existing `(value, measured_over)` contract rather than inventing a second one (FR-142, D-022)
- [ ] T099 [US5] Extend `GET /api/metrics` in `src/stakeroute/dashboard/main.py` with `measured_over_outcomes`, `minimum_for_calibration`, `insufficient`, `insufficient_reason` and a required `provenance` of `measured-real | measured-simulated` — a figure that cannot say which world it came from is not displayable (FR-142, FR-144, SC-109)
- [ ] T100 [US5] Make `src/stakeroute/analysis/cost_of_attack.py` state whether the reputation distribution its frontier is computed against was earned or configured (US5 acceptance scenario 5, FR-144)
- [ ] T101 [US5] Record reputation history so an agent's standing is displayable alongside the settlement records that produced it, in `src/stakeroute/storage/repository.py` (US5 acceptance scenario 1, SC-109)

**Checkpoint**: The cost-of-attack frontier is priced against reputation that was earned. Quickstart V8 passes.

---

## Phase 8: User Story 6 — Every real run replays exactly (Priority: P2)

**Goal**: The *replayable* guarantee, met and named as such. A run involving a component that never repeats
itself still reproduces byte-identically from its recorded inputs.

**Independent Test**: Capture a live run, replay it from recorded inputs with no model reachable, and compare
rankings, settlements and final balances byte for byte — then alter one recorded input and confirm the replay
diverges rather than absorbing the change (quickstart V7).

### Tests for User Story 6 (REQUIRED — this is a constitutional obligation under v1.1.0) ⚠️

- [ ] T102 [P] [US6] Write `tests/integration/test_replay_identical.py` — rankings, settlements and final agent balances byte-identical to the original (FR-129, SC-107)
- [ ] T103 [P] [US6] Write `tests/integration/test_replay_divergence.py` — altering one recorded input produces `identical: false` with `first_divergence` naming the record, field, expected and actual. **This negative case is what makes the positive case mean anything** (FR-131)
- [ ] T104 [P] [US6] Write `tests/integration/test_replay_no_requests.py` — `model_requests_made == 0`, asserted against the counted value rather than an assumption (FR-130, SC-107)

### Implementation for User Story 6

- [ ] T105 [US6] Implement `RecordedModelClient` in `src/stakeroute/model/recorder.py` — serves `model_interactions` rows by request hash and **raises** on a miss, counting requests. There is no code path from replay to a socket (FR-130, D-012)
- [ ] T106 [US6] Implement `src/stakeroute/replay/replay.py` — read recorded model responses, observations and clock reads from the source database, drive the identical pipeline into a **scratch** database with `mode='replay'`, and compare (D-012)
- [ ] T107 [US6] Write the comparison result to `replay_runs` in the **source** database only, from `src/stakeroute/replay/replay.py`, never touching any other source row, so a failed replay leaves evidence of its own failure rather than overwriting what it disagreed with (FR-131)
- [ ] T108 [US6] Add `POST /api/replay` to `src/stakeroute/dashboard/main.py` returning `identical`, `records_compared`, `model_requests_made` and `first_divergence` (FR-129 – FR-131)

**Checkpoint**: The reproducibility claim holds in its amended form. Quickstart V7 passes, including the
negative case.

---

## Phase 9: Operator Surface

**Purpose**: Present facts phases 3–8 already proved. Nothing here computes anything new.

- [ ] T109 [P] Add the mode banner (`real | sim | replay`) to `src/stakeroute/dashboard/static/index.html` — visible at all times and without ambiguity (FR-139, SC-110)
- [ ] T110 [P] Add the source liveness strip to `src/stakeroute/dashboard/static/index.html`, rendering `absent` with its reason and `silent` distinctly from `quiet` (FR-141, SC-110)
- [ ] T111 [P] Add the model status panel to `src/stakeroute/dashboard/static/index.html` — state, detail, unavailable capabilities, and usage against the ceiling (FR-124, FR-125)
- [ ] T112 [P] Render queue entries in `src/stakeroute/dashboard/static/index.html` with each estimate's `basis`, estimated values visibly distinguished from operator-confirmed ones, the confirm control, duplicate flags, and the withheld count (FR-109, FR-110, Principle IV)
- [ ] T113 [P] Add the trace view to `src/stakeroute/dashboard/static/index.html` — hypothesis to observations to rationales to resolution, every element a recorded row (FR-140)
- [ ] T114 [P] Render the metrics strip in `src/stakeroute/dashboard/static/index.html` with `provenance` and insufficiency as "not yet measurable (n=3)" rather than as a zero (FR-142, FR-144, SC-114)
- [ ] T115 [P] Render the agents panel in `src/stakeroute/dashboard/static/index.html` with `measured_calibration` and `resolved_forecast_count` and no accuracy figure anywhere (FR-117)

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: The honesty requirements are deliverables here, not documentation housekeeping — Principle V,
and under v1.1.0 two of them are constitutional.

- [ ] T116 [P] State in `README.md` that agents sharing one underlying model are not independent reasoners, that the evidence-group discount keys on evidence rather than on reasoner, and that this correlation is therefore undiscounted (FR-143, Constitution Principle V)
- [ ] T117 [P] State in `README.md` which figures derive from measured real-world performance and which remain simulated, and narrow the existing reproducibility claim to *regenerable for the seeded simulation, replayable for real runs* (FR-144, D-012, Constitution v1.1.0)
- [ ] T118 [P] State in `README.md` that "real" means evidence and reasoning not fabricated by the evaluator — not production scale, hardening or operational maturity — and that the demonstration domain is the host and repository while AcmePay remains simulated (FR-145, FR-150)
- [ ] T119 [P] State in `README.md` that the system observes a machine it is itself loading, and that a sufficiently severe host fault degrades the observer and the ledger together (FR-149)
- [ ] T120 [P] Document the checkable-condition registry's coverage as a stated limit in `README.md` and `src/stakeroute/real/conditions.py` — a situation the registry cannot express falls back to operator judgement (D-017)
- [ ] T121 Add a real-mode profile to `docker-compose.yml` in which `real/run_ingestor.py` takes the simulator's process slot, keeping the application at three processes (D-024)
- [ ] T122 Run `uv run ruff format . && uv run ruff check . && uv run pyright` and fix in that order — formatting, then types, then lint
- [ ] T123 Run every scenario in [quickstart.md](./quickstart.md) V0 through V9, including the V9 unscripted end-to-end demonstration (SC-116)
- [ ] T124 Adversarial review across `src/stakeroute/real/`, `src/stakeroute/model/` and `src/stakeroute/replay/`: evidence-scope escape, unredacted egress, a replay divergence absorbed silently, and duplicate settlement on a redelivered outcome

---

## Dependencies

```
Phase 1 Setup
    ↓
Phase 2 Foundational (BLOCKING — schema, pure mechanism, model boundary, redaction)
    ↓
Phase 3 US2 (P1) real observations ──→ MVP boundary
    ↓
Phase 4 US1 (P1) reasoning agents      [needs US2's hypotheses to forecast on]
    ↓
Phase 5 US3 (P1) model absence         [needs agents and proposals in flight to remove]
    ↓
Phase 6 US4 (P2) external outcomes     [needs forecasts to settle]
    ↓
Phase 7 US5 (P2) reputation over time  [needs settled outcomes to accumulate]
    ↓
Phase 8 US6 (P2) replay                [needs a recorded run of everything above]
    ↓
Phase 9 Operator surface               [presents what phases 3–8 proved]
    ↓
Phase 10 Polish
```

**Story independence**: US2 stands alone once Phase 2 is green. US1 requires US2's hypotheses. US3 requires
something to remove the model *from*, so it follows US1 — but note its central guarantee is structural and
already true at T077, which can be written the moment Phase 2 lands. US4 requires forecasts. US5 requires
settlements. US6 requires a recorded run of all of it.

**The one cross-phase dependency worth watching**: `GET /api/mode` is built in two halves — sources at T054
(US2) and the model block at T080 (US3) — and `GET /api/hypotheses/{id}/trace` in three — observations and
proposal at T071 (US1), resolution at T092 (US4). Both touch `src/stakeroute/dashboard/main.py`, so those
tasks are not `[P]` with each other.

## Parallel Opportunities

- **Phase 1**: T002–T006 all parallel
- **Phase 2**: T008–T013 (six independent DDL blocks); T018/T019/T020 (three test files); T023/T024;
  T026/T027; T030/T031; T034. The heaviest parallel window in the plan
- **Phase 3**: T036–T040 (five test files); T042–T045 (four collectors, one file each)
- **Phase 4**: T058–T061 (four test files); T064 with any of them
- **Phase 5**: T073–T076 (four test files)
- **Phase 6**: T083–T086 (four test files)
- **Phase 7**: T093–T095 (three test files)
- **Phase 8**: T102–T104 (three test files)
- **Phase 9**: T109–T115 — seven independent panels
- **Phase 10**: T116–T120 all parallel

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (T001–T057).** That yields a system whose queue is computed from real
observations of the machine it runs on, with every displayed number traceable to a recorded row and derived
by a pure function. It is demonstrable and defensible on its own, and it is the half of the feature that
answers the reviewer's sharpest objection to feature 001.

**The two phases to protect after that are 4 and 5, in that order.** Phase 4 is the feature's central claim —
agents that could have been wrong. Phase 5 is the constitutional obligation the v1.1.0 amendment was granted
against: permission to add a live external dependency was conditional on proving the decision path survives
its absence. Shipping the dependency without the proof would leave the constitution asserting an obligation
the code does not meet, which is worse than not having amended it.

**Cut order if the work compresses**: Phase 9 UI beyond T109 and T110, then Phase 7, then Phase 6. Note what
each cut costs. Dropping Phase 6 means outcomes resolve by operator confirmation only, which weakens SC-116's
"resolves by automatic re-check" to a manual step — honest, but a visibly smaller claim. Dropping Phase 7
means reputation does not decay against real time, so FR-137 goes unmet and the cost-of-attack frontier stays
priced against feature 001's configured distribution — which must then be *said*, under FR-144.

**Do not cut Phase 8.** Replay is what the reproducibility claim narrows to under v1.1.0. Without it the
project has traded a guarantee it advertises for realism it cannot audit, and Principle V requires that be
stated rather than quietly carried.

**What not to compress**: Phase 2. The purity test extension at T025 and the model boundary at T028–T033 are
what make every later phase's guarantee structural rather than a matter of discipline. `core/estimates.py`
(T022) in particular is the single decision that lets FR-108 and FR-119 both hold — build it under test, in
the directory the purity test already guards, before anything calls it.

## Notes

- `[P]` tasks touch different files and depend on no incomplete work
- Verify tests fail before implementing — the rejection-reason tests especially, since a validator that
  accepts everything passes a badly written test suite silently
- Re-run T017 at every checkpoint. SC-108 is the one criterion whose failure invalidates the previous feature
  as well as this one
- Commit after each task or logical group
- `src/stakeroute/core/reputation.py::update_reputation` and `src/stakeroute/simulator/` are not modified by
  any task in this list. If a task appears to require it, the design is wrong, not the constraint
