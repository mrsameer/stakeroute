"""Mechanical enforcement of Constitution Principle I.

Walks every module in ``src/stakeroute/core/`` and asserts that none of them
import storage, transport, or any ambient-input module (asyncio, datetime,
time, random). A violation here is a design defect, not a style nit — it
means the deterministic decision path has stopped being deterministic.
"""

import ast
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent.parent / "src" / "stakeroute" / "core"

FORBIDDEN_MODULES = {
    "stakeroute.storage",
    "stakeroute.transport",
    "stakeroute.model",
    "stakeroute.real",
    "stakeroute.replay",
    "asyncio",
    "datetime",
    "time",
    "random",
    "subprocess",
    "psutil",
    "google",
}


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for forbidden in FORBIDDEN_MODULES
    )


def test_core_modules_have_no_forbidden_imports() -> None:
    violations: dict[str, set[str]] = {}
    for path in sorted(CORE_DIR.glob("*.py")):
        if path.name.startswith("._"):
            # macOS AppleDouble sidecar file (seen on exFAT/network volumes),
            # not a real source module.
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        imported = _imported_names(tree)
        bad = {name for name in imported if _is_forbidden(name)}
        if bad:
            violations[path.name] = bad

    assert not violations, (
        "core modules must not import storage/transport/asyncio/datetime/"
        f"time/random — Principle I is violated by: {violations}"
    )


def test_core_dir_exists_and_is_nonempty() -> None:
    py_files = list(CORE_DIR.glob("*.py"))
    assert py_files, "expected at least one module in src/stakeroute/core/"
