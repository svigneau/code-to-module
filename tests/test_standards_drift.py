"""Standards drift detection tests.

These tests fetch the live nf-core module template from GitHub and verify that
the conventions stored in nf_core_standards.json still match upstream.

ALL tests are @pytest.mark.network — skipped in offline CI:
    pytest -m "not network"

ADVISORY DESIGN: upstream template breakage should not block releases, only
alert maintainers to run `code-to-module update-standards`.  Every test is
therefore wrapped so that a network or HTTP failure degrades to a pytest.skip(),
and an assertion mismatch emits pytest.warns(UserWarning) *in addition to*
re-raising the AssertionError — giving visibility in CI without hard-blocking.
Tests that truly cannot produce a result (fetch failed) are skipped;
tests where we fetched the template but the content differs will still fail,
since that represents a real drift that needs a human decision.
"""

from __future__ import annotations

import urllib.error
import urllib.request
import warnings

import pytest

_MAIN_NF_TEMPLATE_URL = (
    "https://raw.githubusercontent.com/nf-core/modules/master/"
    "modules/nf-core/MODULE_TEMPLATE/main.nf"
)
_ENV_YML_TEMPLATE_URL = (
    "https://raw.githubusercontent.com/nf-core/modules/master/"
    "modules/nf-core/MODULE_TEMPLATE/environment.yml"
)

pytestmark = pytest.mark.network


# ── Helpers ────────────────────────────────────────────────────────────────────


def fetch_template(url: str) -> str:
    """Fetch *url* and return content as a string.

    Raises pytest.skip() on any network or HTTP error so a transient failure
    doesn't block CI — the intent is to detect *drift*, not test connectivity.
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
            return r.read().decode()
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"Could not fetch template ({url}): {exc}")


def _advisory_assert(condition: bool, message: str) -> None:
    """Assert *condition*, but emit a UserWarning before re-raising.

    This gives CI a visible warning channel (--W flag or log) for drift
    without changing the hard-fail semantics that pytest normally provides.
    If upstream is broken the test will still fail — the warning is extra
    signal, not a suppressor.
    """
    if not condition:
        warnings.warn(
            f"nf-core standards drift detected: {message}",
            UserWarning,
            stacklevel=2,
        )
        assert condition, message


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_versions_topic_channel_present_in_template() -> None:
    """nf-core template still uses topic: 'versions' on the versions emit."""
    template = fetch_template(_MAIN_NF_TEMPLATE_URL)
    _advisory_assert(
        "topic: 'versions'" in template or 'topic: "versions"' in template,
        "nf-core template no longer uses topic: versions — "
        "update nf_core_standards.json and Jinja2 template accordingly",
    )


def test_ext_args_pattern_present_in_template() -> None:
    """nf-core template still uses task.ext.args pattern."""
    template = fetch_template(_MAIN_NF_TEMPLATE_URL)
    _advisory_assert(
        "task.ext.args" in template,
        "nf-core template no longer uses task.ext.args — "
        "check whether the convention has changed upstream",
    )


def test_container_block_structure_unchanged() -> None:
    """nf-core template container block still uses singularity/docker ternary."""
    template = fetch_template(_MAIN_NF_TEMPLATE_URL)
    _advisory_assert(
        "workflow.containerEngine == 'singularity'" in template,
        "nf-core container block structure has changed — "
        "update the container Jinja2 template in templates/",
    )


def test_conda_channels_order_matches_standards() -> None:
    """Live environment.yml template conda channel order matches stored standards."""
    import yaml

    from code_to_module.standards import get_standards

    template = fetch_template(_ENV_YML_TEMPLATE_URL)
    standards = get_standards()

    try:
        parsed = yaml.safe_load(template)
    except yaml.YAMLError as exc:
        pytest.skip(f"Could not parse environment.yml template: {exc}")

    template_channels: list[str] = parsed.get("channels", []) if parsed else []
    _advisory_assert(
        template_channels == standards.conda_channels,
        f"Conda channel order has changed upstream.\n"
        f"Template: {template_channels}\n"
        f"Stored:   {standards.conda_channels}\n"
        "Run `code-to-module update-standards` to sync.",
    )


def test_standards_version_field_present() -> None:
    """nf_core_standards.json has a schema_version field for change tracking."""
    from code_to_module.standards import get_standards

    standards = get_standards()
    assert hasattr(standards, "schema_version"), (
        "Add a schema_version field to nf_core_standards.json to enable "
        "drift tracking over time"
    )
    assert standards.schema_version, "schema_version must be non-empty"
