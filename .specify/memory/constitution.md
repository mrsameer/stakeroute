<!--
SYNC IMPACT REPORT
==================
Version change: TEMPLATE (unfilled) → 1.0.0
Bump rationale: MAJOR — initial ratification. No prior concrete version existed;
the file was the unmodified spec-kit placeholder template.

Principles defined (all new):
  - I. Deterministic Decision Path (NON-NEGOTIABLE)
  - II. Explainability Over Sophistication
  - III. Exactly-Once Economics
  - IV. Attention Is a Budgeted Resource
  - V. State What You Have Not Proved
  - VI. Demo Path First

Sections added:
  - Additional Constraints (fills the template's second open section slot)
  - Development Workflow (fills the template's third open section slot)
  - Governance

Template slots extended: the template ships 5 principle slots; 6 principles were
requested and defined. Slot count extended rather than merging principles, since
each maps to a distinct, independently verifiable gate.

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check gate populated
  ✅ .specify/templates/tasks-template.md — "tests are OPTIONAL" reversed to match the
     mandatory testing discipline below; sample auth task replaced (auth is out of scope)
  ✅ .specify/templates/spec-template.md — no change needed; mandatory sections
     already align (Assumptions + Success Criteria carry Principle V obligations)
  ✅ README.md — "Design boundary" and "What this is not" already state
     Principles I and V; no drift
  ✅ .claude/skills/speckit-*/SKILL.md — no agent-specific name drift found

Follow-up TODOs: none. No placeholder tokens deferred.
-->

# StakeRoute Constitution

## Core Principles

### I. Deterministic Decision Path (NON-NEGOTIABLE)

Language models MAY propose hypothesis candidates from raw evidence and MAY render
human-readable explanations. Language models MUST NOT participate in aggregation,
ranking, or settlement.

The path `forecast → aggregation → ranking → settlement` MUST be deterministic and
MUST produce byte-identical results for identical inputs. The system MUST continue
ranking and settling existing hypotheses when model inference is slow, degraded, or
entirely unavailable.

**Rationale**: The product's claim is that economics — not a model's opinion —
decides which agent earns influence. A model anywhere on the decision path
forfeits reproducibility, forfeits latency guarantees, forfeits the ability to
explain any individual ranking, and collapses the argument into "we asked an LLM."
This is the single distinguishing property of the system and is therefore
non-negotiable.

### II. Explainability Over Sophistication

Every number the system surfaces MUST be traceable to the inputs that produced it:
an aggregated probability MUST expose its contributing forecasts, weights, and
discounts; every attention decision MUST record its rank, priority, and a
human-readable reason.

When two mechanisms are available and the simpler one can be fully defended under
questioning while the more sophisticated one cannot, the simpler one MUST be
chosen. A mechanism no team member can derive on a whiteboard is a liability, not
an asset.

**Rationale**: An unexplainable ranking is indistinguishable from an arbitrary one.
A formula that cannot be defended live is worse than a weaker formula that can.

### III. Exactly-Once Economics

At-least-once delivery MUST be assumed. Duplicate delivery is a design reality, not
an error condition.

Every event MUST carry a stable deduplication identifier enforced by a uniqueness
constraint at the storage layer. Every economic effect — credit movement,
reputation adjustment, settlement record — MUST be idempotent. State MUST be
committed before delivery is acknowledged, never after. Replaying an entire event
stream MUST yield identical final balances.

**Rationale**: A ledger that double-settles under redelivery is not a ledger. The
durability guarantee that makes the system recoverable is precisely what creates
duplicates, so idempotency is the required consequence of that choice, not an
optional hardening step.

### IV. Attention Is a Budgeted Resource

The human review budget MUST be finite, explicit, and configurable. The system MUST
NOT present more hypotheses than the budget permits, under any load or attack.

Hypotheses withheld because the budget was exhausted MUST be counted and reported.
Suppression MUST be visible; silent dropping is prohibited.

**Rationale**: The entire thesis is that human attention, not compute, is the
scarce resource. A system that exceeds its own stated budget under pressure has
disproved its own thesis. A system that silently discards work has replaced one
opaque failure with another.

### V. State What You Have Not Proved

The following limitations MUST be stated in user-facing documentation and in demo
narration, not confined to internal notes:

- The capped stake-weighted settlement is derived from a proper scoring rule but is
  NOT claimed to be a proven strategy-proof mechanism.
- Sybil resistance is bounded by an attested-identity trust model and is NOT
  claimed for a permissionless setting.
- The evidence-independence discount is a heuristic and CAN miss hidden correlation.

Displayed metrics MUST derive from recorded run data. Fabricated, hard-coded, or
aspirational benchmark figures MUST NOT be displayed under any circumstances. If a
measurement was not taken, the system MUST show that it was not taken.

**Rationale**: Naming the boundary of a claim is what separates an engineering
result from a marketing one. Overclaiming is the fastest way to lose a technically
literate audience, and a fabricated number invalidates every real number beside it.

### VI. Demo Path First

Build order MUST be: mechanism → simulation → baseline comparison → attack →
measurement → failure recovery → user interface.

Work off the demonstrable path is out of scope. Authentication, accounts, teams,
settings, onboarding, animation, and visual polish MUST NOT be built while any
earlier stage is incomplete. A rough working system beats a polished non-working
one wherever the two conflict.

**Rationale**: The mechanism is the contribution; the interface merely displays it.
Every hour spent on chrome before the attack scenario runs is an hour removed from
the only thing that distinguishes this from a dashboard.

## Additional Constraints

**Data model**: `tenant_id` MUST be present on every record from the first commit,
including in single-tenant demonstrations, so the isolation boundary is defensible
without retrofitting.

**Reproducibility**: Scenarios MUST be seeded and reproducible. Identical
configuration MUST produce identical rankings, identical settlements, and identical
final agent balances. Tie-breaks MUST be deterministic; no ordering may depend on
hash iteration order, wall-clock time, or unseeded randomness.

**Bounded values**: Probabilities MUST be clamped away from exactly 0 and 1 so that
scoring and any log-odds treatment remain well defined. Credit loss on a single
forecast MUST NOT exceed the amount staked. Reputation MUST remain within its
configured bounds and MUST retain a recovery path from the floor.

**Simulation only**: No live third-party integrations. All external systems are
simulated. No blockchain, token issuance, or on-chain settlement.

**Process count**: Service proliferation is not architectural maturity. The system
SHOULD run as three processes (simulator, backend worker, dashboard) and MUST
justify any additional process in the plan's Complexity Tracking table.

## Development Workflow

**Package management**: `uv` only. `uv add <package>` to install, `uv run <tool>` to
execute. `pip` and `uv pip install` are prohibited.

**Types**: Type hints are required on all code. `uv run pyright` MUST pass. Optional
values require explicit `None` checks.

**Formatting and linting**: `uv run ruff format .` and `uv run ruff check .` MUST
pass. Line length is 88 characters. Fix order for any CI failure is formatting,
then type errors, then lint.

**Testing**: `uv run pytest`. Async tests use `anyio`, not `asyncio`. Mechanism code
— aggregation, scoring, settlement, reputation, independence discounting — MUST
have unit tests before it is wired into the pipeline. Idempotency and Sybil
resistance MUST have dedicated tests. Every bug fix MUST add a regression test.

**Naming**: PEP 8. `snake_case` for functions and variables, `PascalCase` for
classes, `UPPER_SNAKE_CASE` for constants. Public APIs require docstrings.

**Adversarial review**: At least one team member MUST be assigned to break the
system rather than extend it — hunting duplicate settlement, reputation exploits,
undetected evidence correlation, capital exhaustion, race conditions, and malformed
forecasts. Findings feed the trade-off discussion as evidence, not speculation.

## Governance

This constitution supersedes ad-hoc practice. Where this document and convenience
conflict, this document wins.

**Amendment procedure**: Amendments MUST be recorded in this file with a Sync
Impact Report, a version bump, and an updated amendment date. Dependent templates
(`plan-template.md`, `spec-template.md`, `tasks-template.md`) MUST be checked for
drift in the same change.

**Versioning policy**: Semantic versioning. MAJOR for backward-incompatible
principle removal or redefinition; MINOR for a new principle or materially expanded
guidance; PATCH for clarifications and wording.

**Compliance review**: `/speckit-plan` MUST evaluate its design against the
Constitution Check gate before Phase 0 research and again after Phase 1 design. Any
violation MUST be recorded in the plan's Complexity Tracking table with the simpler
alternative that was rejected and why. An unjustified violation blocks
implementation.

**Non-negotiable status**: Principle I may not be waived by the Complexity Tracking
escape hatch. A design placing model inference on the decision path is rejected, not
justified.

**Version**: 1.0.0 | **Ratified**: 2026-08-29 | **Last Amended**: 2026-08-29
