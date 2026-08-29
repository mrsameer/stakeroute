# Specification Quality Checklist: Real System Mode

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-29
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

**All items pass.** Two validation iterations were run.

*Iteration 1* left one item failing: FR-101 carried an open clarification marker for the
choice of external evidence source, presented as Question 1 with three options.

*Iteration 2* resolved it. The host machine and this repository were selected. FR-101
now names its sources; the decision and its consequences are recorded under Resolved
Clarifications so the reasoning stays auditable rather than being implicit in the
requirements it produced. Requirement inventory: 50 functional requirements
(FR-101–FR-150), 17 success criteria (SC-101–SC-117), no duplicates, no dangling
cross-references. Numbering is deliberately non-contiguous within sections — FR-146/147
sit with real evidence, FR-148 with outcomes, FR-149/150 with the honesty requirements —
following the precedent set by FR-044 in feature 001.

### Consequences of the source decision that changed the specification

- **Redaction moved into scope.** A draft assumption held that sensitive content could be
  avoided by source selection. Against a developer host that assumption fails: logs and
  version-control history carry absolute paths bearing the user's account name, and can
  carry environment values and credential-shaped strings, all of which would otherwise be
  transmitted to an external model service. FR-146 and SC-115 carry the requirement. This
  is a genuine scope increase relative to the other two options.
- **The demonstration domain of real mode is no longer AcmePay.** It is the host and
  repository. AcmePay survives as the seeded simulation's domain on a separate tenant
  (FR-147, FR-150, SC-117) — the first real use of a tenancy boundary feature 001 modelled
  from its first commit and never exercised beyond one tenant.
- **Automatic outcome determination became the common case.** Host conditions are
  re-checkable facts, so calibration in real mode is measured against verifiable truth
  rather than operator judgement (FR-148).
- **Two limitations are stated rather than engineered around**: the system observes a
  machine it is itself loading, and a severe host fault degrades observer and ledger
  together (FR-149).

### Standing items for the planning phase

**Naming the model in Assumptions and Dependencies is deliberate.** The user named Gemini
explicitly. FR-119 through FR-127 are written against "a language model service" so the
specification stays technology-agnostic; the concrete choice is recorded where the
template puts dependencies, and FR-126 requires the system to run with no model at all.

**A constitution amendment is a precondition, not a follow-up.** The Constitution Impact
section records a direct conflict with the "Simulation only: No live third-party
integrations" constraint and a required narrowing of the reproducibility guarantee from
*regenerable* to *replayable*. Per the governance section, `/speckit-plan` will fail its
Constitution Check gate until that amendment lands.

**Security note recorded during specification.** `vertex-ai-credentials.json` was present
in the working tree, untracked and unmatched by any ignore rule, so an inadvertent
`git add -A` would have committed live credentials. An ignore rule was added on this
branch. FR-127 and SC-112 carry the requirement forward. History was checked across all
refs and the file was never committed, so no rotation is required.
