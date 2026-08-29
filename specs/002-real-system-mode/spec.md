# Feature Specification: Real System Mode

**Feature Branch**: `002-real-system-mode`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "need to make it more real system rather than just simulation in new feature branch. use gemini if you need llm and credentials are in the folder of the project"

## Overview

Feature 001 delivered the mechanism and proved it against a seeded scenario. Everything it consumes, however, is manufactured by the same program that evaluates it. Background signals are drawn from a seeded generator. Hypotheses are written into the scenario by hand, along with their business impact and their answer. Agents do not reason at all — each is a dial labelled `accuracy`, and its forecast is drawn from a distribution centred on the ground truth it was already given.

That last point is the one a reviewer will press on, and it is fatal to the strongest claim the project makes. The system asserts that **influence is earned from demonstrated calibration**. In a world where an agent is told the answer and then samples near it, calibration is not demonstrated — it is assigned. Reputation, settlement, and the entire cost-of-attack frontier are computed correctly over numbers that were never at risk of being wrong.

This feature closes that gap. Evidence becomes real observations arriving on a live clock. Hypotheses are proposed from that evidence rather than scripted. Agents become genuine reasoners that see only the evidence they have access to, never the outcome, and are therefore capable of being wrong in ways nobody configured. Outcomes arrive from outside the system. Reputation accrues across many hypotheses over real time instead of resetting each run.

What does **not** change is the thing worth protecting. The decision path — aggregation, ranking, settlement — stays deterministic, explainable, and entirely free of model inference. Introducing a non-deterministic component alongside a system whose central claim is reproducibility is the principal risk of this feature, and the design answer is recorded evidence: whatever a model produces is written down as a durable, inspectable input, and the decision path consumes only what was written down. A recorded run therefore replays to byte-identical rankings even though the run that produced it could never be regenerated.

The deliverable remains a demonstrable system, not a production deployment. "Real" here means *not fabricated by the evaluator* — it does not mean production-scale, multi-tenant, or operationally hardened.

### Demonstration domain

Real mode observes **the machine and repository that run it**: process and resource metrics, application logs, version-control and test-run history, and container lifecycle events. The system's subject becomes its own operation.

This replaces the fictitious "AcmePay" payment platform *for real mode only*. AcmePay remains the domain of the seeded simulation from feature 001, which continues to run unchanged. The two occupy separate tenants, which is what the tenancy boundary — modelled from the first commit and so far exercised by a single tenant — was built for.

The choice earns three things the fictitious domain could not. Faults can be **induced live** during a demonstration — fill a disk, saturate a core, kill a process, commit a failing test — so the attack and incident narratives stop being scripted. Outcomes are **objectively checkable**: whether a process actually died or a test actually failed is a fact the system can re-verify, so calibration is measured against real-world truth rather than an operator's judgement. And it needs **no credentials this environment does not already have**.

It also costs two things, both stated rather than engineered around. The system now observes a machine it is itself loading, so its own activity is part of its evidence. And a real fault severe enough to matter can take out the observer and the ledger together, since they share a host.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agents earn their forecasts instead of being handed them (Priority: P1)

An agent is given access to a specific slice of the available evidence — one source, or one group of related observations — and nothing else. It is not told which hypothesis is true, it is not told its own accuracy, and it has no access to the outcome. It reads what it can see, forms a judgement about a hypothesis, states a probability, and commits stake behind it from its own finite budget. Some agents turn out to be well calibrated. Others turn out to be overconfident, or systematically read one kind of evidence badly. Nobody decided in advance which would be which.

**Why this priority**: This is the whole feature. Every downstream claim the project makes — that reputation is earned, that settlement rewards calibration, that the cost-of-attack frontier prices something real — rests on forecasts that could have been wrong for reasons no one configured. Without this, the rest is a well-instrumented random number generator.

**Independent Test**: Run a set of hypotheses through the real agent population with outcomes withheld. Verify that no agent process has any access path to the outcome, that the resulting forecast distribution is not a function of any configured accuracy parameter, and that after resolution the agents' measured calibration scores differ from one another and were not specified anywhere in configuration.

**Acceptance Scenarios**:

