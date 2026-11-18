#!/usr/bin/env bash
# Nối các phần nằm trong image vào bản code gắn ở /app, tạo mật khẩu online lần đầu.
set -euo pipefail
cd /app
[ -f studio/studio.py ] || { echo "Không thấy code ở /app (thiếu volume pod-3d)." >&2; exit 1; }

link() {  # link <đích trong image> <đường dẫn trong repo>
  if [ -L "$2" ] || [ ! -e "$2" ]; then ln -sfn "$1" "$2"; fi
}
link /opt/venv .venv
link /opt/vendor studio/.vendor
link /opt/repos studio/repos
link /opt/node/node_modules studio/node_modules

mkdir -p studio/runtime/online studio/runtime/logs studio/runtime/models models
if [ ! -s studio/runtime/online/access.json ]; then
  python - <<'PY'
import json, os, secrets
path = 'studio/runtime/online/access.json'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    json.dump({'username': 'studio', 'password': secrets.token_urlsafe(24)}, f)
print('Đã tạo mật khẩu online mới trong studio/runtime/online/access.json')
PY
fi
chmod 600 studio/runtime/online/access.json

# Model phóng to / vá (giấy phép cho phép dùng thương mại), tải một lần.
dl() { [ -s "models/$1" ] || curl -fsSL -o "models/$1" "$2" || echo "Chưa tải được $1"; }
dl RealESRGAN_x4plus_anime_6B.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth
dl big-lama.pt https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt

exec "$@"
