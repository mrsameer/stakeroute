# Phase 0 Research — Real System Mode

Decisions are numbered from D-011, continuing feature 001's sequence (D-001 … D-010). Each records
what was chosen, why, and what was rejected. Where a decision costs something, the cost is stated
rather than engineered around — Principle V.

The spec left no `NEEDS CLARIFICATION` markers: its Resolved Clarifications section already settled
the evidence source (Option A, host and repository). The unknowns resolved below are therefore
technical rather than product-level.

---

## D-011: Model access — Gemini on Vertex AI via `google-genai`, behind a `ModelClient` Protocol

**Decision**: Add `google-genai`. Configure it for the Vertex AI backend using the authorized-user
credentials already present at `vertex-ai-credentials.json` (type `authorized_user`, carrying
`refresh_token` and `quota_project_id`). The path is supplied through
`GOOGLE_APPLICATION_CREDENTIALS`; the file is untracked and already covered by two `.gitignore`
rules (`vertex-ai-credentials.json` and `*-credentials.json`).

Nothing outside `stakeroute/model/gemini.py` imports the SDK. Every caller depends on the
`ModelClient` Protocol in `model/protocol.py`, which has three implementations: the Gemini adapter,
a `NullModelClient` that reports the capability as unavailable (FR-126), and a `RecordedModelClient`
that serves previously recorded responses and raises on a cache miss (used by replay, D-012).

**Rationale**: The Protocol is the whole point. It makes "runs with no model at all" (FR-126,
SC-113) and "makes no outbound request during replay" (FR-130, SC-107) structural properties rather
than flags to be honoured — a replay physically cannot reach the network, because the object it
holds has no network code in it. It also matches the transport indirection feature 001 already uses
(`SignalTransport`, D-003), so the pattern is one the codebase already defends.

**Alternatives considered**:

- *Raw REST over `httpx` with hand-rolled OAuth refresh.* One fewer dependency, but the refresh
  loop, retry semantics and error taxonomy become ours to maintain and test. Rejected: the
  dependency is cheaper than the code.
- *Google AI Studio API key instead of Vertex.* No API key exists in this environment; the
  credentials that do exist are Vertex authorized-user. Rejected as unavailable.
- *A local model.* Removes quota and latency risk, but US3 exists precisely to prove the system
  survives a degrading remote dependency. Removing the risk would remove the demonstration.

**Cost**: A network dependency with quota, latency and availability entirely outside the project's
control. This is what US3, FR-120 and FR-126 exist to bound.

---

## D-012: Reproducibility is replayable, not regenerable

**Decision**: A run is replayable if and only if every non-deterministic input is durably recorded
*before* anything consumes it. Three classes exist, and all three are recorded:

| Non-determinism | Recorded as | Consumed from |
|---|---|---|
| Model responses | `model_interactions` (accepted rows only feed anything) | the recorded row |
| Real observations | `events` (unchanged table, real provenance) | the recorded row |
| Wall-clock reads | `decided_at_ms` on `attention_decisions`, `resolved_at_ms` on `resolutions` | the recorded value |

Replay reads these from the source database, drives the identical pipeline into a **scratch
database** with `mode='replay'`, then compares rankings, settlements and final balances byte for
byte against the originals. A mismatch is reported as a divergence with the first differing record
named (FR-131); the original is never written to.

**Rationale**: Feature 001's guarantee was *regenerable* — same seed, same run, from scratch. A
model breaks that and no amount of engineering restores it. What survives is the property that
actually matters to a reviewer auditing a past decision: the recorded run reproduces exactly. The
decision path was already built for this without anyone planning it — the core is import-linted
pure, and `run_ranking_pass` already takes `decided_at_ms` as an argument rather than calling the
clock. Replay needs no new determinism; it needs the inputs written down.

The scratch-database choice is what makes FR-131 honest. Replaying in place would let a divergence
overwrite the evidence of itself.

