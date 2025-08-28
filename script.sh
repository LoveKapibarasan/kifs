#!/bin/bash
set -e

TARGET="$HOME/kifs/Evaluation/input"
TMPDIR="$HOME/tmp/kif_extract"

mkdir -p "$TARGET"

all_ok=true

# nkf or iconv の利用判定
if command -v nkf >/dev/null 2>&1; then
    CONVERT="nkf -w"
elif command -v iconv >/dev/null 2>&1; then
    # Shift-JIS → UTF-8 (失敗したらそのまま読む)
    CONVERT="iconv -f SHIFT_JIS -t UTF-8"
else
    echo "エラー: nkf または iconv が見つかりません。"
    exit 1
fi

for f in "$TARGET"/*; do
    if ! $CONVERT "$f" 2>/dev/null | grep -q '^\*#'; then
        all_ok=false
        break
    fi
done

if [ "$all_ok" = true ]; then
    ./organize_kif
else
    echo "未解析ファイルあり"
fi

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