1. **Given** a hypothesis whose outcome is recorded but withheld, **When** an agent produces its forecast, **Then** the evidence bundle supplied to that agent contains no outcome field, no ground-truth label, and no accuracy parameter, and this is verifiable from the recorded request.
2. **Given** two agents with access to different evidence groups about the same hypothesis, **When** both forecast, **Then** their probabilities may differ substantially, and each agent's stated reasoning references only evidence within its own access scope.
3. **Given** a completed set of resolved hypotheses, **When** each agent's calibration is measured, **Then** the measured values differ across the population and none of them appears as a configured constant anywhere in the system.
4. **Given** an agent that has been consistently overconfident across resolved hypotheses, **When** its reputation is recomputed, **Then** its influence on subsequent aggregations falls — with the fall attributable to recorded settlement history rather than to a profile setting.
5. **Given** an agent that reasons well, **When** its budget is exhausted by staking on hypotheses it is confident about, **Then** it cannot escalate further until its stakes release, so conviction is rationed even for correct agents.

---

### User Story 2 - The system runs on real observations rather than generated ones (Priority: P1)

Evidence enters the system continuously from the host machine and the repository — resource and process metrics, application logs, version-control and test-run history, container lifecycle events — on a live clock, with real timestamps and real irregularity: bursts, silences, out-of-order arrivals, and content nobody wrote a template for. Candidate hypotheses are proposed from that evidence stream rather than declared in a scenario file, and each carries an estimated impact and urgency derived from the evidence rather than assigned by the author.

**Why this priority**: The attention market's premise is that the operator's queue reflects the world. A queue computed over signals the program invented is a closed loop. Equal priority to US1 because an honest agent reasoning over fabricated evidence is only half the fix.

**Independent Test**: Leave the system running against the host with no scenario invoked, induce a real fault (saturate a core, fill a volume, kill a process, break a test), and confirm a hypothesis reaches the operator queue whose statement, impact and urgency exist nowhere in the codebase or configuration, traceable to the specific observed records that produced it.

**Acceptance Scenarios**:

1. **Given** the system running with no scenario triggered, **When** new observations arrive from a real source, **Then** they are ingested, normalized, and durably recorded with their real provenance, real timestamps, and a stable deduplication identifier.
2. **Given** a set of ingested observations, **When** hypothesis proposal runs, **Then** each proposed hypothesis cites the specific observations that motivated it, and a viewer can open any hypothesis and read the underlying evidence.
3. **Given** a proposed hypothesis, **When** its impact, urgency and review cost are inspected, **Then** each is shown with the basis on which it was estimated, and estimated values are visibly distinguished from operator-confirmed ones.
4. **Given** two proposals describing the same underlying situation, **When** they enter the queue, **Then** the system either merges them or presents them as a flagged possible duplicate — it does not silently double-count the situation across two queue slots.
5. **Given** observations arriving out of chronological order or bearing timestamps in the past, **When** they are ingested, **Then** they are recorded and ranked without corrupting ordering or producing a duplicate economic effect.

---

### User Story 3 - The market keeps running when the model does not (Priority: P1)

The language model becomes slow, rate-limited, unavailable, or returns something unusable. The operator's queue keeps ranking. Open hypotheses keep settling when outcomes arrive. Existing agents keep being scored. The only thing that stops is the proposal of *new* hypotheses and the rendering of prose explanations, and the operator can see plainly that this is what has stopped.

**Why this priority**: Constitution Principle I requires exactly this behaviour, and today it is untestable because there is no model to remove. Introducing a model without simultaneously proving the system survives its absence would convert the project's strongest architectural claim into an unverified assertion. P1 because it must land in the same change that introduces the dependency, not after it.

**Independent Test**: With hypotheses open and forecasts in flight, make the model endpoint unreachable. Verify that ranking passes continue to complete, that settlement of resolved hypotheses continues, that no request hangs indefinitely, and that the operator surface reports degraded proposal capability rather than failing silently or showing stale data as current.

**Acceptance Scenarios**:

1. **Given** the model service is unreachable, **When** a ranking pass runs, **Then** it completes over the existing recorded forecasts and produces the same result it would have produced with the model available.
2. **Given** the model service is unreachable, **When** an outcome is resolved, **Then** settlement completes and credits and reputation move normally.
3. **Given** the model service is responding slowly, **When** its response exceeds the configured time limit, **Then** the request is abandoned within that limit and no part of the decision path waits on it.
4. **Given** the model returns malformed output, an out-of-range probability, or a refusal, **When** that response is handled, **Then** it is rejected and recorded as a rejected response, and no forecast, hypothesis or economic effect is created from it.
5. **Given** the model is degraded or its usage limit has been reached, **When** the operator views the surface, **Then** the degradation is stated explicitly alongside what is consequently unavailable, rather than being inferable only from an absence.
6. **Given** any model response whatsoever, **When** the aggregation, ranking and settlement computations are examined, **Then** none of them reads any model output as an input to a number.

