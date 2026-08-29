# StakeRoute

**An attention market where autonomous AI agents must spend scarce reputation capital to escalate a signal to a human.**

Built for the Signal Labs AI HackDay (29 August 2026) — Tokenomics track.

> When a company runs 1,000 AI agents, every agent can say something is urgent, for free.
> StakeRoute makes them put economic weight behind that claim, rewards calibrated agents,
> discounts correlated evidence, and spends human attention only on the highest-value hypotheses.

## The problem

In an enterprise running hundreds of autonomous agents, compute is not the bottleneck — human
attention is. Every agent can declare an incident critical at zero cost. The usual responses all
fail in a specific way:

| Approach | Failure mode |
|---|---|
| Highest confidence wins | The confidently wrong agent wins |
| Majority vote wins | Sybils and correlated agents win |
| Send everything to the operator | Human attention collapses |
| Let an LLM summarise it | The underlying incentive problem is untouched |

## The mechanism

An agent does not merely publish `"database failure likely: 92%"`. It publishes a probability
**with a stake**, drawn from a finite per-epoch budget, tagged with the evidence group it relied on.

- **Scarce budget** — escalation carries an opportunity cost, so nothing can be marked critical forever.
- **Reputation** — influence is earned from demonstrated calibration and decays over time.
- **Independence discount** — twenty agents reading the same metric are not twenty witnesses.
- **Attention allocation** — probability × impact × urgency ÷ review cost, capped by a fixed human review budget.
- **Settlement** — a proper scoring rule moves credits and reputation once ground truth arrives.

The demonstration domain is production incident response for a fictitious enterprise, "AcmePay".

## Design boundary

> **AI proposes evidence and hypotheses. Economics decides who gets influence. Deterministic policy decides what reaches the human.**

Language models may generate hypothesis candidates and human-readable explanations. They are
deliberately kept off the aggregation, ranking and settlement path, which is fully deterministic
and reproducible.

## What this is not

This is a hackathon prototype, and the specification is explicit about what has **not** been proved:

- The capped stake-weighted settlement is derived from a proper scoring rule, but the full wagering mechanism is **not** claimed to be strategy-proof.
- Sybil resistance is bounded by an **attested-identity** trust model. This is not a permissionless-safe design.
- The evidence-independence discount is a **heuristic** and can miss hidden correlation.

## Status

Specification complete; implementation in progress.

Built with [GitHub Spec Kit](https://github.com/github/spec-kit). The specification is the source of truth:

- [`specs/001-stakeroute-attention-market/spec.md`](specs/001-stakeroute-attention-market/spec.md) — 5 prioritised user stories, 43 functional requirements, 12 success criteria
- [`specs/001-stakeroute-attention-market/checklists/requirements.md`](specs/001-stakeroute-attention-market/checklists/requirements.md) — spec quality validation

Implementation plan and task breakdown follow via `/speckit-plan` and `/speckit-tasks`.
