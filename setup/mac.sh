#!/bin/bash
# autocut one-shot setup for macOS (Apple Silicon or Intel). Run from the autocut folder:
#   bash setup/mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v brew >/dev/null; then
  echo "Installing Homebrew (it will ask for your Mac password)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi
for pkg in python@3.12 ffmpeg git; do brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"; done
brew list --cask tailscale >/dev/null 2>&1 || brew install --cask tailscale

PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
.venv/bin/python -m pip install torch torchvision torchaudio   # includes Apple GPU (MPS) support
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .

.venv/bin/autocut hardware
.venv/bin/autocut models        # one-time model download, then fully offline
.venv/bin/autocut install
.venv/bin/autocut phone
echo; echo "Done. Double-click 'autocut' on your Desktop any time."