**Alternatives considered**:

- *Seed the model.* Temperature-zero sampling is not a reproducibility guarantee across model
  versions, and the version is not ours to pin indefinitely. Rejected as a guarantee that would
  quietly stop holding.
- *Keep model output out of the record entirely and re-derive on replay.* Would require calling the
  model during replay, violating FR-130 outright.
- *Replay in place with a rollback.* Cheaper, but a crash mid-replay leaves the record corrupted,
  and FR-131 requires divergence be surfaced rather than absorbed.

**Cost**: The constitution's Reproducibility constraint must be amended to distinguish the two
(see plan.md). This narrowing must be stated wherever the feature-001 guarantee is claimed
(FR-144) — README and demo narration included.

---

## D-013: Reasoning agents receive a bundle and nothing else

**Decision**: An agent is a coroutine with the signature

```
async def forecast(bundle: EvidenceBundle, model: ModelClient) -> ForecastProposal | Rejection
```

It receives no `Repository` handle, no tenant id, and no hypothesis outcome. `EvidenceBundle` is a
frozen dataclass built by `real/scopes.py`, which queries only the sources inside the agent's
declared `EvidenceAccessScope` and is the single place a bundle can be constructed. The bundle is
serialized and recorded against the resulting forecast, so the exclusion is verifiable after the
fact rather than asserted (FR-113, SC-102).

Scopes are drawn along source lines: one agent sees host metrics, one sees application logs, one
sees version-control and test history, one sees container events (where that source is present).

**Rationale**: Enforcement beats assertion. An agent that has no way to reach the outcome cannot
leak it, and a reviewer can check that claim by reading one function signature rather than auditing
a prompt. Recording the bundle turns SC-102 into a query over stored rows — "no recorded bundle
contains an outcome field" — instead of a code review.

Source-line scopes also make the independence the mechanism discounts against a real property.
Feature 001's evidence clusters were labels; here they are genuinely different views of the machine.

**Alternatives considered**:

- *One model context prompted with several personas.* Far cheaper, and it would make the
  independence discount meaningless — which is precisely the property under test. Rejected.
- *Scope enforced by prompt instruction.* Not enforcement.
- *Scopes drawn randomly over observations rather than by source.* Would produce statistically
  independent slices but no defensible story about why two agents disagree.

**Cost, stated not fixed**: All agents share one underlying model, so they are correlated in a way
the evidence-group discount — which keys on evidence, not on reasoner — does not catch. FR-143
requires this be documented. It is a genuine new limitation of the feature.

---

## D-014: Redact at the ingestion boundary, not at egress

**Decision**: Collectors emit raw content into a redaction pass that (a) keeps only fields on a
declared per-source allow-list and (b) rewrites absolute filesystem paths, user account names,
environment variable values and credential-shaped strings. Only the redacted payload is written to
`events`. Nothing unredacted is ever stored, displayed or transmitted. Each event records which
redaction rules fired.

**Rationale**: Two failure modes, and this choice removes both. Egress-only redaction leaves the
database holding a developer machine's absolute paths and possibly credential-shaped strings, and
it fails open — one new call site that forgets the filter leaks. Redacting at the single ingestion
boundary means there is exactly one place to audit, and SC-115 becomes checkable over
`model_interactions` *and* `events` rather than over every code path that might send something.

Principle II applies directly: an allow-list at one boundary is defensible under questioning; a
filter sprayed across every egress point is not.

**Alternatives considered**:

- *Egress-only redaction.* Rejected above.
- *Avoid sensitive sources.* This was an earlier draft's assumption and the spec explicitly records
  that it fails under Option A — developer-machine logs and git history routinely carry the user's
  account name in absolute paths.
- *Hashing rather than redaction.* Preserves correlation across observations, but a hashed
  home-directory path is still a stable identifier and buys nothing the demonstration needs.

