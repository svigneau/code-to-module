#!/usr/bin/env bash
# Complex pipeline with multiple steps but no case dispatch

INPUT="$1"
REFERENCE="$2"
OUTDIR="${3:-output}"

mkdir -p "$OUTDIR"

# Step 1: align
bwa mem "$REFERENCE" "$INPUT" | samtools view -bS > "$OUTDIR/aligned.bam"

# Step 2: sort
samtools sort "$OUTDIR/aligned.bam" -o "$OUTDIR/sorted.bam"

# Step 3: index
samtools index "$OUTDIR/sorted.bam"

# Step 4: call variants
bcftools mpileup -f "$REFERENCE" "$OUTDIR/sorted.bam" | bcftools call -mv > "$OUTDIR/variants.vcf"

echo "Pipeline complete."
