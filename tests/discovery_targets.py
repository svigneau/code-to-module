"""Shared repo-target definitions for the network discovery tests.

Imported by both conftest.py (for the repo_cache fixture) and
test_discovery_robustness.py (for parametrize).
"""

from __future__ import annotations

# Four repos covering the major CLI shapes code-to-module must handle.
# nf-core/fastqc is a Nextflow module repo (no Python CLI), so it was swapped
# for cutadapt — a Python bioinformatics tool with a single console_scripts
# entry that uses flat argparse/Click (no subcommands → Level-3 cascade).
DISCOVERY_TARGETS: list[dict] = [
    {
        "id": "celltypist",
        "url": "https://github.com/Teichlab/celltypist",
        "shape": "console_scripts",       # setup.py entry_points, flat or subcommands
        "expected_names": ["celltypist"],
        "min_functionalities": 1,
        "max_functionalities": 2,         # annotate + train at most
    },
    {
        # SWAP: nf-core/fastqc is a Nextflow repo with no Python CLI;
        # replaced with cutadapt, a Python adapter trimmer with a single
        # flat CLI entry (console_scripts Level-3 cascade → "cutadapt").
        "id": "cutadapt",
        "url": "https://github.com/marcelm/cutadapt",
        "shape": "console_scripts_flat",  # pyproject.toml [project.scripts], no subparsers
        "expected_names": ["cutadapt"],
        "min_functionalities": 1,
        "max_functionalities": 1,
    },
    {
        "id": "multiqc",
        "url": "https://github.com/MultiQC/MultiQC",
        "shape": "click_flat",            # Click single-command with many options
        "expected_names": ["multiqc"],
        "min_functionalities": 1,
        "max_functionalities": 1,
    },
    {
        "id": "pyfastx",
        "url": "https://github.com/lmdu/pyfastx",
        "shape": "argparse_subcommands",  # argparse with add_subparsers
        "min_functionalities": 2,
        "max_functionalities": 8,
    },
]
