# Feature Specification: StakeRoute — Attention Market for Autonomous AI Agents

**Feature Branch**: `001-stakeroute-attention-market`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: Deep research report "Blueprint to Win the SignalLabs AI HackDay" (`~/Downloads/deep-research-report.md`), recommending StakeRoute — an attention market where autonomous AI agents must spend scarce reputation capital to escalate a signal to a human, demonstrated on production incident response for a fictitious enterprise "AcmePay".

## Overview

When an enterprise runs hundreds of autonomous agents, every agent can declare something urgent at zero cost. Human attention, not compute, becomes the binding constraint. StakeRoute makes escalation costly: an agent must stake finite reputation capital behind a probabilistic claim, evidence drawn from the same source is discounted rather than counted as independent corroboration, and only the highest expected-value hypotheses consume a fixed human review budget. When ground truth arrives, calibrated agents gain influence and overconfident ones lose it.

The deliverable is a working, demonstrable system with an adversarial simulator, not a production deployment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - On-call operator receives only what is worth their attention (Priority: P1)

An on-call operations lead at AcmePay is responsible for a platform emitting thousands of signals per hour from logs, metrics, deployments, security tooling and payment processing. Dozens of automated agents interpret those signals and each wants the operator to look at something. The operator opens one screen and sees a short, ranked list — no longer than their declared review capacity — of the hypotheses most worth their time, each showing an aggregated probability, an estimated business impact, how many genuinely independent lines of evidence support it, and why it was ranked where it was.

**Why this priority**: This is the product. Without ranked, budget-bounded routing there is no value proposition and nothing to attack in later stories.

**Independent Test**: Feed a scenario containing one genuine incident, one plausible-but-wrong competing hypothesis, and background noise. Verify the operator screen shows exactly the configured number of review slots and that the genuine incident occupies rank 1.

**Acceptance Scenarios**:

1. **Given** a scenario with 500+ background signals, one true payment incident and one misleading database hypothesis, **When** the operator views the queue with a review budget of 2, **Then** exactly 2 hypotheses are presented and the payment incident is ranked #1.
2. **Given** a hypothesis with high probability but negligible business impact and another with moderate probability but severe impact, **When** priority is computed, **Then** the high-impact hypothesis outranks the high-probability one.
3. **Given** any presented hypothesis, **When** the operator inspects it, **Then** the system shows the contributing agents, their stakes, their individual probabilities, the evidence groups used, and the resulting aggregate — with no unexplained numbers.
4. **Given** more candidate hypotheses than review slots, **When** the queue is rendered, **Then** hypotheses beyond the budget are visibly withheld rather than silently dropped, with a count of what was suppressed.

---

### User Story 2 - The mechanism survives a coordinated attack (Priority: P1)

An adversary (or simply a badly configured fleet) floods the system with many new agents that all confidently assert the same wrong diagnosis, and with many agents that re-report the same underlying evidence. The operator triggers these attacks from the demo surface and watches naive baselines fail while StakeRoute holds.

**Why this priority**: Equal in priority to routing itself. The event's judging explicitly rewards demonstrated behavior under stress, and this is the difference between a ranking heuristic and a mechanism.

**Independent Test**: Run the identical scenario through three ranking strategies — majority vote, highest-self-reported-confidence, and StakeRoute — then inject 50 Sybil agents and compare top-ranked hypotheses across the three.

**Acceptance Scenarios**:

1. **Given** a scenario where StakeRoute and both baselines agree on the correct top hypothesis, **When** 50 newly-created agents assert a false hypothesis at high confidence, **Then** the majority-vote baseline promotes the false hypothesis to rank 1 while StakeRoute keeps the true incident in the top 2.
2. **Given** a hypothesis supported by 3 independent evidence groups, **When** 20 additional agents report forecasts derived from one already-counted evidence group, **Then** the aggregated probability increases by no more than a small bounded amount attributable to the correlation discount, rather than scaling with agent count.
3. **Given** an agent that repeatedly escalates every signal at maximum stake, **When** its epoch budget is exhausted, **Then** its further escalations are rejected until its stakes are released or its budget replenishes.
4. **Given** a single historically high-reputation agent that issues a confidently wrong forecast, **When** the outcome resolves against it, **Then** its reputation and credit balance both fall and its influence on subsequent aggregations is measurably reduced.
5. **Given** a correct hypothesis supported by only one low-population agent but backed by evidence uncorrelated with the majority, **When** ranking is computed, **Then** it is not suppressed purely by numerical minority status.

