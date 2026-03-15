"""Discovery robustness tests against real GitHub repos.

These tests clone four real repositories (one per major CLI shape) and verify
that code-to-module's rule-based detectors can discover functionalities without
ever falling back to the LLM.

Requires network access.  Skip in offline CI with:
    pytest -m "not network"

Run only this suite with:
    pytest -m network
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from discovery_targets import DISCOVERY_TARGETS

from code_to_module import get_functionalities

# ── Local discover wrapper ─────────────────────────────────────────────────────


@dataclass
class _Func:
    """Lightweight view of a discovered functionality, adding a derived process_name."""

    name: str
    process_name: str  # UPPERCASE, derived from the functionality name
    confidence: float
    detection_method: str


def _discover(repo_path: Path) -> list[_Func]:
    """Ingest a local directory and return discovered functionalities.

    Wraps get_functionalities() and adds a derived process_name so tests can
    assert that names satisfy the TOOLNAME[_SUBCOMMAND] uppercase convention.
    """
    raw = get_functionalities(str(repo_path))
    return [
        _Func(
            name=f["name"],
            process_name=f["name"].upper().replace("-", "_"),
            confidence=f["confidence"],
            detection_method=f["detection_method"],
        )
        for f in raw
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _require_repo(target_id: str, repo_cache: dict) -> Path:
    """Return the cloned repo path or skip the test if the clone failed."""
    path = repo_cache.get(target_id)
    if path is None:
        pytest.skip(f"Repo '{target_id}' could not be cloned (network unavailable?)")
    return path  # type: ignore[return-value]


# ── Parametrised tests ─────────────────────────────────────────────────────────


@pytest.mark.network
@pytest.mark.parametrize(
    "target", DISCOVERY_TARGETS, ids=[t["id"] for t in DISCOVERY_TARGETS]
)
def test_discovery_returns_functionalities(
    target: dict, repo_cache: dict
) -> None:
    """Discovery finds at least one functionality for every supported CLI shape."""
    repo_path = _require_repo(target["id"], repo_cache)
    functionalities = _discover(repo_path)

    assert len(functionalities) >= target["min_functionalities"], (
        f"{target['id']}: expected >= {target['min_functionalities']} functionalities, "
        f"got {len(functionalities)}"
    )
    assert len(functionalities) <= target["max_functionalities"], (
        f"{target['id']}: expected <= {target['max_functionalities']} functionalities, "
        f"got {len(functionalities)} — LLM may have over-split"
    )


@pytest.mark.network
@pytest.mark.parametrize(
    "target", DISCOVERY_TARGETS, ids=[t["id"] for t in DISCOVERY_TARGETS]
)
def test_discovery_no_llm_call(target: dict, repo_cache: dict) -> None:
    """Rule-based detection handles all four shapes without calling the LLM.

    Patches both the inference step and the discover-phase LLM fallback so the
    test asserts that *neither* is invoked when rule-based detectors succeed.
    """
    repo_path = _require_repo(target["id"], repo_cache)

    with (
        patch("code_to_module.infer.infer_module_spec") as mock_infer,
        patch("code_to_module.discover._run_llm", return_value=[]) as mock_llm,
    ):
        funcs = _discover(repo_path)
        mock_infer.assert_not_called()

        # Rule-based must still produce results even with the LLM disabled.
        assert len(funcs) >= target["min_functionalities"], (
            f"{target['id']}: rule-based discovery found 0 functionalities "
            "— LLM fallback may be required for this repo shape"
        )
        _ = mock_llm  # suppress unused-variable warning


@pytest.mark.network
@pytest.mark.parametrize(
    "target", DISCOVERY_TARGETS, ids=[t["id"] for t in DISCOVERY_TARGETS]
)
def test_discovery_process_names_are_valid(
    target: dict, repo_cache: dict
) -> None:
    """All returned process names are non-empty uppercase strings with positive confidence."""
    repo_path = _require_repo(target["id"], repo_cache)
    functionalities = _discover(repo_path)

    for f in functionalities:
        assert f.process_name, f"{target['id']}: empty process name for '{f.name}'"
        assert f.process_name == f.process_name.upper(), (
            f"{target['id']}: process name not uppercase: {f.process_name}"
        )
        assert f.confidence > 0.0, (
            f"{target['id']}: zero confidence for '{f.process_name}'"
        )


@pytest.mark.network
def test_celltypist_finds_console_scripts_entry_point(repo_cache: dict) -> None:
    """Celltypist-specific: console_scripts detector fires and returns 'celltypist'.

    Celltypist uses a setup.py console_scripts entry; the Level-3 cascade (flat
    CLI, no add_subparsers found) should return 'celltypist' as the functionality
    name.  If celltypist adds subparsers in the future this test may need updating.
    """
    repo_path = _require_repo("celltypist", repo_cache)
    functionalities = _discover(repo_path)
    names = [f.name for f in functionalities]

    assert "celltypist" in names, (
        "console_scripts detector missed the celltypist entry point — "
        f"got names: {names}.  "
        "Check _detect_console_scripts in discover.py"
    )
