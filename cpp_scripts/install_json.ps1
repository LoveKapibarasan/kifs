# PowerShell version of install_json.sh
$ErrorActionPreference = "Stop"


# Download latest single-header json.hpp
Write-Host "Downloading nlohmann/json single header..."
$url = "https://raw.githubusercontent.com/nlohmann/json/develop/single_include/nlohmann/json.hpp"
$output = Join-Path $PSScriptRoot "json.hpp"

Invoke-WebRequest -Uri $url -OutFile $output -UseBasicParsing

if (-not (Test-Path $output)) {
    Write-Error "Failed to download json.hpp"
    exit 1
}

Write-Host "json.hpp installed locally in current directory."
