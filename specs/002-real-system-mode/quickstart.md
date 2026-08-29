# Quickstart & Validation: Real System Mode

**Feature**: 002-real-system-mode

The validation guide for real mode. Each scenario maps to a success criterion in
[spec.md](./spec.md) and is the acceptance evidence for it. Feature 001's guide still applies
unchanged for the seeded simulation — V0 below is what proves that.

## Prerequisites

- Everything from feature 001: Python 3.12, `uv`, Docker with Compose v2
- `vertex-ai-credentials.json` present in the project root (untracked, already git-ignored)
- Optional: a Docker daemon. Without one, `container.events` reports `absent` — which is itself a
  validation case (V4).

## Setup

```bash
uv sync
```

Real mode reads credentials from the environment, never from the source tree (FR-127):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$PWD/vertex-ai-credentials.json"
```

## Fast loop — mechanism only, no model, no infrastructure

The new pure modules (`core/estimates.py`, `core/decay.py`, `core/duplicates.py`) have no
transport, storage or model dependency, so they run with nothing else up.

```bash
uv run pytest tests/unit -q
```

```bash
uv run ruff format . && uv run ruff check . && uv run pyright
```

---

## Validation scenarios

### V0 — Feature 001 is untouched (SC-108, FR-132)

Run first, every time. This feature is only correct if the previous one still is.

```bash
uv run pytest tests -q
```

**Expect**: every feature-001 test passes unchanged. Then run the seeded scenario and confirm it
produces the results recorded in `specs/001-stakeroute-attention-market/run-log.md`.

**Fails if**: `mode` defaulted to anything but `'sim'`, or `update_reputation` was modified rather
than extended (D-021).

---

### V1 — Starts with no model at all (SC-113, FR-126)

```bash
STAKEROUTE_MODEL=none uv run python -m stakeroute.real.run_ingestor
```

**Expect**: collectors run, observations are ingested, ranking passes complete, settlement works,
and `GET /api/mode` reports `model.state = "unconfigured"` with
`unavailable_capabilities: ["hypothesis_proposal", "prose_explanation"]`.

**Expect specifically**: the queue endpoint returns a well-formed response. An empty queue here is
correct — no proposals without a model — but the mechanism must be fully alive.

This runs before any model is configured. If it does not pass, the model is load-bearing on the
decision path and Principle I is violated.

---

### V2 — Real observations reach the queue (SC-103, FR-101, FR-107)

Start the full stack in real mode, leave it running, then induce a fault by hand:

```bash
uv run pytest tests/integration/test_expiry.py  # after breaking one assertion locally
```

**Expect**: within one proposal interval, `GET /api/queue?tenant=hostops` shows a hypothesis whose
`statement` appears nowhere in the codebase or configuration, with
`cited_observation_count > 0` and a bound `condition`.

**Verify the trace** (FR-140):

```bash
curl "localhost:8000/api/hypotheses/<id>/trace?tenant=hostops"
```

Every observation listed must be a real `events` row with real provenance and timestamps.

**Grep test**: the returned `statement` must not be findable in the repository.

```bash
git grep -F "<the statement text>" -- src/ || echo "not in codebase — correct"
```

---

### V3 — Agents reason and can be wrong (SC-101, SC-102, FR-112, FR-113)

```bash
uv run pytest tests/integration/test_evidence_scope.py -q
```

**Expect**:

- No recorded `forecasts.evidence_bundle` in real mode contains an outcome, ground-truth label or
  accuracy field — across 100% of rows (SC-102).
- Each agent's rationale references only sources inside its declared scope; a violation is recorded
  in `rejected_forecasts` rather than accepted (FR-116).
- After several resolutions, `GET /api/agents?tenant=hostops` shows `measured_calibration` values
  that differ across the population and no `accuracy` field anywhere (FR-117).

**The negative check that matters**: grep the real-mode path for any configured accuracy constant.
`simulator/agents.py::AgentProfile.accuracy` may exist; nothing under `src/stakeroute/real/` may.

---

### V4 — Source liveness distinguishes silence from quiet (SC-110, FR-141)

```bash
curl "localhost:8000/api/mode"
```

**Expect** four sources, each with a state. On a host with no Docker running,
`container.events` is `absent` with a populated `absent_reason` — not missing from the list.

Now stop a collector and wait past its silence threshold.

**Expect**: that source moves `live → quiet → silent`, and the operator surface shows `silent`
distinctly from `quiet`. A source with nothing to report and a source that has died must never
render the same.

---

### V5 — The market runs when the model does not (SC-104, SC-105, FR-120, FR-121)

With hypotheses open and forecasts in flight, make the model unreachable — block egress, or set an
unroutable endpoint.

**Expect**:

- Ranking passes keep completing, with results identical to those produced with the model available.
- Resolved outcomes keep settling; credits and reputation move normally.
- No decision-path operation slows down at all — not by the timeout, not by anything. The worker
  holds no model client (contracts/model-boundary.md), so there is nothing for it to wait on.
- `GET /api/mode` reports `degraded` and names `hypothesis_proposal` as unavailable.

```bash
uv run pytest tests/integration/test_model_absent.py -q
```

**Expect additionally**: malformed responses, out-of-range probabilities and refusals each produce a
`model_interactions` row with `accepted = 0` and **zero** downstream forecasts, hypotheses or
economic effects (SC-106).

---

### V6 — Nothing sensitive leaves the host (SC-115, FR-146)

```bash
uv run pytest tests/integration/test_redaction.py -q
```

**Expect**: zero absolute filesystem paths, user account names, environment variable values or
credential-shaped strings across 100% of `model_interactions.request` rows.

Because redaction happens at ingestion (D-014), the same scan over `events.payload` must also come
back clean. Run both — one boundary, two independent checks.

```bash
uv run pytest tests/integration/test_no_credentials_recorded.py -q
```

**Expect**: credentials in zero committed files, zero log lines, zero recorded interactions, zero
operator-facing surfaces (SC-112).

---

### V7 — A real run replays exactly (SC-107, FR-129 – FR-131)

```bash
curl -X POST localhost:8000/api/replay \
  -d '{"window_start_ms": <start>, "window_end_ms": <end>}'
