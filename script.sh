#!/bin/bash
set -e
# Enable nullglob so literal *.zip is not processed
shopt -s nullglob

TARGET="$HOME/kifs/Evaluation/input"
TMPDIR="$HOME/tmp/kif_extract"
UNPARSED="$HOME/tmp/unparsed_kif"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$TARGET"
mkdir -p "$UNPARSED"

# Determine conversion tool: nkf or iconv
if command -v nkf >/dev/null 2>&1; then
    CONVERT="nkf -w"
elif command -v iconv >/dev/null 2>&1; then
    CONVERT="iconv -f SHIFT_JIS -t UTF-8"
else
    echo "Error: nkf or iconv not found."
    exit 1
fi

# Ensure json.hpp exists
if [ ! -f "${SCRIPT_DIR}/json.hpp" ]; then
    echo "json.hpp not found. Running install_json.sh..."
    "$SCRIPT_DIR/install_json.sh" || { echo "Failed to install json.hpp"; exit 1; }
fi

# Compile organize_kif if missing
if [ ! -x ./organize_kif ] && [ -f organize_kif.cpp ]; then
    echo "Compiling organize_kif..."
    g++ -std=c++17 -O2 -o organize_kif organize_kif.cpp
fi

# Compile compare_kif if missing
if [ ! -x ./compare_kif ] && [ -f compare_kif.cpp ]; then
    echo "Compiling compare_kif..."
    g++ -std=c++17 -O2 -o compare_kif compare_kif.cpp
fi

# Move unparsed files (files not starting with "*#") into a temporary folder
moved_any=false
for f in "$TARGET"/*; do
    if ! $CONVERT "$f" 2>/dev/null | grep -q '^\*#'; then
        echo "Unparsed: $(basename "$f") → $UNPARSED"
        mv "$f" "$UNPARSED/"
        moved_any=true
    fi
done

# Run organize_kif only on parsed files
if [ "$(ls -A "$TARGET")" ]; then
    "$SCRIPT_DIR/organize_kif"  || { echo "Failed to run organize kif"; exit 1; }
else
    echo "No valid KIF files to organize"
fi

# Restore unparsed files back into the input folder
if [ "$moved_any" = true ]; then
    echo "Restoring unparsed files..."
    mv "$UNPARSED"/* "$TARGET"/ 2>/dev/null || true
fi


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


for kif in "$HOME"/Downloads/*.kif; do
    [ -e "$kif" ] || continue   # skip if no .kif files
    mv "$kif" "$TARGET"
done

"$SCRIPT_DIR/compare_kif" "$HOME/kifs/Evaluation/evaluated_kif" "$TARGET"
"$SCRIPT_DIR/compare_kif" "$HOME/kifs/Evaluation/evaluated_kif_24" "$TARGET"
