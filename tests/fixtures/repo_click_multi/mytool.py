#!/usr/bin/env python3
"""Click-based CLI with multiple subcommands."""
import click


@click.group()
def cli():
    """My bioinformatics tool."""
    pass

@cli.command()
@click.argument("input_bam")
@click.option("--reference", "-r", required=True, help="Reference genome FASTA.")
@click.option("--outdir", "-o", default=".", help="Output directory.")
def align(input_bam, reference, outdir):
    """Align reads to reference genome."""
    pass

@cli.command()
@click.argument("input_bam")
@click.option("--output", "-o", required=True, help="Sorted BAM output.")
def sort(input_bam, output):
    """Sort BAM file by coordinate."""
    pass

@cli.command()
@click.argument("input_bam")
def index(input_bam):
    """Index a BAM file."""
    pass

if __name__ == "__main__":
    cli()
