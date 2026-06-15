# Build standalone Windows executables for SportGPSBulkUpload.
#
# Produces two single-file .exe files in dist\:
#   SportGPSBulkUpload.exe      - console build; full CLI plus --gui.
#   SportGPSBulkUpload-GUI.exe  - windowed (no console) build; opens the GUI
#                                 directly, for double-click use.
#
# Requires PyInstaller:
#     py -m pip install pyinstaller
#
# Usage:
#     .\build_exe.ps1

param(
    # Python interpreter to build with. Defaults to the `py` launcher locally
    # (this machine has only `py`). CI must pass `-Python python` so the build
    # uses setup-python's interpreter — the one PyInstaller was installed into.
    # (On the runner the `py` launcher exists too but points at a different,
    # newer Python without PyInstaller, which is what broke the first release.)
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

if (-not $Python) {
    $Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
}
$Py = $Python

# Console build: CLI needs a terminal for the credential prompts; also serves --gui.
& $Py -m PyInstaller `
    --onefile `
    --name SportGPSBulkUpload `
    --hidden-import komoot_bulk_upload.gui `
    --clean `
    --noconfirm `
    app.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller console build failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

# Windowed GUI-only build: no console window when double-clicked.
& $Py -m PyInstaller `
    --onefile `
    --windowed `
    --name SportGPSBulkUpload-GUI `
    --clean `
    --noconfirm `
    app_gui.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller GUI build failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Built: $(Join-Path $PSScriptRoot 'dist\SportGPSBulkUpload.exe')"
Write-Host "Built: $(Join-Path $PSScriptRoot 'dist\SportGPSBulkUpload-GUI.exe')"
