#!/bin/bash


# import functions
source ../util.sh

USER_HOME=$(get_user_home)
root_check


# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- 環境変数読み込み (.env がこのディレクトリにある場合) ---
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

enable_resolved
sleep 5

echo "Normal users list = ${NORMAL_USER}"
for user in $NORMAL_USER; do
	echo "Processing user = $user"
	HOME=$(eval echo "~$user")
	# --- 0. Change ownership
	sudo chown -R "${user}:${user}" "/home/${user}/chrome-shogi-profile"

	# --- 1. メールからKIFを取得 ---
	"$SCRIPT_DIR/../mail/venv/bin/python" "$SCRIPT_DIR/../mail/dl.py"
	echo "E-mail done."

	# --- 2. shogi-extend 側の処理 ---
	sudo -u takanori $SCRIPT_DIR/../shogi-extend/venv/bin/python $SCRIPT_DIR/../shogi-extend/dl.py

	echo "Shogi-Extend done."

	# --- 3. Run script.sh ---
	cd ..
	./script.sh
	cd -
	echo "script.sh done."

	# --- 4. 将棋アプリでバッチ解析 ---
	su "$user" -c "\"$USER_HOME\"/ShogiHome*.AppImage --batch-analysis \"$KIF_PATH\" \"$ENGINE_URI\""
	echo "Shogi Analysis done"
done

disable_resolved
