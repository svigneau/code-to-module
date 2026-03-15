"""Tests for container.select() — Phase 2 container selection.

container.py has no implementation yet.  Tests are skipped until select() is
defined and will FAIL (not error/pass) against a stub that returns wrong values.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from rich.console import Console

# ── Conditional import ────────────────────────────────────────────────────────
_container_mod = pytest.importorskip("code_to_module.container")
_select = getattr(_container_mod, "select", None)
if _select is None:
    pytest.skip("container.select not yet implemented", allow_module_level=True)

select = _select  # type: ignore[assignment]

from code_to_module.models import (  # noqa: E402
    ContainerDiscovery,
    ContainerOption,
    ContainerSource,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_option(
    source: ContainerSource,
    label: str | None = None,
    is_default: bool = False,
    warnings: list[str] | None = None,
    dockerfile_content: str | None = None,
) -> ContainerOption:
    _url_map: dict[ContainerSource, tuple[str, str]] = {
        ContainerSource.BIOCONTAINERS: (
            "quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0",
            "https://depot.galaxyproject.org/singularity/mytool:1.0.0--pyhdfd78af_0",
        ),
        ContainerSource.DOCKERFILE: (
            "ghcr.io/myorg/mytool:latest",
            "docker://ghcr.io/myorg/mytool:latest",
        ),
        ContainerSource.GENERATED_FROM_ENVYML: (
            "mytool-conda:latest",
            "docker://mytool-conda:latest",
        ),
        ContainerSource.GENERATED_FROM_REQS: (
            "mytool-pip:latest",
            "docker://mytool-pip:latest",
        ),
        ContainerSource.CONVERTED_FROM_SINGULARITY: (
            "mytool-converted:latest",
            "docker://mytool-converted:latest",
        ),
        ContainerSource.STUB: (
            "TODO: build and push your image to a registry",
            "TODO: build and push your image to a registry",
        ),
    }
    docker_url, singularity_url = _url_map.get(source, ("TODO", "TODO"))
    return ContainerOption(
        source=source,
        label=label or source.value,
        docker_url=docker_url,
        singularity_url=singularity_url,
        dockerfile_content=dockerfile_content,
        warnings=warnings or [],
        is_default=is_default,
    )


def make_discovery(sources: list[ContainerSource]) -> ContainerDiscovery:
    """Build a ContainerDiscovery from a list of ContainerSources.

    The first source gets is_default=True; the rest are non-default.
    """
    options = [_make_option(src, is_default=(i == 0)) for i, src in enumerate(sources)]
    return ContainerDiscovery(options=options)


def _capture_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, highlight=False)
    return console, buf


# ── Flag tests ─────────────────────────────────────────────────────────────────


def test_flag_dockerfile_returns_dockerfile() -> None:
    """container_flag='dockerfile' → returns the DOCKERFILE option."""
    discovery = make_discovery(
        [ContainerSource.DOCKERFILE, ContainerSource.BIOCONTAINERS, ContainerSource.STUB]
    )
    console, _ = _capture_console()
    result = select(discovery, container_flag="dockerfile", no_interaction=True, console=console)
    assert result.source == ContainerSource.DOCKERFILE


def test_flag_biocontainers_returns_biocontainers() -> None:
    """container_flag='biocontainers' → returns the BIOCONTAINERS option."""
    discovery = make_discovery(
        [ContainerSource.DOCKERFILE, ContainerSource.BIOCONTAINERS, ContainerSource.STUB]
    )
    console, _ = _capture_console()
    result = select(discovery, container_flag="biocontainers", no_interaction=True, console=console)
    assert result.source == ContainerSource.BIOCONTAINERS


def test_flag_generate_returns_envyml_when_available() -> None:
    """container_flag='generate' returns GENERATED_FROM_ENVYML when present."""
    discovery = make_discovery([ContainerSource.GENERATED_FROM_ENVYML, ContainerSource.STUB])
    console, _ = _capture_console()
    result = select(discovery, container_flag="generate", no_interaction=True, console=console)
    assert result.source == ContainerSource.GENERATED_FROM_ENVYML


def test_flag_generate_falls_back_to_reqs() -> None:
    """container_flag='generate' falls back to GENERATED_FROM_REQS when no envyml."""
    discovery = make_discovery([ContainerSource.GENERATED_FROM_REQS, ContainerSource.STUB])
    console, _ = _capture_console()
    result = select(discovery, container_flag="generate", no_interaction=True, console=console)
    assert result.source == ContainerSource.GENERATED_FROM_REQS


def test_flag_stub_returns_stub() -> None:
    """container_flag='stub' → returns STUB regardless of other options."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, _ = _capture_console()
    result = select(discovery, container_flag="stub", no_interaction=True, console=console)
    assert result.source == ContainerSource.STUB


