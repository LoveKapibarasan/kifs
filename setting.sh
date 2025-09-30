#!/bin/bash

if [[ ! -f "${HOME}/Linux_device_manager/util.sh" ]]; then
    echo "This requires Linux_device_manager repository"
    exit 1
fi
if [[ ! -f "${HOME}/kifs/util.sh" ]]; then
    ln -s "${HOME}/Linux_device_manager/util.sh" "${HOME}/kifs/util.sh"
else
    echo "util.sh already exists."
fi