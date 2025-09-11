#!/bin/bash
set -e

# Detect distro
if [ -f /etc/debian_version ]; then
    DISTRO="debian"
elif [ -f /etc/arch-release ]; then
    DISTRO="arch"
else
    echo "Unsupported distro. Only Debian or Arch are supported."
    exit 1
fi

echo "Detected $DISTRO system"

# Ensure prerequisites
if [ "$DISTRO" = "debian" ]; then
    sudo apt-get update
    sudo apt-get install -y wget
elif [ "$DISTRO" = "arch" ]; then
    sudo pacman -Sy --noconfirm wget
fi

# Download latest single-header json.hpp
echo "Downloading nlohmann/json single header..."
wget -O json.hpp https://raw.githubusercontent.com/nlohmann/json/develop/single_include/nlohmann/json.hpp

echo "json.hpp installed locally in current directory."
echo "Now you can compile with:"
echo "  g++ -std=c++17 -O2 -o organize_kif organize_kif.cpp"
