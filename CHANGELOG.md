# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-03-13

### Added

- **Ingestion** (`ingest.py`): accepts local file paths, directories, or Git URLs; optional `--docs` for documentation URLs; optional `--existing-modules` for consistency checks.
- **Functionality discovery** (`discover.py`): four rule-based detectors (Click decorators, argparse subparsers, shell `case` dispatch, multiple top-level scripts) with LLM fallback; interactive selection UI with confidence thresholds.
- **Complexity assessment** (`assess.py`): assigns Tier 1–5 per functionality with Bioconda and bio.tools cache lookups.
- **LLM inference** (`infer.py`): async Anthropic API calls scoped to one functionality; meta channel invariant enforcement; Tier 5 stub fallback on API errors.
- **Container handling** (`container.py`): two-phase discover/select with six parallel checks (Dockerfile, environment.yml, requirements.txt, Singularity.def, BioContainers, stub); tier-aware default ordering; interactive TTY menu.
- **Bioconda integration** (`bioconda.py`): check existing packages; generate `meta.yaml` scaffold for new submissions.
- **nf-core standards** (`standards/`): versioned JSON schema with all nf-core conventions; singleton loader; `update-standards` command.
- **Test data strategy** (`test_data_match.py`, `test_data_derive.py`, `test_gen.py`): three-strategy priority order — match existing nf-core/test-datasets files, derive/chain from existing data, or fall back to stub mode.
- **Code generation** (`generate.py`): Jinja2 templates for `main.nf`, `meta.yml`, `environment.yml`, `tests/main.nf.test`, and optional `Dockerfile`; consistency check against existing modules.
- **Quick lint** (`quick_lint.py`): fast post-generate structural checks — missing container, missing ext.args/prefix, wrong label, missing versions topic channel, meta.yml field coverage.
- **Validation suite** (`validate_cli.py`, `validate.py`, `fix.py`, `review.py`): separate `ctm-validate` entry point for test/fix/review commands; fix command always shows diff before writing; Class A/B/C fix classification.
- **CLI** (`cli.py`): `convert`, `assess-only`, `containers`, `bioconda-recipe`, and `update-standards` commands.
- **Public API** (`api.py`): clean programmatic interface for other tools.
- Full test suite: 236 tests across all modules; Anthropic API and httpx calls mocked in CI.
- GitHub Actions CI: lint (ruff), type check (mypy strict), test matrix (Python 3.10/3.11/3.12).
- Dev container configuration with nf-core, nf-test, Nextflow, and Claude Code.
