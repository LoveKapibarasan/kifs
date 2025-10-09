#!/bin/bash


# スクリプトの実際の場所を取得（シンボリックリンクも解決）
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# 絶対パスでutil.shを読み込む
source "${SCRIPT_DIR}/../util.sh"

USER_HOME=$(get_user_home)
non_root_check

# --- 環境変数読み込み ---
ENV_FILE="${SCRIPT_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
    echo "Loaded environment from $ENV_FILE"
else
    echo "Warning: .env file not found at $ENV_FILE"
fi



# --- 1. メールからKIFを取得 ---
"$SCRIPT_DIR/../mail/.venv/bin/python" "$SCRIPT_DIR/../mail/dl.py"
echo "E-mail done."

# --- 2. shogi-extend 側の処理 ---
"$SCRIPT_DIR/../shogi-extend/.venv/bin/python" "$SCRIPT_DIR/../shogi-extend/dl.py"
echo "Shogi-Extend done."

# --- 3. Run script.sh ---
"${SCRIPT_DIR}/../script.sh"


# --- 4. 将棋アプリでバッチ解析 ---
"$HOME"/ShogiHome*.AppImage --batch-analysis "$KIF_PATH" "$ENGINE_URI"