```

**Expect**: `identical: true`, `model_requests_made: 0`, and a non-zero `records_compared`.

**Then prove it can fail** (FR-131): alter one recorded input in a copy of the database and replay
again.

**Expect**: `identical: false` with `first_divergence` naming the record, field, expected and actual
values. A replay that silently absorbs an altered input is a defect, not a pass — this negative case
is the one that makes the positive case mean anything.

---

### V8 — Real and simulated never mix (SC-117, FR-147, FR-103)

```bash
uv run pytest tests/integration/test_tenant_separation.py -q
```

**Expect**: zero real-mode rows carry the `acmepay` tenant, zero simulated rows carry `hostops`, and
every metrics response names exactly one tenant and one `provenance` value.

```bash
curl "localhost:8000/api/metrics?tenant=hostops"
```

**Expect** early in a real run: `insufficient: true` with `measured_over_outcomes` below
`minimum_for_calibration`, and `null` for every calibration figure — never a zero, never a
placeholder (SC-114, FR-118).

---

### V9 — End to end, unscripted (SC-116)

The demonstration. Nothing below is configured in advance.

1. Leave the system running in real mode against the host.
2. Induce a fault live: fill a volume, saturate a core, kill a process, or break a test.
3. Watch a hypothesis reach the operator queue, proposed from the observations that fault produced.
4. Confirm agents holding **disjoint** evidence scopes forecast on it, with differing probabilities
   and rationales that cite only what each could see.
5. Let it resolve by automatic re-check of the bound condition.
6. Open the trace and walk every step back to recorded data.

**Expect**: every step traceable to a recorded row, and no step findable in configuration. If any
part of this was scripted, the feature has not been delivered.

---

## Constitutional gate — cleared

This feature contradicted two Additional Constraints under constitution v1.0.0: "Simulation only:
No live third-party integrations", and the Reproducibility constraint's assumption that
reproducible means regenerable.

Both were amended in **v1.1.0** before implementation, via `/speckit-constitution`. See
[plan.md](./plan.md) for what changed and why. Two obligations from that amendment are validated
above and are not optional:

- **V5** proves the decision path survives the model's absence — the amended External
  dependencies constraint permits a live integration *only* on that condition.
- **V7** proves the *replayable* guarantee, including the negative case where an altered input
  must diverge detectably rather than be absorbed.
