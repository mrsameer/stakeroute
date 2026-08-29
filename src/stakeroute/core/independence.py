"""Evidence-independence discount (FR-015)."""

from __future__ import annotations

import math

from stakeroute.core.types import EmptyCluster


def independence_factor(cluster_size: int) -> float:
    """Return ``1 / sqrt(cluster_size)``.

    A cluster of size 1 (one genuinely independent report) carries full
    weight. As more forecasts cite the same evidence cluster, each one's
    contribution shrinks — N correlated reports contribute materially less
    than N independent ones (FR-015).

    Raises:
        EmptyCluster: if ``cluster_size`` is less than 1.
    """
    if cluster_size < 1:
        raise EmptyCluster(cluster_size)
    return 1.0 / math.sqrt(cluster_size)