---

### User Story 4 - Outcomes arrive from outside the system (Priority: P2)

A hypothesis resolves because something happened in the world, not because the program looked up an answer it had stored. The resolution is recorded with its source and the time it arrived, settlement runs against the forecasts that were open at the time, and the audit trail shows who or what resolved it.

**Why this priority**: Settlement is the feedback loop that makes reputation mean anything, and an outcome the evaluator supplied to itself closes the same loop US1 opens. P2 rather than P1 because US1 and US2 deliver a genuinely more real system on their own — agents can reason over real evidence and be scored against operator-confirmed outcomes from day one.

**Independent Test**: Resolve a real hypothesis by an external act, and verify the settlement record names the external source and arrival time, that the forecasts settled are exactly those open when it arrived, and that the outcome value exists nowhere in the system prior to that moment.

**Acceptance Scenarios**:

1. **Given** an open hypothesis with forecasts against it, **When** an outcome arrives from an external source, **Then** settlement runs against exactly the forecasts open at that time and records the outcome's source and arrival time.
2. **Given** a resolved hypothesis, **When** its audit trail is inspected, **Then** it shows the resolution source, whether it was an operator judgement or an automatic determination, and the evidence relied on.
3. **Given** a hypothesis reaching its deadline with no outcome available, **When** expiry runs, **Then** stakes are returned under the stated rule and no outcome is inferred or invented.
4. **Given** the same outcome delivered more than once, **When** it is processed, **Then** exactly one settlement effect occurs and balances are unchanged by the redelivery.
5. **Given** an outcome that contradicts an earlier provisional resolution, **When** it arrives, **Then** the correction is recorded as a new auditable event rather than by overwriting settlement history.

---

### User Story 5 - Reputation accumulates across real time (Priority: P2)

The system runs over days and many hypotheses rather than one scripted epoch. An agent's standing is the accumulated result of everything it has forecast, with recent performance dominating. An operator can look at any agent and see its record: how many forecasts, how well calibrated, how its influence has moved, and why.

**Why this priority**: The cost-of-attack analysis prices reputation as the scarce input an attacker cannot mint, and its frontier table is the strongest economic claim the project makes. That claim is currently untested against a reputation that was ever actually earned. P2 because it requires US1 to have any meaning, and accrues value with elapsed time rather than at a single moment.

**Independent Test**: Operate over a sequence of hypotheses spanning multiple epochs, then verify that agent standings at the end are a function of recorded settlement history alone, that they are reproducible by replaying that history, and that at least one agent's ordering relative to another changed as a result of measured performance.

**Acceptance Scenarios**:

1. **Given** an agent with forecasts settled across several epochs, **When** its reputation is inspected, **Then** it is shown alongside the settlement records that produced it, and recomputing from those records reproduces the same value.
2. **Given** an agent with strong early performance and poor recent performance, **When** enough time passes, **Then** its influence falls, demonstrating decay against real elapsed time rather than against a loop counter.
3. **Given** an agent at the reputation floor, **When** it produces well-calibrated forecasts, **Then** its standing recovers, so the floor is not an absorbing state.
4. **Given** credit budgets issued per epoch, **When** epochs roll over on a real clock, **Then** budgets replenish and released stakes return, without any agent gaining or losing credits through the rollover itself.
5. **Given** an accumulated reputation distribution, **When** the cost-of-attack report is generated, **Then** its frontier is computed against reputations that were earned rather than configured, and the report states which.

---

### User Story 6 - Every real run replays exactly (Priority: P2)

A run that happened once can be replayed from its recorded inputs and produce byte-identical rankings, settlements and balances — even though the run itself involved a component that would never produce the same output twice. A reviewer can therefore audit any past decision without having to trust that it was reproducible in principle.

**Why this priority**: Determinism is a non-negotiable constitutional property and this feature introduces the one thing that threatens it. Reproducibility must be redefined precisely — from *regenerable* to *replayable* — and demonstrated, or the feature has traded the project's foundational claim for realism. P2 only because it is verified rather than user-facing; its failure would invalidate US1 through US5.

