$ErrorActionPreference = "Stop"

$TMPDIR = "$HOME/tmp/kif_extract"
$UNPARSED = "$HOME/tmp/unparsed_kif"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$TARGET = "$SCRIPT_DIR/../Evaluation/input"

New-Item -ItemType Directory -Force -Path $TARGET | Out-Null
New-Item -ItemType Directory -Force -Path $UNPARSED | Out-Null
New-Item -ItemType Directory -Force -Path $TMPDIR | Out-Null


# Ensure json.hpp exists
if (-not (Test-Path "$SCRIPT_DIR/json.hpp")) {
    Write-Host "json.hpp not found. Running install_json.ps1..."

    try {
        & "$SCRIPT_DIR/install_json.ps1"
    }
    catch {
        Write-Error "Failed to install json.hpp"
        exit 1
    }
}

# Compile organize_kif if missing
if (-not (Test-Path "$SCRIPT_DIR/organize_kif.exe")) {
    Write-Host "Compiling organize_kif..."
    g++ "$SCRIPT_DIR/organize_kif.cpp" "$SCRIPT_DIR/util.cpp" -std=c++17 -O2 -o "$SCRIPT_DIR/organize_kif.exe"
}

# Compile compare_kif if missing
if (-not (Test-Path "$SCRIPT_DIR/compare_kif.exe")) {
    Write-Host "Compiling compare_kif..."
    g++ -std=c++17 -O2 -o "$SCRIPT_DIR/compare_kif.exe" "$SCRIPT_DIR/compare_kif.cpp"
}

Get-ChildItem -Path $TARGET -Filter "*.kif" -File | ForEach-Object {
    $kif = $_
    $base = [System.IO.Path]::GetFileNameWithoutExtension($kif.Name)
    $kfkPath = Join-Path $TARGET "$base.kfk"

    # xxx.kfk が無ければ xxx.kif を移動
    if (-not (Test-Path $kfkPath)) {
        Move-Item -LiteralPath $kif.FullName -Destination $UNPARSED -Force
    }
}

# Run organize_kif
& "$SCRIPT_DIR/organize_kif.exe"

# Restore unparsed files
Write-Host "Restoring unparsed files..."
Get-ChildItem -Path $UNPARSED -File | Move-Item -Destination $TARGET -Force

# Process ZIP files containing .kif
Get-ChildItem "$HOME/Downloads" -Filter *.zip | ForEach-Object {
    Write-Host "Checking $($_.FullName)"
    Remove-Item -Recurse -Force $TMPDIR -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $TMPDIR | Out-Null

    if ( (tar -tf $_.FullName | Select-String -Quiet "\.kif") ) {
        Write-Host "Processing $($_.FullName)"
        tar -xf $_.FullName -C $TMPDIR
        Get-ChildItem $TMPDIR -Recurse -Filter *.kif | Move-Item -Destination $TARGET -Force
        Remove-Item -Recurse -Force $TMPDIR
        Remove-Item $_.FullName
    } else {
        Write-Host "Skipping $($_.FullName) (no .kif files)"
        Remove-Item -Recurse -Force $TMPDIR
    }
}

# Move loose .kif files
Get-ChildItem "$HOME/Downloads" -Filter *.kif | Move-Item -Destination $TARGET


if (-not (Test-Path "$SCRIPT_DIR/convert_kif.exe")) {
    Write-Host "Compiling convert_kif..."
    g++ -std=c++17 -O2 -o "$SCRIPT_DIR/convert_kif.exe" "$SCRIPT_DIR/convert_kif.cpp"
}

& "$SCRIPT_DIR/convert_kif.exe" $TARGET


# Compare
## normalize
$folderA = Resolve-Path "$SCRIPT_DIR/../Evaluation/evaluated_kif"
$folderB = Resolve-Path "$TARGET"

& "$SCRIPT_DIR/compare_kif.exe" $folderA $folderB