def test_flag_missing_option_falls_through_to_default() -> None:
    """Flag requests DOCKERFILE but it's absent → warning printed + default returned."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, buf = _capture_console()

    result = select(discovery, container_flag="dockerfile", no_interaction=True, console=console)

    # Falls back to the default (BIOCONTAINERS at index 0)
    assert result.source == ContainerSource.BIOCONTAINERS
    # A warning must be printed
    assert buf.getvalue().strip() != ""


def test_flag_missing_option_warning_lists_available() -> None:
    """Warning message when requested flag is missing lists available option names."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, buf = _capture_console()

    select(discovery, container_flag="dockerfile", no_interaction=True, console=console)

    output = buf.getvalue().lower()
    assert "biocontainers" in output
    assert "stub" in output


# ── Non-interactive tests ──────────────────────────────────────────────────────


def test_no_interaction_returns_default_option() -> None:
    """no_interaction=True → returns the default option (is_default=True)."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, _ = _capture_console()

    result = select(discovery, container_flag=None, no_interaction=True, console=console)

    assert result.source == ContainerSource.BIOCONTAINERS


def test_no_interaction_prints_single_line() -> None:
    """no_interaction=True → at least one output line naming the selected option."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, buf = _capture_console()

    select(discovery, container_flag=None, no_interaction=True, console=console)

    non_blank = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(non_blank) >= 1
    assert any("biocontainers" in ln.lower() for ln in non_blank)


def test_non_tty_returns_default() -> None:
    """Non-TTY stdin → returns default option without prompting."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, _ = _capture_console()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result = select(discovery, container_flag=None, no_interaction=False, console=console)

    assert result.source == ContainerSource.BIOCONTAINERS


def test_non_tty_same_as_no_interaction() -> None:
    """Non-TTY and no_interaction=True both return the same default."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])

    console1, _ = _capture_console()
    result_no_interaction = select(
        discovery, container_flag=None, no_interaction=True, console=console1
    )

    console2, _ = _capture_console()
    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result_non_tty = select(
            discovery, container_flag=None, no_interaction=False, console=console2
        )

    assert result_no_interaction.source == result_non_tty.source


# ── Interactive tests ──────────────────────────────────────────────────────────


def test_interactive_valid_choice() -> None:
    """Interactive mode: input '2' → second option returned."""
    discovery = make_discovery(
        [ContainerSource.DOCKERFILE, ContainerSource.BIOCONTAINERS, ContainerSource.STUB]
    )
    console, _ = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="2"):
        mock_stdin.isatty.return_value = True
        result = select(discovery, container_flag=None, no_interaction=False, console=console)

    assert result.source == ContainerSource.BIOCONTAINERS


def test_interactive_valid_choice_first() -> None:
    """Interactive mode: input '1' → first option returned."""
    discovery = make_discovery(
        [ContainerSource.DOCKERFILE, ContainerSource.BIOCONTAINERS, ContainerSource.STUB]
    )
    console, _ = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="1"):
        mock_stdin.isatty.return_value = True
        result = select(discovery, container_flag=None, no_interaction=False, console=console)

    assert result.source == ContainerSource.DOCKERFILE


