#!/usr/bin/env bash
# Shell tool with case-based dispatch

CMD="$1"
shift

case "$CMD" in
  align)
    echo "Aligning: samtools align $@"
    ;;
  sort)
    echo "Sorting: samtools sort $@"
    ;;
  index)
    echo "Indexing: samtools index $@"
    ;;
  *)
    echo "Usage: mytool.sh {align|sort|index} [args...]"
    exit 1
    ;;
esac
