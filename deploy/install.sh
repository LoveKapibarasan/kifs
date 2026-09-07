#!/usr/bin/env bash
# Install the KIF collector as a systemd service on the current host.
#
#   ./deploy/install.sh              # install + enable + start
#   ./deploy/install.sh --no-start   # install only
#
# Runs as a *user* service so it needs no root and inherits the user's
# ~/.env.global (which holds the Infisical machine identity).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${KIFS_DATA_DIR:-$HOME/kifs-data}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_NAME="kifs-collector.service"
TIMER_UNITS=("kifs-report.service" "kifs-report.timer")
START=1

for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

echo "[*] repo: $REPO_DIR"
echo "[*] data: $DATA_DIR"

if [[ ! -f "$HOME/.env.global" ]]; then
  echo "[!] $HOME/.env.global not found — the service cannot reach Infisical." >&2
  echo "    Copy at least INFISICAL_KIFS_CLIENT_ID / _CLIENT_SECRET / _ENDPOINT there." >&2
  exit 1
fi

echo "[*] creating virtualenv"
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/.venv/bin/pip" install --quiet -e "$REPO_DIR"

mkdir -p "$DATA_DIR"/{kif,state,logs}

echo "[*] verifying credentials"
KIFS_DATA_DIR="$DATA_DIR" "$REPO_DIR/.venv/bin/kifs" --no-log-file secrets check

mkdir -p "$UNIT_DIR"
install_unit() {
  sed -e "s|%h/kifs\b|$REPO_DIR|g" \
      -e "s|%h/kifs-data|$DATA_DIR|g" \
      -e "s|^User=%i$||" \
      "$REPO_DIR/deploy/$1" > "$UNIT_DIR/$1"
}
install_unit "$UNIT_NAME"
for unit in "${TIMER_UNITS[@]}"; do install_unit "$unit"; done

systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME"
# The daily report is a timer; enabling the .service itself would run it at boot.
systemctl --user enable --now kifs-report.timer

# Keep the service alive across logout/reboot without an interactive session.
loginctl enable-linger "$USER" 2>/dev/null || \
  echo "[!] could not enable linger; the service will stop when you log out."

if [[ "$START" == "1" ]]; then
  systemctl --user restart "$UNIT_NAME"
  sleep 3
  systemctl --user --no-pager status "$UNIT_NAME" | head -20
fi

cat <<MSG

[+] installed.
    logs   : journalctl --user -u $UNIT_NAME -f
    status : KIFS_DATA_DIR=$DATA_DIR $REPO_DIR/.venv/bin/kifs status
    stop   : systemctl --user stop $UNIT_NAME
    report : systemctl --user list-timers kifs-report.timer
             KIFS_DATA_DIR=$DATA_DIR $REPO_DIR/.venv/bin/kifs report          # preview
             KIFS_DATA_DIR=$DATA_DIR $REPO_DIR/.venv/bin/kifs report --send   # send now
    sync   : uploads run inside the collector; for a bulk backfill stop it and run
             KIFS_DATA_DIR=$DATA_DIR $REPO_DIR/.venv/bin/kifs sync
MSG