**Independent Test**: Capture a live run, replay it from recorded inputs alone with the model made unreachable, and compare rankings, settlement records and final balances byte for byte.

**Acceptance Scenarios**:

1. **Given** a completed live run, **When** it is replayed from its recorded inputs with no model reachable, **Then** rankings, settlements and final agent balances are byte-identical to the original.
2. **Given** any model interaction, **When** it is inspected, **Then** the request, the response, the time it took, and whether it was accepted or rejected are all recorded and readable.
3. **Given** a replay, **When** it runs, **Then** it makes no outbound model request of any kind.
4. **Given** the recorded inputs of a run, **When** any single recorded input is altered, **Then** the replay diverges detectably rather than silently absorbing the change.
5. **Given** the seeded simulation from feature 001 and a real run, **When** both are executed, **Then** both remain available and the seeded scenario continues to produce its previously recorded results unchanged.

---

### Edge Cases

**Model behaviour**

- The model returns prose where a structured judgement was required, or a probability outside the valid range, or a value for a hypothesis it was not asked about.
- The model declines to answer, or answers with a safety refusal.
- The usage quota is exhausted mid-run, part-way through a population of agents, leaving some agents with forecasts and others without.
- Credentials are absent, malformed, or expired at start-up, or expire while running.
- Two agents intended to be independent return near-identical reasoning because they share the same underlying model — correlation that the evidence-group discount, which keys on evidence rather than reasoner, will not catch.
- The model proposes a hypothesis that restates one already open, or one that is unfalsifiable, or one with no plausible resolution path.
- Model latency exceeds the interval between ranking passes, so proposals arrive against a world that has already moved.
- The model is asked to estimate business impact and produces a figure with no defensible basis.

**Real evidence**

- A source goes silent — indistinguishable, initially, from a quiet period with nothing to report.
- A source floods, exceeding what the model budget can process, forcing a stated selection policy rather than silent dropping.
- Observations contain personal or sensitive data that would leave the machine in a model request — on a developer host, log lines and version-control history routinely carry absolute paths bearing the user's account name, and occasionally environment values or credential-shaped strings.
- The system's own activity — its model calls, its ingestion, its database writes — appears in the resource metrics it is reading, so it observes a machine it is itself loading.
- A fault severe enough to matter (a full volume, memory exhaustion) degrades the observer and the ledger together, because they share a host.
- Timestamps are skewed, in the future, or in a different timezone from the rest.
- An observation is genuinely ambiguous and no hypothesis is warranted at all.
- The same underlying occurrence is reported by two sources under different identifiers — a process dying appears in container events, application logs and resource metrics at once.
- The host is idle for a long stretch, producing evidence too thin to warrant any hypothesis, so the queue is legitimately and correctly empty.

**Economics under real time**

- A hypothesis stays open far longer than the intended epoch, holding stake across several budget issuances.
- An outcome arrives after the deadline, when stakes have already been returned by expiry.
- No hypothesis clears the review threshold for a long period, leaving the queue legitimately empty.
- Every agent runs out of credits simultaneously because a real burst warranted it, leaving nothing stakeable.
- A real run produces so few resolved outcomes that reputation barely moves and calibration is statistically meaningless — the system must show that it does not yet know, not a confident number.

## Requirements *(mandatory)*

Requirements are numbered from FR-101 to remain distinct from feature 001, whose requirements all continue to apply unless explicitly superseded here.

### Functional Requirements

#### Real evidence

- **FR-101**: System MUST ingest observations from the host machine and the repository it runs in — covering at minimum resource and process metrics, application logs, version-control and test-run history, and container lifecycle events — where neither the content nor the timing of those observations is generated by the system.
- **FR-146**: System MUST collect only fields named on a declared allow-list, and MUST redact absolute filesystem paths, user account names, environment variable values and credential-shaped strings from observation content before that content is transmitted to any external service.
- **FR-147**: Real-mode records MUST be written under a tenant distinct from the seeded simulation's tenant, so that real and simulated evidence cannot be aggregated, ranked, settled or reported together.
- **FR-102**: Every ingested real observation MUST carry its true origin, its true observation time, the time it was received, a tenant identifier, and a stable deduplication identifier, and MUST be durably recorded before it influences anything.
- **FR-103**: System MUST distinguish real observations from simulated ones at the record level, so that no analysis, metric, or report can silently mix the two.
- **FR-104**: System MUST continue to accept observations that arrive out of order or bear past timestamps, without duplicated economic effect and without corrupting ordering.
- **FR-105**: System MUST apply a stated, visible policy when the volume of real observations exceeds what it can process, reporting what was set aside rather than discarding silently.
- **FR-106**: System MUST NOT transmit observation content to any external service without that transmission being recorded and inspectable.