**Cost**: Local reasoning loses fidelity — an agent cannot see the actual path that filled up, only
that a redacted path did. Accepted; the checkable conditions (D-017) operate on the host directly,
not on redacted text.

---

## D-015: Four collectors, absence is a first-class state

**Decision**: `host.metrics` (psutil: CPU, memory, disk, process table), `app.logs` (the system's
own log output), `repo.vcs_tests` (`git log`, `git status`, and test-run results), and
`container.events` (`docker events` where a runtime is present). Each collector polls on an
interval, emits observations carrying a stable `source_event_id`, and updates a row in
`observation_sources` with its liveness state and last-seen time.

A source whose binary or runtime is missing is recorded as `absent`, not omitted. A source that has
reported nothing for longer than its configured silence threshold is `silent`, distinct from
`quiet`.

**Rationale**: FR-141 requires a silent source be distinguishable from a quiet period, and the only
way to do that is to make the source itself a record with its own state. `psutil` is added rather
than shelling out to `ps`/`vm_stat` because those differ across macOS and Linux and this must run on
both. Git, pytest and docker are shelled out to because their command-line output is a stable
contract and adding three clients to read four fields each would be service proliferation in
library form.

**Alternatives considered**:

- *A single generic "host" collector.* Would collapse the four evidence access scopes (D-013) into
  one, destroying the independence story.
- *Push-based collection via a log shipper.* More realistic operationally, more moving parts, and
  the constitution's process-count constraint bites. Rejected as beyond a demonstrable system.

**Cost**: The system observes a machine it is itself loading — its own model calls, ingestion and
SQLite writes appear in the metrics it reads. Isolating that would mean observing the host from
outside the host, a larger change than this feature justifies. FR-149 requires the limitation be
stated instead.

---

## D-016: The model names the situation; deterministic code prices it

**Decision**: A model proposal supplies exactly three things — a hypothesis `statement`, a list of
cited `observation_ids`, and a selected `checkable_condition` with its parameters (D-017). It
supplies **no numbers that reach the decision path**. Impact, urgency and review cost are computed
by `core/estimates.py`, a pure function over the cited observations, and each returns its value
together with the basis that produced it.

Citations are validated: an observation id that does not exist, or that lies outside the proposal
window, rejects the whole proposal (FR-122).

**Rationale**: This is the load-bearing decision of the feature, because FR-108 and FR-119 look
like they conflict. FR-108 requires every proposal to carry an estimated impact, urgency and review
cost. FR-119 forbids aggregation, ranking and settlement from reading any model output as a numeric
input. Since `priority_score` consumes exactly those three numbers, a model-estimated impact would
put model output directly into ranking.

Recording the estimate first does not rescue it. "Durably recorded before consumption" is what
makes a run *replayable* (D-012); it does nothing about *whose judgement set the rank*. A recorded
model number driving the queue order is still a model deciding what a human looks at, which is the
one thing the project claims it does not do.

Putting the estimator inside `core/` is deliberate: the purity test already walks that directory and
forbids the imports that would let a model reach it, so the guarantee is enforced by a test that
already exists rather than by a new convention.

The spec's own edge case — "the model is asked to estimate business impact and produces a figure
with no defensible basis" — is not handled here. It is designed out.

**Alternatives considered**:

- *Let the model estimate impact, record it, and rely on the recording for determinism.* Rejected
  above; it satisfies the letter of replay and breaks Principle I.
- *Let the model estimate, then have a human confirm before ranking.* Would satisfy FR-119 but
  makes the queue depend on an operator being present, defeating the attention-budget premise.
- *Drop model proposal entirely and derive hypotheses by rule.* Cheaper and fully deterministic,
  but Principle I explicitly permits proposal, and rule-derived statements cannot produce SC-103's
  "statement that appears nowhere in the codebase or configuration".

**Cost**: The impact figures are cruder than a model's would be — a rule over observation counts,
severities and affected sources rather than a judgement. Stated in the basis field, which is
visible to the operator.

