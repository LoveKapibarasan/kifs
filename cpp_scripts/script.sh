#!/bin/bash
set -e
# Enable nullglob so literal *.zip is not processed
shopt -s nullglob

TMPDIR="$HOME/tmp/kif_extract"
UNPARSED="$HOME/tmp/unparsed_kif"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/../kifs/Evaluation/input"

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
if [ ! -f "${SCRIPT_DIR}/organize_kif" ]; then
    echo "Compiling organize_kif..."
    g++ -std=c++17 -O2 -o "${SCRIPT_DIR}/organize_kif" "${SCRIPT_DIR}/organize_kif.cpp"
fi

# Compile compare_kif if missing
if [ ! -f "${SCRIPT_DIR}/compare_kif" ]; then
    echo "Compiling compare_kif..."
    g++ -std=c++17 -O2 -o "${SCRIPT_DIR}/compare_kif" "${SCRIPT_DIR}/compare_kif.cpp"
fi

# Move unparsed files (files not starting with "*#") into a temporary folder
for f in "$TARGET"/*; do
    if ! $CONVERT "$f" 2>/dev/null | grep -q '^\*#'; then
        echo "Unparsed: $(basename "$f") → $UNPARSED"
        mv "$f" "$UNPARSED/"
    fi
done

# Run organize_kif .    
"$SCRIPT_DIR/organize_kif"

# Restore unparsed files back into the input folder
echo "Restoring unparsed files..."
mv "$UNPARSED"/* "$TARGET"/ 2>/dev/null || true


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
