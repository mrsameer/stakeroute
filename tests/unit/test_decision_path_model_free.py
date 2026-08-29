"""Mechanical enforcement that the decision path holds no model reference
(FR-119, FR-128, Principle I).

A ``run_ranking_pass`` or ``settle_hypothesis`` that imported
``stakeroute.model`` could hold a client and wait on it — this test makes
that structurally impossible to introduce unnoticed, the same way
``test_core_purity.py`` guards ``stakeroute.core``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

WORKER_DIR = Path(__file__).parent.parent.parent / "src" / "stakeroute" / "worker"

FORBIDDEN_MODULES = {"stakeroute.model"}


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for forbidden in FORBIDDEN_MODULES
    )


def test_worker_modules_do_not_import_stakeroute_model() -> None:
    violations: dict[str, set[str]] = {}
    for path in sorted(WORKER_DIR.glob("*.py")):
        if path.name.startswith("._"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        imported = _imported_names(tree)
        bad = {name for name in imported if _is_forbidden(name)}
        if bad:
            violations[path.name] = bad
    assert not violations, (
        "worker modules must not import stakeroute.model — the decision "
        f"path must survive the model's absence: {violations}"
    )


def test_run_ranking_pass_signature_holds_no_model_client() -> None:
    from stakeroute.worker.pipeline import run_ranking_pass

    params = inspect.signature(run_ranking_pass).parameters
    for name in params:
        assert "model" not in name.lower(), (
            f"run_ranking_pass has a parameter named {name!r} — the "
            "ranking pass must never take a ModelClient"
        )


def test_settle_hypothesis_signature_holds_no_model_client() -> None:
    from stakeroute.worker.settlement_runner import settle_hypothesis

    params = inspect.signature(settle_hypothesis).parameters
    for name in params:
        assert "model" not in name.lower(), (
            f"settle_hypothesis has a parameter named {name!r} — "
            "settlement must never take a ModelClient"
        )