#### Hypotheses from evidence

- **FR-107**: System MUST propose candidate hypotheses from ingested observations rather than from a hard-coded scenario, and each proposal MUST cite the specific observations that motivated it.
- **FR-108**: Each proposed hypothesis MUST carry an estimated impact, urgency and review cost, and MUST record the basis of each estimate.
- **FR-109**: System MUST visibly distinguish an estimated hypothesis attribute from an operator-confirmed one, and MUST allow an operator to correct an estimate, with the correction recorded.
- **FR-110**: System MUST detect probable duplicate hypotheses and either merge them or flag them, so that one real situation does not consume two review slots unremarked.
- **FR-111**: A proposed hypothesis MUST NOT enter the ranked queue until it has been durably recorded with its supporting observations.

#### Agents that reason

- **FR-112**: Each agent MUST form its forecast by reasoning over evidence, not by drawing from a configured accuracy parameter.
- **FR-113**: The evidence supplied to an agent MUST exclude the hypothesis outcome, any ground-truth label, and any measure of the agent's own accuracy; the supplied evidence MUST be recorded so this exclusion is verifiable after the fact.
- **FR-114**: Each agent MUST have a declared evidence access scope, and MUST NOT receive evidence outside it, so that the independence between agents is a property of the system rather than an assertion.
- **FR-115**: Each agent MUST state a probability, a stake within its budget, the evidence group it relied on, and a human-readable rationale, and MUST have that rationale recorded alongside the forecast.
- **FR-116**: System MUST reject a forecast whose probability is outside the valid range, whose stake exceeds the agent's available credits, or whose claimed evidence lies outside the agent's access scope, and MUST record the rejection.
- **FR-117**: System MUST measure each agent's calibration from its recorded settlement history, and MUST NOT display or store any accuracy figure that was configured rather than measured.
- **FR-118**: System MUST report, for any agent, how many resolved forecasts its measured calibration is based on, and MUST indicate insufficiency rather than presenting a confident figure from too few outcomes.

#### The model boundary, under real conditions

- **FR-119**: Aggregation, ranking and settlement MUST NOT read any model output as a numeric input, and this MUST be verifiable by inspection of the decision path rather than by assurance.
- **FR-120**: System MUST complete ranking passes and settle resolved outcomes while the model service is unavailable, slow, rate-limited, or returning errors.
- **FR-121**: Every model request MUST be subject to a configured time limit, after which it is abandoned; no decision-path operation may wait on a model response.
- **FR-122**: System MUST validate every model response against the shape and value ranges it requires, and MUST reject and record any response that fails, creating no downstream effect from it.
- **FR-123**: System MUST record every model interaction — request, response, latency, and accept-or-reject outcome — durably and inspectably.
- **FR-124**: System MUST surface model degradation to the operator explicitly, naming what capability is consequently unavailable.
- **FR-125**: System MUST operate within a configured ceiling on model usage per interval, and MUST report consumption against that ceiling rather than failing opaquely when it is reached.
- **FR-126**: System MUST start and run with no model configured at all, offering the full mechanism minus hypothesis proposal and generated prose.
- **FR-127**: Model credentials MUST be supplied from outside the source tree, MUST NOT be committed, and MUST NOT appear in logs, recorded interactions, or any operator-facing surface.

#### Determinism under a non-deterministic component

- **FR-128**: The decision path MUST consume only durably recorded inputs, never a live model response in flight.
- **FR-129**: System MUST support replaying a recorded run from its recorded inputs alone, producing byte-identical rankings, settlements and final balances.
- **FR-130**: A replay MUST make no outbound model request.
- **FR-131**: System MUST detect and surface divergence when a replay does not reproduce the recorded result, rather than overwriting the record.
- **FR-132**: The seeded simulation from feature 001 MUST remain available and MUST continue to produce its previously recorded results unchanged.

#### Outcomes and settlement in real time