---

### User Story 3 - Outcomes settle and the system remembers (Priority: P2)

A hypothesis resolves — the payment incident was real, the database saturation was not. The system scores every forecast against the outcome, moves credits and reputation accordingly, and records an auditable settlement history that explains why each agent's future influence changed.

**Why this priority**: Without settlement the mechanism has no feedback loop and reputation is arbitrary. It is P2 only because routing and attack resistance must be visible first.

**Independent Test**: Resolve a hypothesis to true and verify that agents who forecast closer to the truth than the prior gain, agents who forecast worse than the prior lose, and that no agent loses more than it staked.

**Acceptance Scenarios**:

1. **Given** a prior probability of 0.30, an agent forecast of 0.90 and a resolved outcome of true, **When** settlement runs, **Then** the agent's credit change is positive and proportional to both its stake and its improvement over the prior.
2. **Given** an agent forecast of 0.99 and a resolved outcome of false, **When** settlement runs, **Then** the agent incurs a loss capped at exactly its staked amount and a downward reputation adjustment.
3. **Given** an agent with strong performance far in the past and poor recent performance, **When** reputation is recomputed, **Then** recent results dominate, so historical standing decays rather than persisting indefinitely.
4. **Given** any settled forecast, **When** the settlement record is inspected, **Then** it shows the forecast, the outcome, the score, the credit change and the reputation change as a complete, replayable audit trail.
5. **Given** a resolved hypothesis, **When** settlement completes, **Then** all credits staked on it are released back to their agents' available balances net of the settlement result.

---

### User Story 4 - The pipeline survives a component failure without corrupting the economy (Priority: P2)

A processing worker is killed mid-flight while signals continue to arrive. On restart, unprocessed work completes and no agent is paid or penalized twice.

**Why this priority**: This is the architecture story that distinguishes the project from an in-memory demo, and it is directly demonstrable in five minutes.

**Independent Test**: Start a scenario, kill the worker mid-stream, continue emitting signals, restart the worker, then assert that every emitted signal is reflected exactly once in the ledger.

**Acceptance Scenarios**:

1. **Given** signals are being processed, **When** the worker is terminated before acknowledging in-flight work, **Then** after restart that work is processed and no signal is lost.
2. **Given** the same signal is delivered more than once, **When** it is processed, **Then** it produces exactly one stored event, one forecast effect and one settlement effect.
3. **Given** a worker restart during an open hypothesis, **When** processing resumes, **Then** aggregated probabilities and agent balances match the values that a single uninterrupted run would have produced.
4. **Given** the hypothesis-generation component is slow or unavailable, **When** signals continue to arrive, **Then** ranking and settlement for existing hypotheses continue to operate.

---

### User Story 5 - The operator can see that the mechanism actually works (Priority: P3)

Alongside the queue, the demo surface reports quantitative results: how often the routed hypotheses were the ones that mattered, how often attention was wasted, how quickly true incidents reached a human, how well-calibrated the aggregate forecasts were, and system throughput.

**Why this priority**: Turns claims into evidence. Valuable for judging but the system functions without it.

**Independent Test**: Run the same scenario through all three strategies and confirm the metrics panel reports distinct, non-fabricated values for each.

**Acceptance Scenarios**:

1. **Given** a completed scenario run, **When** metrics are displayed, **Then** precision at the review budget, false-escalation rate, time-to-attention for true incidents, aggregate calibration score and events processed per second are all shown.
2. **Given** three ranking strategies over an identical scenario, **When** results are compared, **Then** the metrics are presented side by side over the same event stream.
3. **Given** any displayed metric, **When** its provenance is questioned, **Then** it traces to recorded run data rather than a hard-coded constant.