---

## D-017: A fixed registry of checkable conditions

**Decision**: `real/conditions.py` holds a closed registry of parameterized checks — for example
`process_absent(name)`, `disk_free_below(mount, pct)`, `cpu_saturated(threshold, window_s)`,
`test_failing(node_id)`, `container_exited(name)`. A proposal must bind to one registry entry and
supply valid parameters; the model selects, it does not invent. A proposal that binds no condition
is still allowed but resolves by operator confirmation instead of automatically.

Resolution re-runs the bound check at the hypothesis deadline and records the check name, its
parameters, when it ran and what it returned (FR-148).

**Rationale**: This is what promotes automatic resolution from exception to common case, and it is
what makes calibration in real mode measured against verifiable truth rather than an operator's
judgement. A closed registry keeps the model out of outcome determination entirely: the worst a
bad proposal can do is bind a check that returns `false`, which resolves the hypothesis against the
agents who backed it — exactly the intended feedback.

**Alternatives considered**:

- *Let the model write the check.* Arbitrary code execution from model output, and it would put the
  model on the settlement path. Rejected outright, not deferred.
- *Operator confirmation for everything.* Loses the objectivity that motivated choosing the host as
  the evidence source in the first place.

**Cost**: Hypotheses expressible by the registry are the ones that resolve automatically. A
genuinely novel situation falls back to operator judgement, and the registry's coverage becomes a
stated limit.

---

## D-018: Real mode is a separate tenant

**Decision**: Real records are written under tenant `hostops`; the seeded simulation keeps
`acmepay`. Every existing query is already tenant-scoped. Reports and metrics take a single tenant
and refuse to aggregate across tenants; a test asserts no displayed figure spans both (SC-117).

**Rationale**: This is the intended use of a boundary feature 001 modelled from the first commit
and never exercised with more than one tenant — the cheapest available guarantee that no report
ever mixes measured results with generated ones. It also means the two demonstration domains can
coexist honestly: AcmePay stays fictitious and simulated, the host is real, and nothing has to
pretend otherwise.

**Alternatives considered**:

- *A `mode` column alone, same tenant.* One forgotten `WHERE` clause silently mixes real and
  simulated settlements into one Brier score. The tenant scoping is already threaded through every
  query, so it fails closed.

---

## D-019: `mode` on every record, and it is not derivable from tenant

**Decision**: Add `mode TEXT NOT NULL` (`'real' | 'sim' | 'replay'`) to `events`, `hypotheses`,
`forecasts`, `attention_decisions`, `outcomes` and `settlements`, defaulting to `'sim'` so existing
rows and every feature-001 path are unchanged.

**Rationale**: Tenant separates real from simulated but cannot express replay, which reuses the real
tenant's data in a scratch database. FR-139 requires the operator surface to state real, simulated
or replay without ambiguity at all times, and FR-103 requires the distinction at the record level so
no analysis can silently mix them. A default of `'sim'` is what keeps SC-108 true without touching
simulator code.

---

## D-020: A model usage ceiling that degrades capability, never the decision path

**Decision**: `model/budget.py` tracks calls and tokens against a configured per-interval ceiling.
On exhaustion, proposal and prose rendering stop and the state is reported as `ceiling_reached`
with the specific capability named. Ranking and settlement are untouched — they never call the
model at all.

Model state is one of `ok | degraded | ceiling_reached | disabled | unconfigured`, exposed on the
operator surface alongside what is consequently unavailable (FR-124, FR-125).

**Rationale**: Quota is finite and a real evidence flood would otherwise exhaust it mid-population,
leaving some agents with forecasts and others without — an edge case the spec names explicitly.
Reporting consumption against a ceiling turns that from an opaque failure into a stated one.

---

## D-021: Reputation decays against elapsed milliseconds, additively