- **FR-133**: System MUST accept outcomes from a source external to the hypothesis's own creation, recording the source, the arrival time, and whether the determination was an operator judgement or automatic.
- **FR-134**: Settlement MUST apply to exactly the forecasts open at the moment the outcome arrived.
- **FR-135**: A redelivered outcome MUST produce exactly one settlement effect.
- **FR-136**: A correction to an earlier resolution MUST be recorded as a new event; settlement history MUST NOT be overwritten.
- **FR-137**: System MUST issue agent credit budgets and decay reputation against real elapsed time, and MUST NOT create or destroy credits through a budget rollover.
- **FR-138**: An outcome arriving after expiry has already returned stakes MUST be recorded without retroactively re-settling returned stakes, and the fact MUST be visible.
- **FR-148**: Where a hypothesis concerns an objectively checkable condition of the host or repository, the system MUST determine its outcome by re-checking that condition, and MUST record the check performed, when it ran, and what it returned — so that the outcome is auditable independently of any operator judgement.

#### Operator surface

- **FR-139**: The operator surface MUST show whether the system is operating on real evidence, simulated evidence, or a replay, at all times and without ambiguity.
- **FR-140**: The operator surface MUST allow tracing any queued hypothesis to the real observations behind it and to the agent rationales that supported it.
- **FR-141**: The operator surface MUST report ingestion liveness per source, so a silent source is distinguishable from a quiet period.
- **FR-142**: Metrics computed over real runs MUST be reported separately from metrics computed over simulated runs, and MUST show insufficiency rather than a figure when too few outcomes exist.

#### Honesty about limits

- **FR-143**: Documentation MUST state that agents sharing one underlying model are not independent reasoners, that the evidence-group discount keys on evidence rather than on reasoner, and that this correlation is therefore undiscounted.
- **FR-144**: Documentation MUST state which figures derive from measured real-world performance and which remain simulated, wherever both are presented.
- **FR-145**: Documentation MUST state that "real" here means evidence and reasoning not fabricated by the evaluator, and does not claim production scale, hardening, or operational maturity.
- **FR-149**: Documentation MUST state that the system observes a machine it is itself loading, and that a sufficiently severe host fault degrades the observer and the ledger together.
- **FR-150**: Documentation MUST state that the demonstration domain of real mode is the host and repository, not a payment platform, and that the AcmePay scenario remains simulated.

### Key Entities

Entities from feature 001 carry forward. This feature adds:

- **Observation Source**: A real origin of evidence external to the system — resource metrics, application logs, version-control history, test runs, container events — with a liveness state and a last-seen time. The unit at which silence is detected and at which an agent's evidence access scope is declared.
- **Checkable Condition**: The re-verifiable fact a hypothesis about the host asserts, together with the check that determines it. What makes an outcome an observation rather than an opinion.
- **Evidence Access Scope**: The declared set of sources and evidence groups an agent may see. Enforced rather than assumed, and the basis on which agent independence is claimed.
- **Model Interaction**: A durable record of one exchange with the language model — what was sent, what came back, how long it took, whether it was accepted, and if rejected, why. The audit object that makes the model boundary inspectable.
- **Proposal**: A candidate hypothesis derived from observations, with its citations and its estimated attributes, prior to becoming a hypothesis in the queue.
- **Attribute Estimate**: An impact, urgency or review-cost value together with its basis and whether it has been operator-confirmed.
- **Resolution**: An outcome arriving from an external source, with its origin, arrival time, determination method, and its relationship to any prior provisional resolution.
- **Run Record**: The complete set of recorded inputs sufficient to replay a period of operation to identical results, including every accepted model interaction.
- **Operating Mode**: Whether the system is consuming real evidence, seeded simulation, or replaying a recorded run. Present on every record produced.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-101**: No agent forecast in real mode is a function of any configured accuracy value; measured calibration differs across the agent population and every value is traceable to recorded settlements.
- **SC-102**: For every forecast made in real mode, the recorded evidence supplied to that agent contains no outcome, no ground-truth label and no accuracy parameter — verified across 100% of recorded forecasts.
- **SC-103**: At least one hypothesis reaches the operator queue whose statement, impact and urgency appear nowhere in the codebase or configuration, and can be traced to the specific real observations that produced it.
- **SC-104**: With the model service made unreachable, ranking passes complete and resolved outcomes settle with a 100% success rate, and results match those produced with the model available.
- **SC-105**: No decision-path operation exceeds its normal completion time by more than the configured model time limit when the model is slow or unavailable.
- **SC-106**: 100% of model responses that are malformed, out of range, or refusals are rejected and recorded, with zero downstream forecasts, hypotheses or economic effects created from them.
- **SC-107**: Replaying a recorded real run from its recorded inputs alone reproduces rankings, settlements and final agent balances byte-identically, with zero outbound model requests during replay.
- **SC-108**: The seeded simulation from feature 001 produces results identical to those recorded before this feature, and every test from feature 001 continues to pass.
- **SC-109**: Every number displayed for a real run traces to a recorded observation, a recorded forecast, or a recorded settlement; zero displayed figures are configured constants.
- **SC-110**: An operator can determine, within one interaction from the queue, whether the system is on real evidence, simulation or replay, and which sources are currently live.
- **SC-111**: Agent reputation ordering changes at least once over the course of real operation as a result of measured performance, demonstrating that standing is earned rather than assigned.
- **SC-112**: Model credentials appear in zero committed files, zero log lines, zero recorded interactions and zero operator-facing surfaces.
- **SC-113**: The system starts and delivers ranking, settlement and the operator queue with no model configured at all.
- **SC-114**: When fewer than the stated minimum number of outcomes have resolved, calibration and ranking-quality figures display insufficiency rather than a number, in 100% of such cases.
- **SC-115**: Zero absolute filesystem paths, user account names, environment variable values or credential-shaped strings appear in any recorded outbound model request, across 100% of recorded interactions.
- **SC-116**: A fault induced on the host during a demonstration reaches the operator queue as a ranked hypothesis, is forecast by agents holding disjoint evidence access, and resolves by automatic re-check of the condition — end to end, with every step traceable to recorded data and none of it scripted in advance.
- **SC-117**: Zero records produced in real mode share a tenant with records produced by the seeded simulation, and no displayed figure aggregates across the two.