---

### Edge Cases

- A hypothesis reaches its deadline with no ground truth available — stakes must be resolved deterministically without inventing an outcome.
- An agent attempts to stake more credits than it has available, or stakes zero.
- An agent submits a probability of exactly 0 or exactly 1, or a value outside [0,1].
- Every forecast on a hypothesis comes from a single evidence group — aggregate confidence must not exceed what one independent witness justifies.
- Two hypotheses tie on priority when review slots are scarce.
- No hypothesis clears the threshold for human review during a quiet period.
- An agent's reputation approaches the floor — it must retain a path back rather than being permanently silenced.
- A brand-new agent that is actually correct — must be able to earn influence, so a low starting reputation cannot be an absolute veto.
- Two agents report the same underlying evidence under different group labels (undetected correlation) — a known limitation that must be stated, not hidden.
- The same real-world incident is described by two differently-worded hypotheses.
- Signals arrive out of order or with timestamps in the past.
- A scenario is re-run — results must be reproducible for identical inputs.

## Requirements *(mandatory)*

### Functional Requirements

#### Signals and provenance

- **FR-001**: System MUST ingest signals from multiple distinct simulated sources (logs, metrics, deployments, security, payments) and normalize them into a common event record.
- **FR-002**: Every ingested event MUST carry a tenant identifier, a stable deduplication identifier, a source, and provenance describing where it originated.
- **FR-003**: System MUST guarantee that a given event affects stored state and the economic ledger exactly once, regardless of how many times it is delivered.
- **FR-004**: System MUST persist ingested signals durably enough that a consumer failure does not lose unprocessed work.

#### Agents and their budget

- **FR-005**: System MUST represent each agent with a persistent identity, a reputation score bounded within a configured range, and an available credit balance.
- **FR-006**: System MUST issue each agent a finite credit budget per epoch, so that escalation carries an opportunity cost.
- **FR-007**: Agents MUST be able to publish a forecast consisting of a hypothesis reference, a probability, a staked credit amount, one or more evidence references, an evidence-group label, and an expiry.
- **FR-008**: System MUST reject a forecast whose stake exceeds the agent's available credits, whose probability is outside the valid range, or whose stake is outside the configured per-forecast limits.
- **FR-009**: System MUST hold staked credits as unavailable until the referenced hypothesis resolves or expires.
- **FR-010**: System MUST start previously unknown agents at a low reputation rather than at parity with proven agents.
- **FR-011**: Agent identity MUST be attested by the enterprise; the system MUST NOT assume it can defend against costless identity creation, and this trust boundary MUST be stated in user-facing documentation.

#### Aggregation

- **FR-012**: System MUST group forecasts into hypotheses, where a hypothesis carries a statement, a prior probability, an estimated impact, a deadline and a status.
- **FR-013**: System MUST compute each forecast's influence weight from the agent's reputation, its staked amount, and an independence factor derived from the size of its evidence group.
- **FR-014**: The influence of stake MUST be sub-linear, so that a single well-capitalized agent cannot buy proportionally unbounded influence.
- **FR-015**: The independence factor MUST decrease as more forecasts share an evidence group, so that N correlated reports contribute materially less than N independent ones.
- **FR-016**: System MUST combine weighted forecasts into a single aggregated probability per hypothesis, using a method the team can explain in full during questioning.
- **FR-017**: System MUST expose, for every aggregated probability, the individual forecasts, weights and discounts that produced it.
- **FR-018**: The path from forecast to aggregation to ranking to settlement MUST be deterministic and produce identical results for identical inputs.

#### Attention allocation

- **FR-019**: System MUST compute a priority for each hypothesis that increases with aggregated probability, business impact and urgency, and decreases with the cost of human review.
- **FR-020**: System MUST enforce a configurable finite human review budget and present only the top-ranked hypotheses within it.
- **FR-021**: System MUST record every allocation decision with its rank, priority, aggregated probability and a human-readable reason.
- **FR-022**: System MUST report how many candidate hypotheses were withheld because the attention budget was exhausted.
- **FR-023**: System MUST provide majority-vote and highest-confidence baseline rankings over the identical event stream for side-by-side comparison.

