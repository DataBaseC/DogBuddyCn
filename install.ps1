# DogBuddyCn installer for Windows — venv + deps + data dirs + starter config.
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
# Usage (in PowerShell at the repo root):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# Then:
#   python manage.py start     (or:  %LOCALAPPDATA%\dogbuddy\venv\Scripts\python -m dogbuddy.main)
$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = if ($env:DOGBUDDY_DATA_DIR) { $env:DOGBUDDY_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "dogbuddy" }
$VenvDir = Join-Path $DataDir "venv"

Write-Host "== DogBuddyCn install (Windows) =="
Write-Host "   repo:   $RepoDir"
Write-Host "   data:   $DataDir"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) { Write-Error "Python 3.11+ required (https://python.org)"; exit 1 }
Write-Host "   python: $($py.Source)"

Write-Host "-- creating venv at $VenvDir"
& $py.Source -m venv $VenvDir
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip -q
& $venvPython -m pip install -r (Join-Path $RepoDir "requirements.txt") -q

Write-Host "-- creating data dirs"
foreach ($d in @("", "memory", "memory\windows", "logs", "checkpoints", "workspaces", "skills")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
}

$cfg = Join-Path $DataDir "config.yaml"
if (!(Test-Path $cfg)) {
    Copy-Item (Join-Path $RepoDir "config.example.yaml") $cfg
    Write-Host "-- starter config written to $cfg (no need to edit now)"
}

Write-Host "== done =="
Write-Host "Next steps:"
Write-Host "  1. start:  python $RepoDir\manage.py start"
Write-Host "  2. open the console http://localhost:8100 — the setup wizard guides you"
Write-Host "     through the LLM config (base_url + api_key; the model list is fetched"
Write-Host "     automatically). No config editing needed."
Write-Host "  3. optional: edit $cfg later for gateway/panel tokens"
