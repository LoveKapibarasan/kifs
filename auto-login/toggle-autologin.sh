#!/bin/bash
# usage: ./toggle-autologin.sh on|off

UNIT_DIR=/etc/systemd/system/getty@tty1.service.d
OVERRIDE=$UNIT_DIR/override.conf
mkdir -p $UNIT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# import functions
UTIL_PATH="${SCRIPT_DIR}/../util.sh"
source "$UTIL_PATH"

hour=$(date +%H)

if [ "$hour" -ge 21 ] && [ "$hour" -lt 23 ]; then
    echo "Enabling autologin..."
     cp "$SCRIPT_DIR/config/getty-autologin.conf" $OVERRIDE
     sudo systemctl daemon-reexec
     sudo reboot
else
    echo "Disabling autologin..."
     cp "$SCRIPT_DIR/config/getty-normal.conf" $OVERRIDE
     # Reload systemd 
     sudo systemctl daemon-reexec
     sudo shutdown now
fi
