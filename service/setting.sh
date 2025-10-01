#!/bin/bash

BASE_NAME=analysis
SERVICE_NAME=analysis.service
SERVICE_TIMER=analysis.timer


# import functions
source ../util.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Venv
create_venv "${SCRIPT_DIR}/../mail/"
create_venv "${SCRIPT_DIR}/../shogi-extend/"

# Reset the service
reset_user_service "${SERVICE_NAME}"
reset_user_timer "${SERVICE_TIMER}"

USER_HOME=$(get_user_home)

# タイマーとサービスを無効化 & 停止
systemctl --user disable --now analysis.timer
systemctl --user disable --now analysis.service

read -p "Enter username: " username

allow_nopass "${wrapper}" "${username}"

copy_user_service_files "$BASE_NAME" "$SERVICE_DIR"

# Start the service
start_user_service "${SERVICE_NAME}"
start_user_timer "${SERVICE_TIMER}"
