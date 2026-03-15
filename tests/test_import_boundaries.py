"""Import boundary enforcement tests.

Verifies that the validation suite (validate_cli, validate, fix, review, standards/)
never imports from the conversion pipeline (ingest, discover, assess, infer,
container, generate).  Uses the AST module — no subprocess calls, no imports
of the modules under test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ── Constants ──────────────────────────────────────────────────────────────────

_SRC = Path("src/code_to_module")

PROTECTED_FILES: list[Path] = [
    _SRC / "validate_cli.py",
    _SRC / "validate.py",
    _SRC / "fix.py",
    _SRC / "review.py",
    *sorted((_SRC / "standards").rglob("*.py")),
]

CONVERSION_PIPELINE_MODULES: frozenset[str] = frozenset({
    "code_to_module.ingest",
    "code_to_module.discover",
    "code_to_module.assess",
    "code_to_module.infer",
    "code_to_module.container",
    "code_to_module.generate",
})


# ── Helpers ────────────────────────────────────────────────────────────────────


def get_imports(path: Path) -> set[str]:
    """Return all module names imported by the file at path."""
    tree = ast.parse(path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "protected_file",
    PROTECTED_FILES,
    ids=[str(p.relative_to(_SRC)) for p in PROTECTED_FILES],
)
def test_no_conversion_pipeline_imports(protected_file: Path) -> None:
    imports = get_imports(protected_file)
    violations = imports & CONVERSION_PIPELINE_MODULES
    assert not violations, (
        f"{protected_file.name} imports from conversion pipeline: {violations}\n"
        "These modules must remain independent of the conversion pipeline."
    )


def test_standards_subpackage_is_self_contained() -> None:
    """standards/ must not import from any other code_to_module submodule
    except other standards/ files. It is a read-only data layer."""
    standards_files = list((_SRC / "standards").rglob("*.py"))
    allowed_prefix = "code_to_module.standards"
    for f in standards_files:
        imports = get_imports(f)
        violations = {
            imp for imp in imports
            if imp.startswith("code_to_module") and not imp.startswith(allowed_prefix)
        }
        assert not violations, (
            f"{f.name} imports from outside standards/: {violations}"
        )
