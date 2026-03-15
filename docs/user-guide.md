# User Guide

## Installation

Install from PyPI:

```bash
pip install code-to-module
```

Set your Anthropic API key before running `convert`. The other commands do not call the
API:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Devcontainer:** a `.devcontainer/devcontainer.json` is included with all Python
dependencies, nf-core/tools, and nf-test pre-installed. Open the repo in VS Code or
GitHub Codespaces to get a fully-configured environment without a local install.

**External tools for validation:** `validate-module test` and `validate-module fix`
require `nf-test` and Docker or Singularity. `validate-module review` and all
`code-to-module` commands work without them.

---

## Your first module

This example uses [Celltypist](https://github.com/Teichlab/celltypist), a cell type
annotation tool with a genuine CLI, a Bioconda package, and no existing nf-core module
— a good real-world target.

### Step 1 — Convert

```bash
code-to-module convert https://github.com/Teichlab/celltypist \
  --outdir modules/ \
  --docs https://celltypist.readthedocs.io/en/latest/ \
  --no-interaction
```

Pass `--docs` to give the LLM context it could not infer from source code alone —
documentation URLs or local markdown files are both accepted. `--no-interaction` skips
the container selection menu and uses the tier-aware default.

Confirmed terminal output:

```text
→ Single functionality: celltypist
Tier 2  Confidence: 89%
Selected: BioContainers → quay.io/biocontainers/celltypist:1.7.1--pyhdfd78af_0
→ CELLTYPIST: 6 inputs, 5 outputs
✓ Quick lint passed
```

**Reading the output:**

- *Single functionality* — the discovery phase found one CLI entry point (`celltypist`).
  If multiple subcommands or scripts were found, a selection UI would appear.
- *Tier 2* — the tool is in Bioconda with minor inference ambiguity. BioContainers is
  selected as the default container.
- *6 inputs, 5 outputs* — the number of inferred channel specs. These may need review;
  optional inputs in particular should be marked `optional true` in `main.nf`.
- *Quick lint passed* — no structural problems detected before running nf-core lint.

### Step 2 — Review the generated files

```text
modules/
└── celltypist/
    ├── main.nf                 # Process definition
    ├── meta.yml                # Module metadata, channel descriptions, EDAM terms
    ├── environment.yml         # Conda environment pinned to container version
    └── tests/
        ├── main.nf.test        # nf-test spec
        └── nextflow.config     # ext.args wiring
```

Open `main.nf` and check:

- Output globs match what the tool actually produces (e.g. `*.pkl`, `*.csv`)
- Optional CLI arguments are marked `optional true`
- The process label (`process_medium`) matches the tool's typical resource use
- `ext.args` is wired correctly in `tests/nextflow.config`

### Step 3 — Static review

```bash
validate-module review modules/celltypist/
```

This runs static analysis only — no test execution. It reports three severity levels:

- `ERROR` — blocks nf-core submission; must fix before opening a PR
- `WARNING` — should fix; reviewers will ask about it
- `INFO` — suggestion; optional

Fix any `ERROR` items manually, re-run the review to confirm, then proceed.

### Step 4 — Test and fix

```bash
validate-module test modules/celltypist/
validate-module fix modules/celltypist/
```

See [The validate-module suite](#the-validate-module-suite) below for details.

### Step 5 — Test data and submission

If `derive_test_data.sh` was written alongside the module, it means Strategy 2a
applied: the test data can be derived from an existing nf-core/test-datasets file but
needs a separate PR. Run the script, inspect its output, and PR the derived file to
[nf-core/test-datasets](https://github.com/nf-core/test-datasets) before submitting
the module.

For Celltypist specifically, all inputs fall back to `stub` strategy because `.pkl`
model files and `.h5ad` count matrices are not in nf-core/test-datasets. The TODO
comments in `tests/main.nf.test` describe what real test data would look like. Add
it to nf-core/test-datasets first, then update the test paths.

When ready, open a module PR following the
[nf-core modules contributing guide](https://nf-co.re/docs/contributing/modules).

---

## Test data strategies

The tool tries four strategies in priority order. It stops at the first one that
produces a usable test input for each channel.

| Strategy | Name | When it applies |
|----------|------|-----------------|
| 1 | **Match** | A file matching the required format and size is already in nf-core/test-datasets. Always preferred. |
| 2a | **Derive** | No exact match, but a suitable file can be subsetted or transformed from an existing nf-core/test-datasets file (VCF region, FASTA extract, BAM subsample). Generates `derive_test_data.sh`. |
| 2b | **Chain** | The input is the natural output of a known upstream nf-core module. Generates a `setup {}` block in `main.nf.test`. Use only when the input would be too large to store, or when the upstream module runs in seconds (index generation). |
| 3 | **Stub** | Data is too large, proprietary, or cannot be derived. Generates a `stub:` block; tests run with `nf-test --stub`. Always produces a syntactically valid test. |

Strategy 2b (`chain`) is intentionally lower priority than 2a (`derive`). Stored files
keep CI test runtime predictable; `setup {}` blocks add upstream module execution time
to every test run. Use `chain` only when a stored file is genuinely not appropriate.

For derive and chain cases, the test data file or the upstream module must be
PRed to [nf-core/test-datasets](https://github.com/nf-core/test-datasets) before
the module PR can be merged.

---

## The validate-module suite

### test

`validate-module test` runs `nf-core modules lint` and `nf-test` against the module,
captures the output, and classifies each failure into Class A, B, or C. Results are
written to stdout as a formatted `TestReport`; pass `--json-output` to save them for
use with `fix`.

```bash
validate-module test modules/celltypist/
validate-module test modules/celltypist/ --lint-only
validate-module test modules/celltypist/ --json-output report.json
```

### fix

`validate-module fix` loads a test report (or re-runs validation), shows a coloured
diff panel for each fixable failure, and waits for explicit approval before writing any
file. Every diff is labelled with the fix source:

| Class | Label | What triggers it | Auto-fixable |
|-------|-------|------------------|--------------|
| A | `[rule]` | Deterministic structural issue: wrong emit name, missing `topic: versions` tag, container URL format, ext.args pattern, meta.yml field | Yes, with approval |
| B | `[llm]` | Requires reading code in context: wrong output glob, wrong process label, unclear channel description | Yes, with approval — lower trust |
| C | `[manual]` | Cannot be resolved safely without human judgement | Never — explains what to do and stops |

The `fix` command never silently modifies files. At each diff you choose:
`y` apply, `n` skip, `a` apply all remaining Class A, `s` skip all remaining, `q` quit.
Validation re-runs automatically after fixes are applied unless `--no-revalidate` is
passed.

```bash
validate-module fix modules/celltypist/
validate-module fix modules/celltypist/ --from-report report.json
validate-module fix modules/celltypist/ --class-a-only
validate-module fix modules/celltypist/ -y    # auto-approve Class A; Class B still prompts
```

### review

`validate-module review` performs static analysis only — no subprocess, no test
execution. It checks channel naming conventions, process label appropriateness,
`ext.args` usage, meta.yml completeness, versions channel structure, and EDAM ontology
coverage.

```bash
validate-module review modules/celltypist/
validate-module review modules/celltypist/ --errors-only
validate-module review modules/celltypist/ --json-output review.json
```

Example output:

```text
CELLTYPIST — review
─────────────────────────────────────────────────────────────────────
  ERROR    versions channel missing 'topic: versions' tag
  WARNING  process label 'process_medium' may be low for h5ad inputs
  INFO     EDAM term not found for .pkl input — add manually if known
─────────────────────────────────────────────────────────────────────
  1 error (blocks submission) · 1 warning · 1 info
```