**Decision**: Add `core/decay.py` with a pure `decay_reputation(current, elapsed_ms, half_life_ms)`.
It is applied by the worker at epoch rollover in real mode only. `core/reputation.py::update_reputation`
is **not modified**.

**Rationale**: FR-137 requires decay against real elapsed time; today's decay is a per-settlement
weight, which is a loop counter. But SC-108 requires every feature-001 result to be reproduced
byte-identically, and `update_reputation` is on that path. Additive is the only option that
satisfies both. `elapsed_ms` is passed in as an argument, so the core still reads no clock and the
purity test still holds.

**Alternatives considered**:

- *Change `update_reputation` to take elapsed time.* Would change simulated results and break
  SC-108 on the first commit.

---

## D-022: Insufficiency is a value, not a small number

**Decision**: A configured `MIN_RESOLVED_FOR_CALIBRATION` (default 10). Below it, calibration and
ranking-quality figures return `None` together with the count they are based on, and the surface
renders "not yet measurable (n=3)". This extends `metrics.py`'s existing
`(value, measured_over)` contract rather than inventing a second one.

**Rationale**: Real runs resolve far fewer outcomes than a seeded scenario, so the early state of
every real metric is "we do not know yet". Feature 001 already established that a metric must be
able to say it was not measured; this is that same rule applied to a regime where it will actually
fire (FR-118, SC-114).

---

## D-023: Duplicate hypotheses are detected by rule

**Decision**: Two open hypotheses are probable duplicates when they bind the **same checkable
condition with the same parameters**, or when their cited observation sets overlap beyond a
configured Jaccard threshold within a time window. Detection lives in `core/duplicates.py` — pure,
deterministic, testable. A detected pair is merged when the conditions match exactly, and flagged
for the operator otherwise.

**Rationale**: FR-110 exists so one real situation does not consume two of two review slots. With
`ATTENTION_BUDGET = 2`, a single duplicated incident is a total queue failure, so this matters more
than its size suggests. A rule over bound conditions and citations is derivable on a whiteboard;
an embedding similarity score is not (Principle II).

**Alternatives considered**:

- *Ask the model whether two hypotheses are the same.* Puts model output in front of an allocation
  decision and is non-deterministic under replay.

---

## D-024: Three processes, agents inside the ingestor

**Decision**: Real mode runs `real/run_ingestor.py` in the process slot the simulator occupies,
with collectors and reasoning agents as coroutines inside it. Worker and dashboard are unchanged.
Total application processes: three.

**Rationale**: The constitution's process-count constraint requires justifying a fourth process in
Complexity Tracking, and there is no justification available — the agents are I/O-bound on model
calls and share the collectors' observation stream, so separating them would add a transport hop to
move data between two coroutines that could hold the same reference.

Feature 001's D-008 note also applies: the worker remains the sole SQLite writer for pipeline
effects, and the ingestor publishes over the transport rather than opening its own write
connection.

---

## Resolved unknowns summary

| Unknown | Resolution |
|---|---|
| Which model SDK and auth path | `google-genai` on Vertex AI, authorized-user credentials, `GOOGLE_APPLICATION_CREDENTIALS` (D-011) |
| How determinism survives a model | Recorded inputs + scratch-database replay with divergence report (D-012) |
| How FR-108 and FR-119 both hold | Model supplies prose and citations; `core/estimates.py` supplies every number (D-016) |
| How agent independence is enforced | Bundle-only signature, scope-built bundles, recorded bundles (D-013) |
| Where redaction happens | Ingestion boundary, allow-list plus rewrite rules (D-014) |
| How outcomes resolve without an operator | Closed registry of re-runnable checks bound at proposal (D-017) |
| How real and simulated stay unmixed | Separate tenants plus a `mode` column (D-018, D-019) |
| Whether feature 001 changes | No. Additive core modules only; `mode` defaults to `'sim'` (D-021, D-019) |
