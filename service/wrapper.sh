#!/bin/bash

# import functions
source ../util.sh

USER_HOME=$(get_user_home)
root_check

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- 環境変数読み込み (.env がこのディレクトリにある場合) ---
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

enable_resolved

# --- 1. メールからKIFを取得 ---
"$SCRIPT_DIR/../mail/venv/bin/python" "$SCRIPT_DIR/../mail/dl.py"
echo "E-mail done"

# --- 2. shogi-extend 側の処理 ---
"$SCRIPT_DIR/../shogi-extend/venv/bin/python" "$SCRIPT_DIR/../shogi-extend/dl.py"

echo "Shogi-Entend done"

disable_resolved

# --- 3. Run script.sh ---
"$SCRIPT_DIR/../script.sh"

# --- 4. 将棋アプリでバッチ解析 ---
"$USER_HOME"/ShogiHome*.AppImage \
  --batch-analysis "$KIF_PATH" \
  "$ENGINE_URI"

echo "Shogi-Entend done"
