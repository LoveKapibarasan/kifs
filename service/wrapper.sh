#!/bin/bash


# スクリプトの実際の場所を取得（シンボリックリンクも解決）
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# 絶対パスでutil.shを読み込む
source "${SCRIPT_DIR}/../util.sh"

USER_HOME=$(get_user_home)
root_check

# --- 環境変数読み込み ---
ENV_FILE="${SCRIPT_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
    echo "Loaded environment from $ENV_FILE"
else
    echo "Warning: .env file not found at $ENV_FILE"
fi



echo "Normal users list = ${NORMAL_USER}"
for user in $NORMAL_USER; do
	echo "Processing user = $user"
	HOME=$(eval echo "~$user")
	# --- 0. Change ownership
	sudo chown -R "${user}:${user}" "${HOME}/chrome-shogi-profile"
	
	enable_resolved
	sleep 5
	
	# --- 1. メールからKIFを取得 ---
	"$SCRIPT_DIR/../mail/venv/bin/python" "$SCRIPT_DIR/../mail/dl.py"
	echo "E-mail done."

	# --- 2. shogi-extend 側の処理 ---
	sudo -u takanori $SCRIPT_DIR/../shogi-extend/venv/bin/python $SCRIPT_DIR/../shogi-extend/dl.py

	echo "Shogi-Extend done."

	# --- 3. Run script.sh ---
   	BATCH_SCRIPT="${SCRIPT_DIR}/../script.sh"
    	if [ -f "$BATCH_SCRIPT" ]; then
        	bash "$BATCH_SCRIPT"
        	echo "script.sh done."
    	else
        	echo "Warning: Batch script not found at $BATCH_SCRIPT"
    	fi
	
	disable_resolved
	# --- 4. 将棋アプリでバッチ解析 ---
	if [ -z "$KIF_PATH" ] || [ -z "$ENGINE_URI" ]; then
        	echo "Error: KIF_PATH or ENGINE_URI not set in .env"
        	continue
    	fi
	
	sudo chown -R "${user}:${user}" "$KIF_PATH"
	su "$user" -c "\"$HOME\"/ShogiHome*.AppImage --batch-analysis \"$KIF_PATH\" \"$ENGINE_URI\""
	echo "Shogi Analysis done"
done


