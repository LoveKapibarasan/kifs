#!/bin/bash

BASE_NAME=analysis
SERVICE_NAME=analysis.service
SERVICE_TIMER=analysis.timer
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# import functions
UTIL_PATH="${SCRIPT_DIR}/../util.sh"
source "$UTIL_PATH"

root_check

echo "Creating venv."
create_venv "${SCRIPT_DIR}/../mail/"
create_venv "${SCRIPT_DIR}/../shogi-extend/"

echo "Reset the service."
reset_service "${SERVICE_NAME}"
reset_timer "${SERVICE_TIMER}"

echo "Creating /opt/kifs/ directory"
mkdir -p /opt/kifs/
    
echo "Creating symbolic link to /opt"
ln -sf "${SCRIPT_DIR}/wrapper.sh" /opt/kifs/wrapper.sh

echo "Copying service files"
cp "${SCRIPT_DIR}/${SERVICE_NAME}" /etc/systemd/system/
cp "${SCRIPT_DIR}/${SERVICE_TIMER}" /etc/systemd/system/

echo "Reloading systemd"
systemctl daemon-reload

echo "Starting services"
start_service "${SERVICE_NAME}"
start_timer "${SERVICE_TIMER}"

echo ""
echo "Setup completed!"
