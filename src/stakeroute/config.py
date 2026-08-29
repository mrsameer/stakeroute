"""Central configuration constants for StakeRoute.

All tunables live here so the mechanism, storage layer, and simulator agree on
one source of truth. Nothing here is read by ``src/stakeroute/core/`` — the
core receives every value it needs as an explicit function argument (D-001).
"""

DB_PATH = "data/stakeroute.db"

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
