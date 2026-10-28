# POD Studio v2: thiết kế lại toàn diện

Ngày: 28/10/2026 · Chạy trên Mac mini 16 GB · Dùng cá nhân

## Mục tiêu

Một trang web chạy trên Mac: dán ảnh hoặc link ảnh áo tìm được, nhận lại file in sẵn sản xuất, tự lưu lên Google Drive và ghi vào Sheet quản lý.

- **2D**: art in một mặt (trước hoặc sau). Tự tìm vùng art, tách ra PNG nền trong suốt 4500 × 5400 px, 300 DPI.
- **3D**: áo in tràn, ghép may từ 6 mảnh. Hai chế độ: **AI** (gọi API, tự động) và **Local** (thư viện kiểu dáng, tự nhận dáng áo rồi trải phẳng).

## Hiện trạng (đã kiểm tra)

| Phần | Ở đâu | Tình trạng |
| --- | --- | --- |
| Engine `pod3d` (calibrate, unwrap, fill, upscale, mark 8 điểm) | GitHub, nhánh `prototype-unwrap` | Chạy được với sweater, hoodie. Chỉ đúng với đúng ảnh mockup đã hiệu chỉnh |
| `pod-studio` do Codex làm (web, OpenAI gpt-image, duyệt chữ/logo, PSD có lớp, SAM 2, VTracer, nhận template, thư viện kiểu hoodie, Cloudflare tunnel) | Chỉ trên Mac: `/Volumes/ViTu/Claude Codex/POD/pod-studio`, **chưa có Git** | Nhiều tính năng nhưng dồn trong `studio.py` 41 KB + `index.html` 54 KB, chưa có 2D, chưa có link ảnh, chưa có Drive |

Kết luận: giữ engine và các module tốt của Codex (`openai_api`, `review`/`psd_export`, `segment_asset`, `garment_styles`, `template_detection`), dựng lại phần vỏ web theo luồng 2D/3D mới. Không viết lại từ đầu.

## Kiến trúc

```
Trình duyệt ── FastAPI (127.0.0.1) ── Hàng đợi 1 worker (SQLite)
                   │                        ├─ pipeline 2D
                   │                        ├─ pipeline 3D Local (pod3d)
                   │                        └─ pipeline 3D AI (OpenAI)
                   └─ Google Drive + Sheets (đồng bộ sau khi duyệt)
```

- **Backend**: FastAPI + SQLite (nguồn dữ liệu chính). Một worker chạy tuần tự để vừa RAM 16 GB.
- **Frontend**: HTML + Alpine.js, không cần build. Xem ảnh lớn bằng OpenSeadragon (BSD) với ảnh lát DZI tạo bởi pyvips: zoom tới từng pixel của file 6000 px mà không lag.
- **Truy cập từ xa**: giữ Cloudflare tunnel + đăng nhập của Codex.

## Luồng 2D

1. **Nạp**: tải file, dán ảnh (Ctrl+V), hoặc dán link. Link được tải về máy, kiểm tra loại file, cỡ tối đa 32 MB.
2. **Tìm vùng art**: tách áo khỏi nền (BiRefNet qua `rembg`, MIT), rồi trong áo tìm vùng khác màu vải. Hiện khung đề xuất, người dùng kéo chỉnh được. Ảnh có 2 mặt cạnh nhau thì hỏi chọn mặt.
3. **Làm phẳng**: nắn phối cảnh theo 4 góc khung, bỏ bóng và vân vải (đã có `clean.py`).
4. **Bỏ nền**: tách màu vải thành trong suốt; viền mềm, giữ chi tiết mảnh.
5. **Phóng to**: Real-ESRGAN (đã cài), rồi đặt vào canvas 4500 × 5400, căn giữa trên, chừa lề an toàn.
6. **Tùy chọn AI**: nút "Làm lại bằng AI" gửi vùng art cho gpt-image với `background: transparent` khi bản local không đạt.
7. **Tùy chọn vector**: VTracer xuất SVG cho art ít màu.

## Luồng 3D

**Local**
1. **Nhận kiểu dáng**: so ảnh với thư viện (CLIP/SigLIP embedding + silhouette). Ra kiểu dáng (sweater, hoodie chui, hoodie khóa kéo, áo thun AOP…) và góc chụp.
2. **Chọn khuôn**: nếu trùng góc chụp đã hiệu chỉnh thì dùng map sẵn; nếu không thì mở trang chấm 8 điểm mỗi mảnh (`pod3d mark`).
3. **Trải phẳng → lấp phần khuất → làm sạch → phóng to → xuất đúng kích thước khuôn** (engine hiện có).

**AI**
1. Gửi mockup + ảnh hướng dẫn khuôn cho gpt-image, phục hồi artwork từng mảnh.
2. Code local ghép lên khuôn đúng kích thước, DPI. AI không quyết định đường cắt.
3. Hiện ước tính số lần gọi API trước khi chạy; ghi chi phí thực vào Sheet.

**Thư viện kiểu dáng** (trang riêng): mỗi kiểu gồm tên, 6 mảnh và kích thước xưởng, ảnh tham chiếu, các map đã hiệu chỉnh theo góc chụp, trạng thái "đã duyệt in thật".

## Xem kết quả

