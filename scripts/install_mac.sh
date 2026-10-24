#!/usr/bin/env bash
# Cài pod-3d trên Mac (Apple Silicon). Chạy lại nhiều lần cũng không sao.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Cài uv (quản lý Python)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Tạo môi trường Python 3.12 trong .venv"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

echo "==> Tải model (Real-ESRGAN anime 6B: BSD-3, LaMa: Apache-2.0)"
mkdir -p models
dl() { [ -s "models/$1" ] || curl -L --fail -o "models/$1" "$2"; }
dl RealESRGAN_x4plus_anime_6B.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth
dl big-lama.pt https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt

echo "==> Kiểm tra"
.venv/bin/python - <<'PY'
import torch
from pod3d import template, upscale, inpaint
print("templates:", template.available())
print("GPU Apple (MPS):", torch.backends.mps.is_available())
print("upscaler:", upscale.available(), "| LaMa:", inpaint.available())
PY
echo "Xong. Chạy thử: .venv/bin/python -m pod3d run <mockup.jpg> -t sweater"
