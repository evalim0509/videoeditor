# autocut one-shot setup for Windows 10/11 (per-user; installers may ask for admin approval).
# Run from the autocut folder in PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass -Force; .\setup\windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Need($id, $cmd) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Installing $id ..." -ForegroundColor Cyan
    winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent
    Refresh-Path
  } else { Write-Host "OK  $cmd already installed" }
}

Need "Python.Python.3.12" "python"
Need "Gyan.FFmpeg" "ffmpeg"
Need "Git.Git" "git"
Need "tailscale.tailscale" "tailscale"
Refresh-Path

if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip wheel

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  Write-Host "NVIDIA GPU found: installing CUDA PyTorch + CUDA libraries for Whisper" -ForegroundColor Cyan
  & $py -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
  & $py -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
} else {
  & $py -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
}
& $py -m pip install -r requirements.txt
& $py -m pip install -e .

& .\.venv\Scripts\autocut.exe hardware
& .\.venv\Scripts\autocut.exe models          # one-time model download, then fully offline
& .\.venv\Scripts\autocut.exe install --firewall
& .\.venv\Scripts\autocut.exe phone
Write-Host "`nDone. Double-click 'autocut' on your desktop any time." -ForegroundColor Green