## Assumptions

Chosen as reasonable defaults where the feature description left details open. Each is cheap to revisit.

- **Real mode is added alongside the seeded simulation, not in place of it.** The description says "more real ... rather than just simulation", which reads as addition. The seeded scenario also underpins the reproducibility guarantee, the three-strategy comparison and the cost-of-attack tests; removing it would delete the project's evidence base.
- **The language model is Gemini, accessed through the credentials already present in the project directory.** No other model credentials exist in this environment. Requirements are written against "a language model service" so the choice stays swappable, and FR-126 requires the system to run with none.
- **Agents are distinct reasoners with enforced, non-overlapping evidence access**, rather than one context prompted with several personas. Shared context would make the independence discount meaningless, which is precisely the property under test.
- **Agents sharing one underlying model are correlated in a way the mechanism does not discount.** This is a genuine new limitation introduced by the feature and is stated (FR-143) rather than engineered around.
- **Outcomes are determined automatically wherever the hypothesis names a re-checkable condition of the host**, falling back to operator confirmation otherwise. Observing the host rather than a fictitious enterprise is what makes automatic determination the common case rather than the exception, and it is why calibration in real mode is measured against verifiable truth. Peer prediction remains future work, as in feature 001.
- **Agent evidence access scopes are drawn along source lines** — one agent sees resource metrics, another application logs, another version-control and test history. The sources are genuinely different views of the host, so the independence the mechanism discounts against is a real property rather than a label.
- **Real mode and the seeded simulation occupy separate tenants.** This is the intended use of a boundary feature 001 modelled from the first commit and never exercised with more than one tenant; it is also the cheapest available guarantee that no report ever mixes measured results with generated ones.
- **Reproducibility is redefined from regenerable to replayable.** A live run cannot be regenerated because the model is non-deterministic; the recorded run replays exactly. This is a deliberate narrowing of the feature 001 guarantee and must be stated wherever that guarantee is claimed.
- **Model usage is bounded by a configured per-interval ceiling**, because quota is finite and a real evidence flood would otherwise exhaust it. Exceeding the ceiling degrades proposal capability; it never degrades ranking or settlement.
- **A single tenant remains in use**, with tenancy modelled throughout as before.
- **Deployment stays the three-process shape** — evidence ingestion, worker, dashboard — with the agent reasoners running inside the ingestion process rather than as additional services, per the constitution's process-count constraint.
- **Scope is a demonstrable system, not a production deployment.** Multi-tenant operation, access control, alerting, on-call integration and horizontal scale remain out of scope.
- **Redaction is in scope, because the chosen source carries sensitive content.** An earlier draft assumed sensitive data could be avoided by source selection. Under the host-and-repository source that assumption fails outright: application logs and version-control history on a developer machine routinely contain absolute paths bearing the user's account name, and can contain environment values and credential-shaped strings — all of which would otherwise be transmitted to an external model service. FR-146 and SC-115 carry the consequence. This is a real scope increase relative to the other options considered, and it is the price of Option A.
- **The observer effect is accepted and stated, not corrected for.** The system's own load appears in the metrics it reads. Isolating it would mean measuring the host from outside the host, which is a larger change than this feature justifies; FR-149 requires the limitation be stated instead.

