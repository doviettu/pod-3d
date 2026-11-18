# POD Studio trên máy Oracle (ARM)

Chạy Studio 24/7 trong 1 container riêng, không đụng các container khác trên máy.

## Cài lần đầu (trên máy Oracle, user ubuntu)

```bash
sudo mkdir -p /srv/pod-studio/{config,hf-cache,claude,secrets} && sudo chown -R $USER: /srv/pod-studio
git clone -b claude/project-thread-j5wo2i https://github.com/doviettu/pod-3d /srv/pod-studio/pod-3d
touch /srv/pod-studio/secrets/claude.env && chmod 600 /srv/pod-studio/secrets/claude.env
cd /srv/pod-studio/pod-3d/deploy/oracle
POD_UID=$(id -u) POD_GID=$(id -g) docker compose up -d --build
docker logs -f pod-studio
```

Chuyển dữ liệu từ Mac (chạy trên Mac): `studio/runtime`, `studio/templates`, `models`, và `~/.config/pod-studio/*` sang `/srv/pod-studio/config/`.

## Đăng nhập Claude và Hugging Face (bạn tự gõ, token không đi qua chat)

1. Trên Mac: `claude setup-token` rồi chép token.
2. Trên Oracle: `nano /srv/pod-studio/secrets/claude.env`, thêm dòng `CLAUDE_CODE_OAUTH_TOKEN=...` và `HF_TOKEN=...` (token Read của huggingface.co).
3. `docker compose up -d` để nạp lại.

## Link HTTPS

Thêm khối trong `Caddyfile.example` vào reverse proxy đang có (hoặc chạy Caddy riêng). Mật khẩu đăng nhập nằm trong `studio/runtime/online/access.json`.

## Đo tốc độ

```bash
docker exec pod-studio python deploy/oracle/bench.py
```

## Cập nhật

```bash
cd /srv/pod-studio/pod-3d && git pull && docker restart pod-studio
```

An toàn: container đặt `POD_ONLINE_ONLY=1`, nên mọi truy cập đều phải đăng nhập và tab Hỏi Claude chỉ có chế độ đọc / vẽ SVG. Cổng 8765 chỉ mở trên 127.0.0.1 của máy chủ. Giới hạn 3 nhân, 12 GB RAM.
