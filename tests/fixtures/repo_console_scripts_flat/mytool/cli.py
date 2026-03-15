import click


@click.command()
@click.option("-i", "--indata", required=True, help="Input file path.")
@click.option("-m", "--model", default=None, help="Model name.")
@click.option("-o", "--outdir", default=".", help="Output directory.")
def main(indata: str, model: str, outdir: str) -> None:
    """Mytool: a single-command bioinformatics tool."""
    pass


if __name__ == "__main__":
    main()
