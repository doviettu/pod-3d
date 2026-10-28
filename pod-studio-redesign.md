# POD Studio v2: thiết kế lại toàn diện

Ngày: 28/10/2026 · Chạy trên Mac mini 16 GB · Dùng cá nhân

## Mục tiêu

Một trang web chạy trên Mac: dán ảnh hoặc link ảnh áo tìm được, nhận lại file in sẵn sản xuất, tự lưu lên Google Drive và ghi vào Sheet quản lý.

- **2D**: art in mặt trước, mặt sau, hoặc cả hai. Tự tìm vùng art, mỗi mặt tách ra một PNG nền trong suốt 4500 × 5400 px, 300 DPI.
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

- **Backend**: máy chủ Python sẵn có của Codex (giữ nguyên phần đăng nhập online đã kiểm thử) + SQLite (nguồn dữ liệu chính). Một worker chạy tuần tự để vừa RAM 16 GB. *Đổi so với bản đầu:* chưa chuyển sang FastAPI vì phải viết lại phần đăng nhập; xét lại nếu máy chủ hiện tại thành nút thắt.
- **Frontend**: HTML + JavaScript thuần, không cần build. Xem ảnh lớn bằng OpenSeadragon (BSD) với ảnh lát DZI tạo bởi pyvips: zoom tới từng pixel của file 6000 px mà không lag.
- **Truy cập từ xa**: giữ Cloudflare tunnel + đăng nhập của Codex.

## Luồng 2D

1. **Nạp**: tải file, dán ảnh (Ctrl+V), hoặc dán link. Link được tải về máy, kiểm tra loại file, cỡ tối đa 32 MB.
2. **Tìm vùng art**: tách áo khỏi nền (BiRefNet qua `rembg`, MIT), rồi trong áo tìm vùng khác màu vải. Hiện khung đề xuất, người dùng kéo chỉnh được.
   - Chọn **Trước / Sau / Cả 2 mặt** (mặc định: tự đoán). Ảnh có 2 áo cạnh nhau (trước + sau) thì tự nhận 2 khung và gắn nhãn mặt; người dùng đổi nhãn nếu sai.
   - Mặt trước và sau nằm ở 2 ảnh riêng thì nạp cả 2 vào cùng một job.
   - Các bước dưới chạy riêng cho từng mặt, chung một SKU.
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
1. Chạy Local trước (miễn phí), chấm điểm từng mảnh.
2. Một lần gọi model đọc ảnh rẻ để lập **bản mô tả thiết kế** (bảng màu, hoạ tiết, dải lặp, chữ). Bản này dùng chung cho mọi mảnh.
3. Gọi gpt-image **một lần cho cả bộ**: xin một tấm "sheet" xếp sẵn các mảnh theo lưới cố định, code tự cắt ra. Mảnh nào Local đã đạt thì không gửi.
4. Code local phóng to, ghép lên khuôn đúng kích thước, DPI. AI không quyết định đường cắt.

### Tối ưu chi phí API

Nguyên tắc: **mỗi ảnh nguồn chỉ trả tiền một lần, và chỉ trả cho phần Local làm không được.**

- **Không gọi lặp**: khóa cache = hash ảnh nguồn + hash prompt + model + cấu hình. Trùng khóa thì lấy kết quả cũ, kể cả khi bấm chạy lại. Không tự thử lại khi lỗi; nút "Thử lại" phải bấm tay và hiện giá.
- **Gộp lệnh**: 1 lệnh sheet thay cho 6 lệnh từng mảnh. Sửa một chỗ thì dùng phục hồi theo vùng có mask (đã có `masked_repair.py`), không sinh lại cả mảnh.
- **Gửi ít, nhận ít**: cắt ảnh nguồn sát vùng art, thu về cạnh 1024 px; xin ảnh 1K, chất lượng `medium`. Phóng to bằng Real-ESRGAN trên máy, không trả tiền cho độ phân giải.
- **Thử rẻ trước**: kiểu dáng mới chạy `low` một lần để kiểm tra bố cục, đạt rồi mới chạy `medium`.
- **Hàng loạt ban đêm**: dùng Batch API (giảm khoảng 50%) nếu model ảnh hỗ trợ; nếu không thì chạy tuần tự bình thường.
- **Trần ngân sách**: đặt mức tối đa mỗi ngày/tháng. Trước khi chạy hiện ước tính; vượt trần thì dừng và hỏi. Ghi token và tiền thực tế của từng lệnh vào Sheet.

### Sinh prompt hiệu quả

