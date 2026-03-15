"""Shared pytest fixtures for the code-to-module test suite."""

from __future__ import annotations

import subprocess

import pytest
from discovery_targets import DISCOVERY_TARGETS


@pytest.fixture(scope="session")
def repo_cache(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Clone each DISCOVERY_TARGETS repo once per test session into a shared dir.

    Skips individual repos that fail to clone (network unavailable, rate-limited,
    etc.) rather than aborting the entire session.
    """
    from pathlib import Path

    cache: dict[str, Path] = {}
    base = tmp_path_factory.mktemp("repos")
    for target in DISCOVERY_TARGETS:
        dest = base / target["id"]
        result = subprocess.run(
            ["git", "clone", "--depth=1", target["url"], str(dest)],
            capture_output=True,
        )
        if result.returncode == 0:
            cache[target["id"]] = dest
        else:
            # Leave target absent from cache — tests will skip via pytest.skip()
            pass
    return cache  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _clear_module_caches() -> None:
    """Reset all module-level HTTP-result caches before every test.

    Prevents a cached 200-OK (or 404) from one test leaking into a subsequent
    test that expects a different HTTP response for the same tool name.
    Clears caches in container.py and assess.py.
    """
    import code_to_module.assess as _a
    import code_to_module.container as _c

    _c._biocontainers_cache.clear()
    _a._bioconda_cache.clear()
    _a._biotools_cache.clear()
