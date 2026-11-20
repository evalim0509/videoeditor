#!/bin/bash
# autocut one-shot setup for Ubuntu/Debian. Run from the autocut folder:  bash setup/linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg git libegl1 libgles2
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
if command -v nvidia-smi >/dev/null; then
  .venv/bin/python -m pip install torch torchvision torchaudio            # CUDA build
  .venv/bin/python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
else
  .venv/bin/python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .

.venv/bin/autocut hardware
.venv/bin/autocut models
.venv/bin/autocut install
.venv/bin/autocut phone
echo; echo "Done. Double-click 'autocut' on your desktop any time."