Prompt không viết tay mỗi lần mà **ghép từ khuôn**, để giống hệt nhau giữa các lần chạy (cache trúng) và dễ đo:

```
[Phần cố định]  vai trò + luật chung: ảnh phẳng, bỏ nếp vải/bóng/ánh sáng,
                không chữ/logo (dựng lớp riêng), không bịa phần khuất ngoài mô tả
[Theo kiểu dáng] lưới sheet, tên và tỉ lệ từng mảnh, hướng hoạ tiết
                (lấy từ thư viện kiểu dáng, ví dụ tay áo = dải ngang)
[Theo thiết kế]  bản mô tả JSON: màu HEX, hoạ tiết, chu kỳ dải lặp, nền
[Theo lần chạy] chỉ những mảnh cần làm + ghi chú của bạn (tối đa vài dòng)
```

- Ngắn, liệt kê, có số đo cụ thể; tránh câu mơ hồ kiểu "đẹp, chất lượng cao".
- Mỗi kiểu dáng lưu 1–2 ví dụ đạt và lỗi thường gặp, đưa vào phần luật.
- Mỗi lệnh ghi lại phiên bản prompt + điểm chất lượng; prompt nào cho điểm cao thì thành mặc định. Theo dõi trên tab **Prompt** của Sheet.

**Thư viện kiểu dáng** (trang riêng): mỗi kiểu gồm tên, bộ mảnh riêng và kích thước (mảnh chính tối thiểu 4500 × 5400), ảnh tham chiếu, các map đã hiệu chỉnh theo góc chụp, trạng thái "đã duyệt in thật".

**Tăng chất lượng Local**: các kỹ thuật bổ sung (tìm ảnh gốc lớn hơn, dựng lại lưới mũi len/pixel, khử vân len bằng FFT, vector hoá lai, bộ test vàng…) ở [docs/local-quality.md](docs/local-quality.md).

## Xem kết quả

- Lưới thẻ: ảnh gốc, từng mảnh, bản ghép lên áo.
- Nút **Mở rộng** mở toàn màn hình; cuộn để zoom, kéo để di chuyển, phím 1 = 100% pixel.
- Thanh trượt so sánh ảnh gốc với kết quả; nền ca rô để thấy vùng trong suốt.
- Đánh dấu màu vùng AI tự dựng hoặc phần khuất (đã có `local-quality.json`).

## Google Drive và Sheet

Đăng nhập Google bằng OAuth (ứng dụng desktop, chỉ scope `drive.file`: chỉ đụng tới file do tool tạo; Sheets API nhận scope này cho Sheet do tool tạo, nên không cần `spreadsheets`).

```
POD Studio/
├─ 2D/2026-10/<SKU>/  source.jpg · front.png · back.png · *.svg · preview.jpg
├─ 3D/2026-10/<SKU>/  source.jpg · panels/*.png · panels.zip · preview.jpg
├─ ⚠ Bản quyền/      job bị gắn Đỏ
├─ Thư viện kiểu dáng/
└─ POD Quản lý (Sheet)
```

Sheet tự tạo các tab:
- **Jobs**: SKU, ngày, loại 2D/3D, mặt in (trước/sau/cả 2), chế độ, link nguồn, kiểu dáng, trạng thái, ảnh nhỏ `=IMAGE()`, link thư mục, chi phí AI, bản quyền (Xanh/Vàng/Đỏ) + lý do, ghi chú.
- **Kiểu dáng**: danh sách thư viện và kích thước mảnh.
- **Bản quyền**: danh sách từ khoá thương hiệu + danh sách đen (hash art đã gắn Đỏ).
- **Prompt**: phiên bản prompt, số lần dùng, điểm chất lượng trung bình, chi phí trung bình.
- **Cài đặt**: quy tắc đặt SKU, lề an toàn, trần ngân sách AI.

SQLite vẫn là gốc; Sheet là bản sao để xem và lọc. Đồng bộ chạy khi bấm Duyệt, lỗi mạng thì xếp hàng thử lại.

## Kiểm tra bản quyền

Chạy tự động cho mọi job, ngay sau bước tìm vùng art, trước khi tốn công phóng to hay gọi AI tạo ảnh.