def test_interactive_empty_input_uses_default() -> None:
    """Interactive mode: empty input '' → default option returned."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, _ = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""):
        mock_stdin.isatty.return_value = True
        result = select(discovery, container_flag=None, no_interaction=False, console=console)

    assert result.source == ContainerSource.BIOCONTAINERS


def test_interactive_invalid_input_reprompts() -> None:
    """Interactive mode: invalid input '99' then '1' → re-prompted; first option returned."""
    discovery = make_discovery(
        [ContainerSource.DOCKERFILE, ContainerSource.BIOCONTAINERS, ContainerSource.STUB]
    )
    console, _ = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", side_effect=["99", "1"]):
        mock_stdin.isatty.return_value = True
        result = select(discovery, container_flag=None, no_interaction=False, console=console)

    assert result.source == ContainerSource.DOCKERFILE


def test_interactive_invalid_twice_uses_default() -> None:
    """Interactive mode: two invalid inputs → default returned after giving up."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, _ = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", side_effect=["99", "99"]):
        mock_stdin.isatty.return_value = True
        result = select(discovery, container_flag=None, no_interaction=False, console=console)

    assert result.source == ContainerSource.BIOCONTAINERS


def test_single_stub_only_skips_menu() -> None:
    """Only STUB available → no interactive menu shown; STUB returned."""
    discovery = make_discovery([ContainerSource.STUB])
    console, buf = _capture_console()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        # input() must NOT be called — if it is, StopIteration surfaces as an error
        with patch("builtins.input", side_effect=StopIteration("menu shown unexpectedly")):
            result = select(
                discovery, container_flag=None, no_interaction=False, console=console
            )

    assert result.source == ContainerSource.STUB


def test_interactive_shows_star_for_default() -> None:
    """Interactive menu output contains a star marker ('★' or '*') for the default option."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, buf = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""):
        mock_stdin.isatty.return_value = True
        select(discovery, container_flag=None, no_interaction=False, console=console)

    output = buf.getvalue()
    assert "★" in output or "*" in output


def test_interactive_shows_docker_url() -> None:
    """Interactive menu output contains (part of) the docker URL."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, buf = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""):
        mock_stdin.isatty.return_value = True
        select(discovery, container_flag=None, no_interaction=False, console=console)

    output = buf.getvalue()
    bio_url = discovery.options[0].docker_url
    # Either the full URL or a recognizable prefix must appear
    assert "quay.io" in output or bio_url[:20] in output


def test_interactive_shows_warning_for_option() -> None:
    """Option with warnings → the first warning text appears in the menu."""
    opt_with_warning = _make_option(
        ContainerSource.GENERATED_FROM_ENVYML,
        warnings=["environment.yml uses a deprecated channel"],
        is_default=True,
    )
    stub_opt = _make_option(ContainerSource.STUB)
    discovery = ContainerDiscovery(options=[opt_with_warning, stub_opt])

    console, buf = _capture_console()

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""):
        mock_stdin.isatty.return_value = True
        select(discovery, container_flag=None, no_interaction=False, console=console)

    assert "deprecated" in buf.getvalue()


# ── Post-selection tests ───────────────────────────────────────────────────────


def test_discovery_selected_field_updated() -> None:
    """After select(), discovery.selected equals the returned ContainerOption."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, _ = _capture_console()

    result = select(discovery, container_flag=None, no_interaction=True, console=console)

    assert discovery.selected is result


def test_confirmation_line_printed() -> None:
    """Console output after selection contains 'Selected:' and the option label."""
    discovery = make_discovery([ContainerSource.BIOCONTAINERS, ContainerSource.STUB])
    console, buf = _capture_console()

    select(discovery, container_flag=None, no_interaction=True, console=console)

    output = buf.getvalue().lower()
    assert "selected" in output


def test_warnings_printed_after_selection() -> None:
    """If the chosen option has warnings, each is printed after the confirmation line."""
    opt = _make_option(
        ContainerSource.GENERATED_FROM_ENVYML,
        warnings=["environment.yml is missing the bioconda channel"],
        is_default=True,
    )
    stub = _make_option(ContainerSource.STUB)
    discovery = ContainerDiscovery(options=[opt, stub])

    console, buf = _capture_console()
    select(discovery, container_flag=None, no_interaction=True, console=console)

    assert "bioconda channel" in buf.getvalue()
