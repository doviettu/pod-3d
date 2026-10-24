# pod-3d

Tạo file in all-over (Front, Back, Sleeve, ...) từ 1 ảnh mockup áo 3D, chạy hoàn toàn trên máy (Mac Apple Silicon), không tốn phí API.

> Chỉ dùng với ảnh bạn có quyền sử dụng. Không xoá watermark trên ảnh của người khác.

## Cài đặt (Mac mini)

```bash
git clone https://github.com/doviettu/pod-3d.git
cd pod-3d
./scripts/install_mac.sh
```

Script cài `uv`, tạo `.venv` (Python 3.12), cài thư viện và tải 2 model:

| Model | Dùng để | License |
| --- | --- | --- |
| RealESRGAN_x4plus_anime_6B | Phóng to 4x giữ nét tranh vẽ / pixel art | BSD-3 |
| big-lama | Lấp phần áo mockup không nhìn thấy | Apache-2.0 |

## Dùng

```bash
.venv/bin/python -m pod3d templates                      # xem các loại áo có sẵn
.venv/bin/python -m pod3d run mockup.jpg -t sweater      # ra thư mục mockup_panels/ + file .zip
.venv/bin/python -m pod3d run mockup.jpg -t sweater --no-model   # nhanh, bỏ AI upscale
```

Mỗi template chỉ dùng được cho **đúng ảnh mockup 3D nó được hiệu chỉnh** (cùng dáng áo, cùng góc chụp, chỉ khác hình in). Ảnh khác dáng, ví dụ áo mặc trên người mẫu, sẽ bị từ chối; khi đó chấm điểm mốc (mục dưới) hoặc tạo template mới. Thêm `--force` để vẫn chạy.

### Ảnh mockup bất kỳ: chấm điểm mốc

Không có file in mẫu thì bấm 8 điểm quanh mỗi mảnh trên ảnh (theo chiều kim đồng hồ, từ góc trên trái):

```bash
.venv/bin/python -m pod3d mark mockup.jpg -t hoodie   # mở trang chấm điểm trong trình duyệt, bấm Lưu
.venv/bin/python -m pod3d run mockup.jpg -t hoodie    # tự dùng file mockup.marks.json vừa lưu
```

Vùng bấm được kéo phẳng ra cả mảnh in; mép và nếp gấp kém chính xác hơn template đã hiệu chỉnh. Ảnh mockup càng lớn càng nét (nên từ 1500 px trở lên).

File in giữ đúng kích thước file mẫu, 300 DPI:

| Template | Mảnh (px) |
| --- | --- |
| sweater | Front, Back 6048 × 6559 · Sleeve 4720 × 5744 · Collar 4456 × 627 · Hem 10397 × 1240 · Wrist 2478 × 1255 |
| hoodie | Front, Back 5100 × 6300 · Hood 1800 × 1150 · Left, Right 1500 × 2100 |

## Thêm loại áo mới (template)

1. Tạo `templates/<ten>/template.json` (tên mảnh, kích thước, vùng nhìn thấy trên mockup).
2. Chuẩn bị 1 hoặc nhiều thư mục mẫu, mỗi thư mục gồm 1 ảnh mockup + các file in thật đặt tên theo mảnh (`Front.jpg`, `Back.jpg`, ...). Mockup phải cùng dáng 3D.
3. `.venv/bin/python -m pod3d calibrate <ten> mau1/ mau2/`

Càng nhiều bộ mẫu cùng dáng, bản đồ trải phẳng càng chính xác.

## Cách hoạt động

1. **Hiệu chỉnh** (1 lần mỗi loại áo): SIFT + optical flow tìm điểm khớp giữa mockup và file in thật, ra bản đồ trải phẳng và vùng tin cậy.
2. **Trải phẳng** mockup mới theo bản đồ, chỉnh màu (mockup bị đổ bóng).
3. **Lấp phần bị che**: dải hoạ tiết lặp được nối tiếp đúng chu kỳ; phần còn lại do LaMa vẽ.
4. **Làm sạch** vân len, gom màu phẳng về bảng màu chuẩn.
5. **Phóng to** bằng Real-ESRGAN (GPU Apple qua MPS), ép đúng kích thước khuôn, xuất JPG 300 DPI + ZIP.

## Trạng thái

Bản thử nghiệm. Vùng giữa thân trước/sau khớp tốt với file thật; phần hai bên và tay áo (mockup không thấy rõ) là phỏng đoán và cần kiểm tra trước khi in. Mũ hoodie chưa hiệu chỉnh được.