## Dependencies

- **Model access**: Gemini via the credentials file already present in the project directory. The file is untracked and has been added to the ignore list as part of this change. Availability, latency and quota are outside the project's control, which is why US3 exists.
- **A real evidence source**: the host machine and this repository. Needs no credentials and no third-party account. Read access to process and resource metrics, application log output, version-control and test-run history, and container lifecycle events where a container runtime is present. Where it is not, container events are simply an absent source, which FR-141 already requires be visible rather than silent.
- **Feature 001**: the mechanism, ledger, transport, dashboard and cost-of-attack analysis are all preconditions. This feature changes what flows into them, not what they compute.

## Constitution Impact

This feature conflicts with one existing constitutional constraint and must not proceed until it is amended.

- **"Simulation only: No live third-party integrations. All external systems are simulated."** (Additional Constraints) is directly contradicted. An amendment is required before implementation, narrowing the constraint to what it was actually protecting: no blockchain, no token issuance, no on-chain settlement, and no dependency whose absence stops the decision path. A MINOR version bump with a Sync Impact Report, per the governance section.
- **"Reproducibility: identical configuration MUST produce identical rankings"** (Additional Constraints) needs the regenerable-versus-replayable distinction written into it. The guarantee is preserved in substance and narrowed in form; the amendment must say so plainly rather than quietly.
- **Principle I (Deterministic Decision Path)** is *not* in conflict. It already permits model inference to propose hypothesis candidates and render explanations, and already requires the system to keep ranking and settling when inference is unavailable. This feature is the first thing that actually tests that requirement — US3 exists to prove the principle rather than to relax it.
- **Principle V (State What You Have Not Proved)** gains a new obligation: the shared-model correlation between agents (FR-143) and the real-versus-simulated provenance of every displayed figure (FR-144).

## Out of Scope

- Replacing or retiring the seeded simulator.
- Any model inference on the aggregation, ranking or settlement path — prohibited, not deferred.
- Blockchain, token issuance, or on-chain settlement, as before.
- Authentication, accounts, teams, or multi-user operation.
- Isolating the system's own load from the host metrics it observes; the observer effect is stated rather than corrected (FR-149).
- Observing any host other than the one the system runs on; remote or fleet-wide collection is not in scope.
- Retiring or restating the AcmePay domain for the seeded simulation, which keeps it unchanged (FR-150).
- Fine-tuning, training, or evaluation of the language model itself.
- Horizontal scale-out, which remains gated on moving the ledger off SQLite.
- A permissionless identity model; the attested-identity trust boundary from feature 001 is unchanged.

## Resolved Clarifications

### Question 1: Which external evidence source? — **Answered: the host machine and this repository**

FR-101 originally carried an open clarification marker. Three options were presented: (A) the host machine and repository, (B) an HTTP receiver plus public status feeds, (C) replay of a published incident dataset on a live clock.

**Option A was selected.** Recorded here with what it settles and what it costs, so the decision is auditable rather than implicit in the requirements it produced.

**What it settles**: FR-101 names its sources concretely. Faults are inducible live, so the demonstration stops being scripted. Outcomes are re-checkable facts about the host rather than operator judgements, which promotes automatic resolution from an exception to the common case (FR-148) and means calibration in real mode is measured against verifiable truth. Agent evidence access scopes fall naturally along source lines, making the independence the mechanism discounts against a real property rather than a label. No credentials beyond the model access already present are needed.

**What it costs**: The demonstration domain of real mode becomes the host and repository rather than a payment platform; AcmePay survives as the seeded simulation's domain, on a separate tenant (FR-147, FR-150). Redaction moves into scope, because developer-machine logs and version-control history carry absolute paths bearing the user's account name and can carry environment values and credential-shaped strings, all of which would otherwise be transmitted to an external model service (FR-146, SC-115). The system observes a machine it is itself loading, and a severe enough host fault degrades observer and ledger together — both stated rather than engineered around (FR-149).