#### Settlement and memory

- **FR-024**: System MUST accept ground-truth outcomes for hypotheses, whether supplied by the simulator or by an operator decision.
- **FR-025**: System MUST score each forecast against the outcome using a proper probabilistic scoring rule, rewarding calibration rather than binary correctness.
- **FR-026**: Credit settlement MUST be a function of the agent's stake and its measured improvement over the hypothesis prior, with loss capped at the amount staked.
- **FR-027**: System MUST adjust agent reputation from settlement results, weighting recent performance above older performance so that standing decays.
- **FR-028**: System MUST resolve stakes on hypotheses that expire without ground truth by a stated, deterministic rule, without fabricating an outcome.
- **FR-029**: System MUST retain a durable, inspectable settlement history for every forecast.

#### Demonstration surface

- **FR-030**: System MUST present a single operator screen showing the ranked human-review queue, remaining attention budget, per-hypothesis probability, impact and independent-evidence count, and an agent table with reputation, stake, forecast and settlement state.
- **FR-031**: System MUST provide on-demand controls to run the baseline scenario, inject a Sybil agent flood, inject correlated-evidence agents, terminate a processing component, and resolve an outcome.
- **FR-032**: System MUST display the StakeRoute ranking against the baseline rankings simultaneously so divergence under attack is visible without narration.
- **FR-033**: System MUST report precision at the review budget, false-escalation rate, time-to-attention, aggregate calibration score and events processed per second from recorded run data only.
- **FR-034**: System MUST NOT display fabricated or hard-coded benchmark figures.

#### Simulation

- **FR-035**: System MUST provide a scenario generator producing background noise signals, one genuine incident, at least one plausible competing false hypothesis, and a population of agents with differing accuracy profiles including honest specialists, general agents, noisy agents, adversarial agents and newly-created agents.
- **FR-036**: System MUST support injecting a configurable number of Sybil agents and correlated-evidence agents into a running scenario.
- **FR-037**: Scenarios MUST be reproducible, producing identical outcomes for identical configuration.

#### Model usage boundary

- **FR-038**: Language-model inference MAY be used to propose hypothesis candidates from raw evidence and to produce human-readable explanations.
- **FR-039**: Language-model inference MUST NOT participate in aggregation, ranking or settlement.
- **FR-040**: System MUST continue ranking and settling existing hypotheses when model inference is unavailable or slow.

#### Honesty about limits

- **FR-041**: System documentation and demo narration MUST state that the capped stake-weighted settlement is derived from a proper scoring rule but is not claimed to be a proven strategy-proof mechanism.
- **FR-042**: System documentation MUST state that the evidence-independence discount is a heuristic that can miss hidden correlation.
- **FR-043**: System documentation MUST state that Sybil resistance is bounded by the attested-identity assumption and is not claimed for a permissionless setting.

### Key Entities

