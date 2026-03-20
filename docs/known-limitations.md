# Known Limitations

!!! warning "Tools that assess as Tier 4–5"
    Perl wrappers, tools with no detectable CLI structure, and tools
    that require large proprietary databases (BLAST, Kraken2) typically
    assess as Tier 4 or 5. The tool reports the tier honestly and
    generates what it can; Tier 5 tools require manual module authoring.

    TrimGalore is a representative example: it is a Perl wrapper around
    cutadapt and FastQC with no Python AST to analyse. It correctly
    assesses as Tier 5 and the tool stops with an explanation rather than
    producing a misleading partial module. For these tools, use the
    assessment output to understand what the tool found, then author the
    module manually using `nf-core modules create` as a starting point.

!!! info "Library-only tools (no CLI entry point)"
    Tools like decoupler and liana-py expose a Python API but no
    command-line interface. These are not yet supported. `code-to-module`
    works by analysing CLI structure — `console_scripts` entry points,
    argparse/Click argument parsers, shell `case` statements — and cannot
    generate a meaningful module for code that is designed to be called as
    `import foo; foo.run(...)` rather than `foo --input file --output dir`.

    Library-to-module support is planned — see the
    [architecture doc](architecture.md) for the proposed
    architecture. The intended approach is to generate a thin CLI wrapper
    script first, then feed that into the standard conversion pipeline.

!!! info "Domain-specific test data"
    Formats not in nf-core/test-datasets (h5ad, pkl, mzML, Visium
    directories) fall back to stub mode. Real test data must be added
    manually and PRed to nf-core/test-datasets before submission.

    Celltypist is a representative example: its inputs (`.pkl` model files
    and `.h5ad` or `.csv` count matrices) are not in nf-core/test-datasets.
    All input channels in `tests/main.nf.test` receive stub strategy, which
    produces a syntactically valid test that passes lint and confirms channel
    wiring — but it cannot test real data flow. The TODO comments in the
    generated test file describe exactly what data would be needed.

!!! note "LLM non-determinism"
    Running convert twice on the same tool may produce slightly different
    channel names, output globs, or script arguments. This is expected
    behaviour — always review the generated module before submitting.
    The post-processing guards in `infer.py` enforce structural invariants
    (meta as first input, no duplicate versions emit, no TODO placeholders)
    but do not guarantee identical output across runs.

    If two runs produce significantly different modules, the tool-specific
    context is ambiguous and both outputs deserve manual review. Passing
    `--docs` with the tool's documentation URL typically reduces variation
    by giving the LLM more signal to work with.

---

## Planned improvements

The following are known future directions. Contributions are welcome — see
[GitHub Issues](https://github.com/svigneau/code-to-module/issues) for open items.

- **Library-to-module:** generate a CLI wrapper for library-only Python tools, then
  pipe the result through the standard conversion pipeline.
- **Strategy 2c (test data from tool docs):** if tool documentation links to example
  data files of appropriate size, use them directly rather than deriving from
  nf-core/test-datasets.
- **Snakemake-to-Nextflow:** convert Snakemake rules to nf-core module format, using
  the rule's input/output blocks to infer channel structure instead of CLI analysis.