**Các lớp kiểm tra** (rẻ trước, đắt sau):
1. **Danh sách đen nội bộ**: hash cảm quan (pHash) của các art đã bị đánh dấu đỏ. Trùng thì gắn đỏ ngay, không tốn gì.
2. **Đọc chữ trên art** (OCR local, ví dụ PaddleOCR/Tesseract), so với danh sách từ khoá thương hiệu: Disney, Marvel, NFL, NBA, Pokémon, tên phim, ca sĩ… Danh sách nằm trong tab **Bản quyền** của Sheet, bạn tự thêm.
3. **AI nhận diện**: gộp luôn vào lệnh đọc ảnh lập bản mô tả thiết kế (không thêm lệnh). Hỏi thêm: có logo, nhân vật, người nổi tiếng, thương hiệu, câu khẩu hiệu nổi tiếng không, là gì. Ở chế độ Local không bật AI thì bỏ qua lớp này.
4. **Tra ngược ảnh**: nút mở Google Lens / TinEye với ảnh art để bạn xem nguồn gốc (miễn phí, bấm tay).
5. **Nhãn hiệu chữ**: với art chủ yếu là chữ, nút mở tra cứu USPTO cho câu chữ đó.

**Đánh dấu**

| Mức | Khi nào | Tool làm gì |
| --- | --- | --- |
| 🟢 Xanh | Không thấy dấu hiệu, hoặc bạn xác nhận art tự thiết kế / đã mua quyền | Xử lý và xuất bình thường |
| 🟡 Vàng | Có từ khoá hoặc hình nghi ngờ | Vẫn xử lý; nút Duyệt yêu cầu bạn chọn Xanh hoặc Đỏ kèm lý do |
| 🔴 Đỏ | Trùng danh sách đen, hoặc thương hiệu/nhân vật rõ ràng | Huy hiệu đỏ trên thẻ kết quả, file xuất có hậu tố `_IP`, lưu vào thư mục Drive riêng `⚠ Bản quyền/`, không đồng bộ sang mục sẵn bán. Muốn xuất vẫn được nhưng phải bấm xác nhận |

- Mỗi job lưu bằng chứng: chữ đọc được, tên thương hiệu AI thấy, ảnh trùng, người đổi mức và lý do.
- Sheet thêm cột **Bản quyền** (màu theo mức) + **Lý do**, và tab **Bản quyền** chứa danh sách từ khoá + danh sách đen.
- Bạn đổi mức bằng tay bất cứ lúc nào. Art đổi sang Đỏ được thêm hash vào danh sách đen để lần sau tự bắt.
- Đây là bộ lọc hỗ trợ, không phải ý kiến pháp lý: Xanh nghĩa là "chưa thấy dấu hiệu", không đảm bảo an toàn.

## Việc cần làm