- **Tenant**: The isolation boundary for all data. Present on every record from the outset even though the demonstration uses a single tenant.
- **Agent**: An autonomous participant with a reputation score, an available credit balance, an accuracy profile in simulation, and a settlement history.
- **Signal / Event**: A normalized observation from a source, carrying provenance, timestamp, tenant and a deduplication identifier.
- **Evidence Group**: A label identifying the underlying source of information a forecast relies on; the unit over which correlation is discounted.
- **Hypothesis**: A candidate explanation under evaluation, with a statement, prior probability, estimated impact, urgency, deadline and status.
- **Forecast**: An agent's staked probabilistic claim about a hypothesis, referencing its evidence and evidence group.
- **Attention Decision**: The record of a ranking pass — hypothesis, aggregated probability, priority, rank and reason.
- **Outcome**: Resolved ground truth for a hypothesis, with resolution time and source.
- **Settlement**: The per-forecast consequence of an outcome — score, credit change and reputation change.
- **Epoch**: The interval over which agent credit budgets are issued and exhausted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the baseline scenario, the genuine incident occupies rank 1 of the human review queue.
- **SC-002**: After injecting 50 Sybil agents supporting a false hypothesis, the majority-vote baseline ranks the false hypothesis first while StakeRoute still ranks the genuine incident in its top 2.
- **SC-003**: Adding 20 forecasts drawn from an evidence group already represented changes the aggregated probability of the affected hypothesis by no more than 5 percentage points.
- **SC-004**: An operator with a review budget of 2 is presented with at most 2 hypotheses regardless of how many candidates exist.
- **SC-005**: Terminating and restarting the processing component mid-run results in zero lost signals and zero duplicated settlements, verified against the ledger.
- **SC-006**: Re-running an identical scenario reproduces identical rankings and identical final agent balances.
- **SC-007**: A viewer can trace any presented hypothesis to its contributing agents, stakes and evidence groups in a single interaction from the queue screen.
- **SC-008**: After outcome resolution, agents whose forecasts improved on the prior end with higher reputation and credits, and agents whose forecasts were worse than the prior end with lower reputation and credits, in every run of the demonstration scenario.
- **SC-009**: No agent's credit loss on any single forecast exceeds the amount it staked.
- **SC-010**: The full demonstration — baseline, attack, failure recovery and settlement — is completed within 5 minutes from a clean start.
- **SC-011**: The system sustains the scenario's signal volume without the operator queue falling behind the event stream, with measured throughput displayed.
- **SC-012**: All four ranking-quality metrics and the throughput metric are populated from recorded run data in every demonstration.

## Assumptions

The following defaults were chosen where the source report left details open. Each is a reasonable default rather than a stated requirement, and each is cheap to change.

- **Scope is a hackathon deliverable due the evening of 2026-08-29**, with a self-imposed feature freeze roughly 90 minutes before submission to leave time for rehearsal and failure testing. Depth of demonstration is prioritized over breadth of features; anything not on the demo path is out of scope. The full day is available, so the P1 and P2 user stories are all treated as in-scope commitments rather than stretch goals, and P3 (metrics) is expected to land rather than being sacrificed under time pressure.
- **All external systems are simulated.** No live integrations with observability, paging, chat or cloud providers are in scope.
- **The demonstration domain is production incident response** for a fictitious enterprise, deliberately not named after any real company.
- **A single tenant is used in the demonstration**, but tenancy is modeled from the start so isolation is defensible under questioning.
- **Human review budget defaults to 2 slots**, configurable.
- **Agent credit budget defaults to 100 per epoch with a per-forecast stake between 1 and 50**, configurable.
- **Reputation is bounded roughly between 0.1 and 1.0**, with new agents starting low and standing decaying over time.
- **Hypotheses that expire without ground truth are voided**: staked credits are returned and reputation is left unchanged. No outcome is inferred.
- **Probabilities are clamped away from exactly 0 and 1** so that scoring and any log-odds treatment remain well defined.
- **Ties in priority are broken deterministically** (by impact, then by hypothesis identifier) so runs stay reproducible.
- **Aggregation uses a weighted average of probabilities** unless the team is confident defending a log-odds formulation under questioning; explainability outranks sophistication here.
- **The operator screen is a single page with no authentication, onboarding, settings or multi-user support.** Login, accounts and polish are explicitly out of scope.
- **Ground truth is supplied by the simulator or by an explicit operator action**; peer-prediction for the no-ground-truth case is named as future work, not built.
- **Hypothesis deduplication across differently-worded statements is best-effort**; residual duplication is a stated limitation.
- **Success is judged on architecture, real-problem fit, originality and a working demo**, so a rough working system beats a polished non-working one wherever the two conflict.

## Out of Scope

- Any blockchain, token issuance, DAO or on-chain settlement.
- Authentication, user accounts, teams, workspaces, onboarding or settings screens.
- Real third-party integrations of any kind.
- A general-purpose agent framework or a clone of any existing signal-routing product.
- Formal proof of strategy-proofness, permissionless Sybil resistance, or learned evidence-dependence modeling — all named explicitly as future work.
