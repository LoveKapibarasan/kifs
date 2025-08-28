#!/bin/bash
set -e

TARGET="$HOME/kifs/Evaluation/input"
TMPDIR="$HOME/tmp/kif_extract"

mkdir -p "$TARGET"

./organize_kif

shopt -s nullglob

for z in "$HOME"/Downloads/*.zip; do
  echo "Processing $z"
  rm -rf "$TMPDIR"
  7z x -y -o"$TMPDIR" "$z" > /dev/null
  find "$TMPDIR" -type f -name "*.kif" -exec mv {} "$TARGET" \;
  rm -rf "$TMPDIR"
  rm -f "$z"
done

./compare_kif "$HOME/kifs/Evaluation/evaluated_kif" "$TARGET"
./compare_kif "$HOME/kifs/Evaluation/evaluated_kif_24" "$TARGET"