- [x] 1. Đưa `pod-studio` vào repo (`studio/`), loại `runtime/`, `.vendor/`, `node_modules/`, khóa API → Verify: `git status` sạch, test cũ của Codex vẫn chạy.
- [x] 2. SQLite jobs + hàng đợi chung trên máy chủ sẵn có (chưa FastAPI) → Verify: `curl 127.0.0.1:8765/api/status` trả 200, job cũ hiện trong lịch sử.
- [x] 3. Trang chính 2 tab 2D / 3D, ô nạp ảnh/link/dán → Verify: dán link ảnh, thấy ảnh xem trước.
- [x] 4. Pipeline 2D (tìm vùng, phẳng, bỏ nền, phóng to, canvas 4500 × 5400) → Verify: 5 ảnh mẫu ra PNG đúng cỡ, có alpha, 300 DPI; 1 ảnh trước + sau cạnh nhau ra đủ `front.png` và `back.png`.
- [~] 4b. (Đã có bộ test vàng + mốc + kỹ thuật 1) Bộ test vàng + 5 kỹ thuật ưu tiên trong `docs/local-quality.md` → Verify: bảng điểm SSIM/ΔE trước-sau trên 3 bộ mẫu, chỉ bật kỹ thuật tăng điểm.
- [x] 5. Trình xem OpenSeadragon + so sánh trước/sau → Verify: zoom tới pixel trên mảnh 6048 × 6559 mượt.
- [x] 6. Thư viện kiểu dáng + nhận dáng tự động → Verify: ảnh sweater/hoodie mẫu được nhận đúng kiểu, ảnh lạ hỏi chọn tay. (Có thêm: tạo khuôn mới trên trang bằng 4 góc mỗi mảnh, tab Thư viện.)
- [x] 7. Nối 3D Local và 3D AI vào luồng mới (khuôn prompt, cache, sheet 1 lệnh, trần ngân sách) → Verify: mockup Home Alone ra đủ 6 mảnh theo cả 2 chế độ; chạy AI lần 2 cùng ảnh không phát sinh lệnh mới. (Đã làm: sheet 1 lệnh, cache theo hash, tự chọn mảnh Local yếu, trần ngày/tháng + sổ chi tiêu, prompt ghép khuôn có phiên bản. Chưa làm: lệnh mô tả thiết kế bằng model đọc ảnh, chạy thử `low` trước, Batch API.)
- [x] 8. Google Drive + Sheet (OAuth, tạo cây thư mục, ghi dòng) → Verify: duyệt 1 job thì thấy thư mục và dòng mới trong Sheet. (Đã làm: OAuth Desktop + PKCE chỉ quyền `drive.file`, cây thư mục theo tháng, job Đỏ vào `⚠ Bản quyền/` với hậu tố `_IP`, tải lên theo đoạn 8 MB, Sheet 5 tab, dòng ghi đè theo SKU, hàng đợi thử lại, nút Đồng bộ lại. Kiểm bằng Google giả lập trong test; chờ bạn tạo client OAuth để chạy thật. Không dùng `=IMAGE()` vì phải công khai file; cột Xem trước là link.)
- [x] 8b. Kiểm tra bản quyền (pHash, OCR + từ khoá, gộp vào lệnh AI mô tả, nhãn Xanh/Vàng/Đỏ, thư mục và cột Sheet) → Verify: mockup Home Alone bị gắn Đỏ (Disney/Fox); một art chữ tự viết ra Xanh; đổi một job sang Đỏ thì ảnh trùng lần sau tự Đỏ. (Đã làm: pHash + danh sách đen, từ khoá thương hiệu trong link/tên file và chữ trên art (OCR Tesseract, tuỳ chọn), nhãn Xanh/Vàng/Đỏ trên thẻ job, nút Google Lens/TinEye/USPTO, sửa từ khoá trong tab Lịch sử. Mockup Home Alone chỉ tự Vàng/Đỏ khi link/tên/chữ có từ khoá. Chưa làm: lớp AI nhận logo/nhân vật (đợi lệnh mô tả thiết kế), thư mục và cột Sheet (bước 8).)
- [~] 9. Kiểm tra tổng: chạy 3 job 2D + 2 job 3D từ đầu tới Drive → Verify: Sheet có 5 dòng, mọi link mở được. (28/10: chạy trên máy chủ thật với Google giả lập: 5 dòng, 0 link hỏng, job Đỏ vào `⚠ Bản quyền/` có `_IP`. Còn chạy lại với Google thật sau khi bạn kết nối. Phát hiện và sửa: khuôn `hoodie` cũ thiếu bản đồ mũ nhưng vẫn được tự chọn, nay tự nhận dáng bỏ qua khuôn chưa đủ dữ liệu.)

Thứ tự: 1 → 2 → (3, 5 song song) → 4 → 4b → 6 → 7 → 8b → 8 → 9 (8b làm trước vì 8 cần bạn bật quyền Google). Mỗi bước là một PR nhỏ.

## Đã chốt (28/10)

1. **Mỗi kiểu dáng có bộ mảnh riêng** (sweater 6 mảnh, hoodie theo bộ của hoodie…), khai báo trong thư viện kiểu dáng.
2. **Chuẩn kích thước**: mảnh chính (thân, tay, mũ) tối thiểu 4500 × 5400 px, 300 DPI; lớn hơn vẫn đạt. Mảnh hẹp (cổ, gấu, bo tay) giữ đúng tỉ lệ khuôn ở 300 DPI. Kiểu dáng có file mẫu xưởng thì theo file mẫu.
3. **AI**: chỉ dùng OpenAI (gpt-image), bỏ Gemini.
4. **2D**: canvas cố định 4500 × 5400 cho cả mặt trước và mặt sau.

## Gợi ý thêm để dự án tốt hơn

- **Hàng loạt**: dán 20 link một lần, chạy đêm, sáng duyệt.
- **Nút "Gửi về POD Studio" trên trình duyệt** (bookmarklet): đang lướt Etsy/Pinterest bấm một cái là vào hàng đợi.
- **Chống trùng**: hash ảnh, báo nếu art đã làm rồi.
- **Mockup bán hàng**: sau khi có art, tự đặt lên áo trơn để có ảnh đăng bán.
- **Tiêu đề, tag**: sinh sẵn cho listing, ghi vào Sheet.
- **Điểm chất lượng**: độ phân giải gốc thấp (dưới 1000 px) thì cảnh báo sớm, khỏi tốn thời gian.
- **Rủi ro bản quyền**: đã chốt thành mục "Kiểm tra bản quyền" ở trên. Tool không xóa watermark.
