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
deliberately kept off the aggregation, ranking and settlement path — and in this build there is no
model call anywhere in that path to begin with, not merely a flag left off (`LLM_ENABLED = False`
by default; `src/stakeroute/core/` and `src/stakeroute/worker/` contain zero references to any
model API).

## Running it

**Fast loop — the mechanism, no infrastructure** (this is the loop used while building):

```bash
uv sync
uv run pytest -q                                    # 53 tests, no broker required
uv run ruff format . && uv run ruff check . && uv run pyright
```

**Interactive demo — one process, in-memory transport:**

```bash
uv run uvicorn stakeroute.dashboard.main:app --reload
```

Open `http://localhost:8000` — a single page with the ranked queue, the three-strategy
comparison panel, the agent table, and a metrics strip. Click a queue row to see its full
per-agent traceability. Buttons drive the same scenario endpoints the API contract defines:
run the baseline, inject 50 Sybils, inject correlated evidence, resolve an outcome.

**Full stack — real durability, three separate processes, a real broker:**

```bash
docker compose up --build     # nats + worker + dashboard + simulator
docker compose kill worker    # the failure-recovery demonstration (SC-005)
docker compose start worker   # recovery — the worker resumes, it does not reseed
```

## What actually happened when this was run

Recorded, not asserted — see
[`specs/001-stakeroute-attention-market/run-log.md`](specs/001-stakeroute-attention-market/run-log.md)
for the full transcript. Highlights:

- **Baseline routing**: 5 candidate hypotheses, budget of 2 → exactly 2 routed, 3 withheld, the
  genuine payment incident at rank 1.
- **Sybil attack**: after 50 new agents back the false hypothesis, majority vote flips to rank it
  first (91%); StakeRoute holds the true incident at rank 1.
- **Correlated evidence**: 20 additional forecasts citing one already-counted source move the
  aggregate by 1.23 percentage points — inside the 5pp bound.
- **Worker kill and recovery** (real NATS JetStream, real separate OS processes): killed mid-stream,
  simulator kept publishing, worker restarted and resumed (not reseeded) — zero lost events, zero
  duplicate settlements, verified by direct SQL query against the ledger.
- **Settlement**: agents that beat the prior gained reputation and credits; a confidently-wrong
  agent lost 18 credits and floored out at 0.1 reputation; no agent's loss ever exceeded its stake.
- **Reproducibility**: the identical seed produces byte-identical rankings on a second run.
- **Metrics**: every ranking-quality metric reads `null` — not a placeholder zero — until ground
  truth exists, then populates from real recorded rows.

## What this is not

This is a hackathon prototype, and the specification is explicit about what has **not** been proved:

- The capped stake-weighted settlement is derived from a proper scoring rule, but the full wagering mechanism is **not** claimed to be strategy-proof.
- Sybil resistance is bounded by an **attested-identity** trust model. This is not a permissionless-safe design.
- The evidence-independence discount is a **heuristic** and can miss hidden correlation.
- SQLite serialises writers. The durable bus (NATS JetStream) already supports multiple workers
  sharing a consumer, and the ledger's uniqueness constraints already make that safe — but the
  demo store is SQLite, so the demo runs a single writer process by design. Moving to Postgres is
  the change that would make true multi-worker scale-out real, and it is not done here.
- This is a confirmed single-contributor build (see
  [`plan.md`](specs/001-stakeroute-attention-market/plan.md#team-allocation-and-adversarial-review)):
  the adversarial reviewer and the implementer are the same person, which is materially weaker
  than an independent attacker. Independence is approximated by mechanisation — the attack tests
  were written before the mechanism they attack was tuned — not by a second set of eyes.
