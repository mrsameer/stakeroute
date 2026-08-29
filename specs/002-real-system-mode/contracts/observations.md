# Contract: Real Observation Sources

**Constitution**: Principle III, Principle V. **Research**: D-014, D-015, D-017, D-018.

**Requirements**: FR-101 – FR-106, FR-141, FR-146 – FR-148.

Real observations reuse feature 001's `signals.raw` subject and the `events` table unchanged. What
this contract adds is where they come from, what is allowed to leave the host, and how a source
that has stopped reporting is distinguished from one with nothing to report.

---

## The four sources

| Source id | Collector | Poll | Absent when |
|---|---|---|---|
| `host.metrics` | `psutil` — CPU, memory, disk per mount, process table deltas | 2s | never (psutil is a hard dependency) |
| `app.logs` | the system's own log output | 2s | log path unwritable |
| `repo.vcs_tests` | `git log`, `git status`, most recent test-run result | 30s | `git` not on PATH, or not a repository |
| `container.events` | `docker events --since` | streaming | `docker` not on PATH or daemon unreachable |

An absent source is **recorded as absent with a reason**, never omitted (FR-141). On a developer
laptop with no Docker running, `container.events` sits at `state='absent'`,
`absent_reason='docker daemon unreachable'`, and the operator surface says so. This is the difference
between a system that has three sources and a system that has four sources one of which is down.

## Envelope

Real observations publish on `signals.raw` using feature 001's universal envelope, with
`tenant_id` set to the real tenant:

```json
{
  "tenant_id": "hostops",
  "event_id": "<sha256 hex>",
  "emitted_at_ms": 1756449998000,
  "payload": {
    "source": "host.metrics",
    "source_event_id": "disk:/System/Volumes/Data:1756449998",
    "observed_at_ms": 1756449998000,
    "provenance": {"collector": "psutil", "host_id": "<stable opaque id>", "mode": "real"},
    "payload": {"metric": "disk_used_pct", "mount": "<redacted:mount>", "value": 94.2}
  }
}
```

`event_id` uses the existing `compute_event_id(tenant_id, source, source_event_id, ts_ms)` with its
one-second bucket. No new idempotency mechanism is introduced — FR-104's out-of-order and past-dated
arrivals are handled by the same `ON CONFLICT DO NOTHING` insert that feature 001 already relies on,
and produce no duplicate economic effect (Principle III).

**`source_event_id` must be stable across polls for the same underlying observation.** For sampled
metrics it is `<metric>:<subject>:<second>`; for log lines it is a hash of the redacted line plus its
timestamp; for git it is the commit sha; for container events it is the runtime's own event id.

## Redaction (FR-146, SC-115)

Redaction runs at the ingestion boundary, before the durable write (D-014). Nothing unredacted is
stored, displayed, or transmitted.

**Step 1 — allow-list.** Only fields declared for a source survive. Anything else is dropped, and
the count of dropped fields is recorded.

| Source | Allowed fields |
|---|---|
| `host.metrics` | `metric`, `value`, `mount`, `process_name`, `pid`, `unit` |
| `app.logs` | `level`, `logger`, `message`, `exception_type` |
| `repo.vcs_tests` | `commit_sha`, `subject`, `files_changed`, `test_node_id`, `test_outcome`, `duration_ms` |
| `container.events` | `action`, `container_name`, `image`, `exit_code` |

**Step 2 — rewrite rules**, applied to every surviving string value:

| Pattern | Replacement |
|---|---|
| Absolute path containing the home directory | `<redacted:path>` |
| Any other absolute path | `<redacted:path>` with the basename kept when it carries no user data |
| The current user's account name | `<redacted:user>` |
| `KEY=value` where `KEY` matches a known env-var shape | `KEY=<redacted:env>` |
| Credential-shaped strings — long base64/hex runs, `sk-`/`ghp_`/`AIza` prefixes, PEM blocks, JWTs | `<redacted:credential>` |

Every event records which rules fired. A rule that fires on a field is not an error; a rule that
fires is the system working.

**Verification**: SC-115 scans `model_interactions.request` for all five categories across 100% of
rows. Because redaction happens before storage, the same scan over `events.payload` must also come
back clean — which is a second, independent check on the same boundary.

**Cost, stated (D-014)**: an agent sees `<redacted:path>` rather than the path that filled up. The
checkable conditions (below) operate on the host directly and are unaffected, so outcome
determination loses nothing.

## Volume policy (FR-105)

When observations arrive faster than the configured per-interval processing limit, the system
applies a **stated, visible** selection policy: retain the highest-severity observation per
`(source, subject)` within the interval, count what was set aside, and report the count on the
operator surface and in `observation_sources`.

Set-aside observations are still written to `events` — the policy limits what is *proposed over*,
not what is recorded. Nothing is silently dropped (Principle IV: suppression must be visible).

## Checkable condition registry (FR-148, D-017)

A closed registry. The model selects an entry and supplies parameters; it cannot invent a check.

| Condition | Parameters | Returns true when |
|---|---|---|
| `process_absent` | `name` | no process matching `name` is running |
| `disk_free_below` | `mount`, `pct` | free space on `mount` is below `pct` |
| `cpu_saturated` | `threshold`, `window_s` | mean CPU over `window_s` exceeds `threshold` |
| `memory_pressure` | `threshold_pct` | memory used exceeds `threshold_pct` |
| `test_failing` | `node_id` | that test node fails on a fresh run |
| `container_exited` | `name` | the named container is not running |
| `log_error_rate_above` | `logger`, `rate_per_min` | error lines exceed the rate |

Each check is a pure predicate over a freshly sampled host state — never over the recorded
observations, which is what makes it an independent verification rather than a restatement of the
evidence the agents already saw.

**Recorded on every run** (FR-148): `check_name`, `check_params`, `check_result` verbatim,
`checked_at_ms`. A hypothesis whose proposal bound no condition resolves by operator confirmation,
and its `resolutions.determination` says `'operator'` rather than `'automatic'`.

**Registry coverage is a stated limit.** A situation the registry cannot express falls back to
operator judgement. That boundary belongs in the documentation alongside the other limitations.

## Source liveness state machine (FR-141)

```
                  observation arrives
     ┌──────────────────────────────────────┐
     ▼                                      │
  ┌──────┐  poll, nothing   ┌───────┐  threshold   ┌────────┐
  │ live │ ───────────────► │ quiet │ ───────────► │ silent │
  └──────┘                  └───────┘              └────────┘
     ▲                          │                      │
     └──────────────────────────┴──────────────────────┘
                       observation arrives

  ┌────────┐   set once at startup, terminal for the run
  │ absent │   requires absent_reason
  └────────┘
```

`quiet` means the collector ran and found nothing — the host was idle, which is a legitimate and
correct state (the spec names it explicitly: a long idle stretch produces evidence too thin to
warrant any hypothesis, so the queue is correctly empty). `silent` means the collector itself has
stopped reporting. Conflating the two is exactly what FR-141 forbids.

## Tenancy (FR-147, FR-103, SC-117)

Real observations are written under `hostops`. The seeded simulation keeps `acmepay`. No query
aggregates across tenants, and `mode='real'` is set on every record in addition to the tenant
(D-018, D-019) so replay remains distinguishable from both.

The test for SC-117 asserts two things: zero real-mode rows carry the simulated tenant, and every
metrics endpoint rejects a request that names more than one tenant.
