"""Central configuration constants for StakeRoute.

All tunables live here so the mechanism, storage layer, and simulator agree on
one source of truth. Nothing here is read by ``src/stakeroute/core/`` — the
core receives every value it needs as an explicit function argument (D-001).
"""

import os

DB_PATH = os.environ.get("STAKEROUTE_DB_PATH", "data/stakeroute.db")

# Attention allocation
ATTENTION_BUDGET = 2

# Agent economics
EPOCH_GRANT = 100
STAKE_MIN = 1
STAKE_MAX = 50
REPUTATION_FLOOR = 0.1
REPUTATION_CEIL = 1.0

# Probability handling
PROBABILITY_EPSILON = 0.01

# Settlement
SETTLEMENT_SCALE = 100

# Model usage boundary (Constitution Principle I / D-010)
LLM_ENABLED = False

# Tenancy
DEFAULT_TENANT_ID = "acmepay"

# Transport selection (Phase 7). "memory" is the default and what every
# test uses — no broker required. "jetstream" is used in Docker Compose,
# where the dashboard, worker and simulator run as separate processes and
# need a real broker between them. Read once at process start; not a
# per-request toggle.
TRANSPORT_MODE = os.environ.get("STAKEROUTE_TRANSPORT", "memory")
NATS_URL = os.environ.get("STAKEROUTE_NATS_URL", "nats://localhost:4222")

# Real system mode (feature 002). "sim" reproduces feature 001 unchanged;
# "real" runs collectors and reasoning agents against the host; "replay"
# drives a scratch database from recorded inputs (D-012, D-019).
STAKEROUTE_MODE = os.environ.get("STAKEROUTE_MODE", "sim")

# Model selection: "gemini" is the live Vertex AI adapter, "none" is the
# NullModelClient (FR-126), "recorded" is the RecordedModelClient used by
# replay (D-012). Never read the credential file's contents here — only
# its path (FR-127).
STAKEROUTE_MODEL = os.environ.get("STAKEROUTE_MODEL", "none")
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

# Real tenant (D-018). The seeded simulation keeps DEFAULT_TENANT_ID/"acmepay".
REAL_TENANT_ID = "hostops"

# Model boundary (D-011, D-020, contracts/model-boundary.md)
MODEL_TIMEOUT_S = 10.0
MODEL_CEILING_CALLS_PER_HOUR = int(
    os.environ.get("STAKEROUTE_MODEL_CEILING_CALLS_PER_HOUR", "60")
)

# Calibration insufficiency (D-022)
MIN_RESOLVED_FOR_CALIBRATION = 10

# Collectors and proposal cadence (D-015, contracts/observations.md)
COLLECTOR_POLL_INTERVAL_S = 2.0
REPO_POLL_INTERVAL_S = 30.0  # git log/status and test results poll slower
SOURCE_SILENCE_THRESHOLD_MS = 30_000
PROPOSAL_INTERVAL_S = 30.0
OBSERVATIONS_PER_INTERVAL_LIMIT = 50

# Gemini adapter (D-011) — only consulted when STAKEROUTE_MODEL=gemini
GEMINI_MODEL_NAME = os.environ.get("STAKEROUTE_GEMINI_MODEL", "gemini-2.0-flash-001")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# Duplicate detection (D-023)
DUPLICATE_JACCARD_THRESHOLD = 0.6
DUPLICATE_WINDOW_MS = 5 * 60 * 1000  # five minutes

# Reputation decay (D-021)
REPUTATION_HALF_LIFE_MS = 7 * 24 * 60 * 60 * 1000  # one week

# Real-mode hypothesis review deadline (FR-107). Independent of feature
# 001's scenario-generated deadlines, which stay simulator-only.
REAL_HYPOTHESIS_DEADLINE_MS = 60 * 60 * 1000  # one hour
