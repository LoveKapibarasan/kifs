#!/bin/bash
set -e

TARGET="$HOME/kifs/Evaluation/input"
TMPDIR="$HOME/tmp/kif_extract"

mkdir -p "$TARGET"

all_ok=true

# Determine conversion tool: nkf or iconv
if command -v nkf >/dev/null 2>&1; then
    CONVERT="nkf -w"
elif command -v iconv >/dev/null 2>&1; then
    # Shift-JIS → UTF-8 (fallback: read as-is)
    CONVERT="iconv -f SHIFT_JIS -t UTF-8"
else
    echo "Error: nkf or iconv not found."
    exit 1
fi

# Compile organize_kif and compare_kif if not present
[ ! -x ./organize_kif ] && [ -f organize_kif.cpp ] && g++ -O2 -o organize_kif organize_kif.cpp
[ ! -x ./compare_kif ] && [ -f compare_kif.cpp ] && g++ -O2 -o compare_kif compare_kif.cpp

# Check if all files in TARGET start with "*#"
for f in "$TARGET"/*; do
    if ! $CONVERT "$f" 2>/dev/null | grep -q '^\*#'; then
        all_ok=false
        break
    fi
done

if [ "$all_ok" = true ]; then
    ./organize_kif
else
    echo "Unparsed file(s) found"
fi

shopt -s nullglob

# Process only zip files that contain .kif files
for z in "$HOME"/Downloads/*.zip; do
    echo "Checking $z"
    rm -rf "$TMPDIR"
    mkdir -p "$TMPDIR"

    if unzip -l "$z" | grep -q "\.kif"; then
        echo "Processing $z"
        unzip -q -o "$z" -d "$TMPDIR"
        find "$TMPDIR" -type f -name "*.kif" -exec mv {} "$TARGET" \;
        rm -rf "$TMPDIR"
        rm -f "$z"
    else
        echo "Skipping $z (no .kif files)"
        rm -rf "$TMPDIR"
    fi
done

./compare_kif "$HOME/kifs/Evaluation/evaluated_kif" "$TARGET"
./compare_kif "$HOME/kifs/Evaluation/evaluated_kif_24" "$TARGET"