- Lưới thẻ: ảnh gốc, từng mảnh, bản ghép lên áo.
- Nút **Mở rộng** mở toàn màn hình; cuộn để zoom, kéo để di chuyển, phím 1 = 100% pixel.
- Thanh trượt so sánh ảnh gốc với kết quả; nền ca rô để thấy vùng trong suốt.
- Đánh dấu màu vùng AI tự dựng hoặc phần khuất (đã có `local-quality.json`).

## Google Drive và Sheet

Đăng nhập Google bằng OAuth (ứng dụng desktop, scope `drive.file` + `spreadsheets`: chỉ đụng tới file do tool tạo).

```
POD Studio/
├─ 2D/2026-10/<SKU>/  source.jpg · art.png · art.svg · preview.jpg
├─ 3D/2026-10/<SKU>/  source.jpg · panels/*.png · panels.zip · preview.jpg
├─ Thư viện kiểu dáng/
└─ POD Quản lý (Sheet)
```

Sheet tự tạo các tab:
- **Jobs**: SKU, ngày, loại 2D/3D, chế độ, link nguồn, kiểu dáng, trạng thái, ảnh nhỏ `=IMAGE()`, link thư mục, chi phí AI, ghi chú, quyền sử dụng.
- **Kiểu dáng**: danh sách thư viện và kích thước mảnh.
- **Cài đặt**: quy tắc đặt SKU, lề an toàn.

SQLite vẫn là gốc; Sheet là bản sao để xem và lọc. Đồng bộ chạy khi bấm Duyệt, lỗi mạng thì xếp hàng thử lại.

## Việc cần làm

- [ ] 1. Đưa `pod-studio` vào repo (`studio/`), loại `runtime/`, `.vendor/`, `node_modules/`, khóa API → Verify: `git status` sạch, test cũ của Codex vẫn chạy.
- [ ] 2. Dựng vỏ FastAPI + SQLite jobs + worker, chuyển dần route của `studio.py` → Verify: `curl 127.0.0.1:8765/api/status` trả 200, job cũ hiện trong lịch sử.
- [ ] 3. Trang chính 2 tab 2D / 3D, ô nạp ảnh/link/dán → Verify: dán link ảnh, thấy ảnh xem trước.
- [ ] 4. Pipeline 2D (tìm vùng, phẳng, bỏ nền, phóng to, canvas 4500 × 5400) → Verify: 5 ảnh mẫu ra PNG đúng cỡ, có alpha, 300 DPI.
- [ ] 5. Trình xem OpenSeadragon + so sánh trước/sau → Verify: zoom tới pixel trên mảnh 6048 × 6559 mượt.
- [ ] 6. Thư viện kiểu dáng + nhận dáng tự động → Verify: ảnh sweater/hoodie mẫu được nhận đúng kiểu, ảnh lạ hỏi chọn tay.
- [ ] 7. Nối 3D Local và 3D AI vào luồng mới → Verify: mockup Home Alone ra đủ 6 mảnh đúng kích thước theo cả 2 chế độ.
- [ ] 8. Google Drive + Sheet (OAuth, tạo cây thư mục, ghi dòng) → Verify: duyệt 1 job thì thấy thư mục và dòng mới trong Sheet.
- [ ] 9. Kiểm tra tổng: chạy 3 job 2D + 2 job 3D từ đầu tới Drive → Verify: Sheet có 5 dòng, mọi link mở được.

Thứ tự: 1 → 2 → (3, 5 song song) → 4 → 6 → 7 → 8 → 9. Mỗi bước là một PR nhỏ.

## Cần chốt

1. **6 thành phần của 3D là những mảnh nào?** Sweater mẫu có Front, Back, Sleeve, Collar, Hem, Wrist. Hoodie mẫu chỉ có 5 (Front, Back, Hood, Left, Right). Mỗi kiểu dáng có bộ 6 mảnh riêng, hay một bộ chung?
2. **Kích thước xưởng** cho hoodie, áo thun AOP (Codex đang để "chưa xác nhận").
3. **API AI**: giữ OpenAI gpt-image như Codex đã nối, hay thêm Gemini? Ngân sách mỗi tháng?
4. **2D: vùng in 4500 × 5400 cố định** cho cả mặt trước lẫn sau, hay có cỡ khác (in ngực trái, in lưng to)?

## Gợi ý thêm để dự án tốt hơn

- **Hàng loạt**: dán 20 link một lần, chạy đêm, sáng duyệt.
- **Nút "Gửi về POD Studio" trên trình duyệt** (bookmarklet): đang lướt Etsy/Pinterest bấm một cái là vào hàng đợi.
- **Chống trùng**: hash ảnh, báo nếu art đã làm rồi.
- **Mockup bán hàng**: sau khi có art, tự đặt lên áo trơn để có ảnh đăng bán.
- **Tiêu đề, tag**: sinh sẵn cho listing, ghi vào Sheet.
- **Điểm chất lượng**: độ phân giải gốc thấp (dưới 1000 px) thì cảnh báo sớm, khỏi tốn thời gian.
- **Rủi ro bản quyền**: art lấy trên mạng thường có chủ; in bán có thể bị gỡ shop hoặc kiện (Disney, Fox, Capcom rất hay đánh). Sheet có cột "quyền sử dụng" và tool cảnh báo khi thấy logo/nhân vật nổi tiếng. Tool không xóa watermark.
