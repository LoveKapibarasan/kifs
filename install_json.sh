#!/bin/bash

# import functions
source ../util.sh

# Ensure prerequisites
if is_command apt; then
    sudo apt-get update
    sudo apt-get install -y wget
elif is_command pacman; then
    sudo pacman -Sy --noconfirm wget
fi

# Download latest single-header json.hpp
echo "Downloading nlohmann/json single header..."
wget -O json.hpp https://raw.githubusercontent.com/nlohmann/json/develop/single_include/nlohmann/json.hpp

echo "json.hpp installed locally in current directory."
echo "Now you can compile with:"
echo "  g++ -std=c++17 -O2 -o organize_kif organize_kif.cpp"
