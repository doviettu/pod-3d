# POD Studio trên Mac

## Giao diện v2 (mặc định)

Mở `http://127.0.0.1:8765` sau khi chạy `studio/start.command`.

- **2D · In 1–2 mặt**: kéo thả, dán (⌘V) hoặc dán link ảnh áo. Link Etsy/Shopify/Amazon/Pinterest tự đổi về bản ảnh lớn nhất. Tool tự tìm áo và vùng art, hiện khung Trước/Sau; kéo trên ảnh để vẽ lại khung. Kết quả mỗi mặt là PNG 4500 × 5400 px, 300 DPI, nền trong suốt (`runtime/v2-jobs/<mã>/front.png`, `back.png`).
- **3D · In tràn**: nạp mockup, tool so đường viền áo với các khuôn trong thư viện. Trùng khuôn thì chọn luôn. Ảnh khác thì chọn kiểu dáng và bấm **Tạo khuôn mới từ ảnh này**: kéo 4 góc từng mảnh trên ảnh, lưu xong là trải phẳng được (khuôn mới nằm ở `studio/templates/<kiểu>-<ngày>-<số>/`, mảnh không đánh dấu tô một màu). Chọn Local hoặc AI (OpenAI, có phí), chạy bằng engine `pod3d`. Duyệt chữ/logo và xuất PSD vẫn ở **Công cụ duyệt chi tiết** (`/legacy`).
- **Thư viện kiểu dáng**: các kiểu (sweater, hoodie chui đầu, hoodie khoá kéo, áo thun in tràn), bộ mảnh và cỡ mảnh của từng kiểu, các khuôn kèm ảnh.
- **Lịch sử**: mọi job có mã SKU (`2D-261028-001`), trạng thái, mức bản quyền. Danh sách lưu trong `runtime/studio.db`.
- **Bản quyền**: mỗi job tự gắn Xanh/Vàng/Đỏ (trùng art đã đánh dấu Đỏ, từ khoá thương hiệu trong link/tên file/chữ trên art). Vàng phải chọn Xanh hoặc Đỏ trước khi duyệt. Sửa từ khoá ở tab Lịch sử. Cài `brew install tesseract` để đọc chữ trên art. Đây là bộ lọc hỗ trợ, không phải ý kiến pháp lý.
- **Google Drive + Sheet** (tab Lịch sử): bấm Duyệt là file lên `POD Studio/2D|3D/<tháng>/<SKU>/` (job Đỏ vào `⚠ Bản quyền/`, tên file có `_IP`) và dòng của job ghi vào Sheet `POD Quản lý` (tab Jobs, Kiểu dáng, Bản quyền, Prompt, Cài đặt). Lỗi mạng thì tự thử lại sau 1, 5, 30 phút, 2 giờ. Cài một lần:
  1. [Google Cloud](https://console.cloud.google.com/): tạo project, bật **Google Drive API** và **Google Sheets API**.
  2. **OAuth consent screen**: External, thêm email của bạn, bấm **Publish app**. Tool chỉ xin quyền `drive.file` (chỉ thấy file do tool tạo) nên không cần Google duyệt; để ở Testing thì 7 ngày phải kết nối lại.
  3. **Credentials → OAuth client ID → Desktop app**, tải file JSON, dán vào ô trong tab Lịch sử, bấm **Kết nối Google**.
  Khoá và token lưu ở `~/.config/pod-studio/google.json` (quyền 600), không nằm trong repo.
- Nút **Mở rộng** mở trình xem zoom tới từng pixel (phím `1` = 100%, `0` = vừa khung), đổi nền ca rô/trắng/tối để soi vùng trong suốt.
- Cùng ảnh, cùng tuỳ chọn thì không chạy lại mà hiện kết quả cũ.

Mã nguồn: `app.html`, `static/app.js`, `v2.py` (dịch vụ), `art2d.py` (pipeline 2D), `styles.py` (thư viện kiểu dáng, nhận dáng, tạo khuôn), `sources.py` (tải ảnh/link), `jobstore.py` (SQLite), `rights.py` (bản quyền), `gdrive.py` (Drive + Sheet), `ai_sheet.py` (AI 1 lệnh, cache, ngân sách). Test: `.venv/bin/python studio/test_v2.py`, `test_styles.py`, `test_rights.py`, `test_gdrive.py`, `test_ai_sheet.py` (không gọi mạng, không tốn phí).

## Công cụ cũ (Codex)

Giao diện local cho engine `pod3d` ở thư mục gốc repo. Chạy các lệnh dưới từ thư mục gốc repo.
Ứng dụng dùng lại template, model và mã hiện có; không sửa dữ liệu mẫu hoặc kết quả cũ.
Máy chủ dùng Python chuẩn; xử lý ảnh dùng môi trường `.venv` hiện có. Công cụ hậu kỳ được cài riêng trong workspace, không thay môi trường engine gốc.

## Mở ứng dụng

Trong Terminal:

```zsh
.venv/bin/python "studio/studio.py"
```

Mở `http://127.0.0.1:8765` trong trình duyệt. Có thể mở `start.command` để chạy cùng lệnh.
Giữ Terminal và Mac hoạt động trong lúc xử lý. Nhấn Control+C để kết thúc sau khi công việc hiện tại xong.
Máy chủ chỉ lắng nghe trên máy này, không xuất bản ra Internet.

## Chạy nền, tự khởi động lại và mail link

Không cần mở Terminal mỗi lần: cài một lần, POD Studio tự chạy khi Mac đăng nhập và tự chạy lại sau ~30 giây nếu bị tắt hoặc lỗi.

1. Bấm đúp `studio/setup-mail.command` (tuỳ chọn): nhập Gmail gửi đi, [mật khẩu ứng dụng Google](https://myaccount.google.com/apppasswords) (cần bật xác minh 2 bước) và email nhận. Tool gửi một mail thử. Cài đặt lưu ở `~/.config/pod-studio/mail.json` (quyền 600), không nằm trong repo.
2. Tắt cửa sổ POD Studio đang chạy bằng tay (Control+C), rồi bấm đúp `studio/install-service.command`. Lệnh này cài 2 dịch vụ launchd: `com.podstudio.server` (ứng dụng) và `com.podstudio.online` (đường truyền HTTPS + đăng nhập; cần `caddy`, `cloudflared`). Chỉ muốn chạy local: `.venv/bin/python studio/service.py install --no-online`.
3. Mỗi lần đường truyền có link mới (khởi động lại, mất mạng, Mac khởi động), tool gửi mail gồm link, tài khoản `studio` và mật khẩu. Không đổi link thì không gửi lại.
4. Lệnh cài cũng tạo app **POD Studio** có icon ở `~/Applications/POD Studio.app`: kéo vào Dock hoặc Desktop, bấm là mở trang (tự bật dịch vụ nếu đang tắt). Tạo lại app: `.venv/bin/python studio/service.py app`.
5. Sau `git pull`: bấm đúp `studio/restart.command`.
6. Xem trạng thái: `.venv/bin/python studio/service.py status`; log ở `studio/runtime/logs/`. Gỡ: `.venv/bin/python studio/service.py uninstall`.

Để chạy cả khi mất điện: System Settings → Users & Groups → bật tự đăng nhập; Energy → bật "Start up automatically after a power failure" và chặn Mac ngủ. Dịch vụ launchd chỉ chạy khi tài khoản đã đăng nhập.

Mail có mật khẩu đăng nhập: ai đọc được mail đều dùng được toàn bộ tool, kể cả lệnh AI có phí. Chọn "n" ở bước 1 nếu chỉ muốn nhận link.

## Truy cập online bằng Mac mini

Đã chuẩn bị `start-online.command` / `online.py`: Cloudflare Tunnel cung cấp HTTPS;
Caddy chuyển yêu cầu tới biểu mẫu đăng nhập của ứng dụng; API và tệp kết quả
được bảo vệ bằng phiên đăng nhập. Bộ xử lý
vẫn chạy trên Mac, dữ liệu và OpenAI key vẫn dùng cấu hình trên Mac. Lưu lượng web
đi qua Cloudflare. Không mở cổng router và không đổi máy chủ gốc khỏi localhost.

1. Mở POD Studio local trước.
2. Cần `caddy` và `cloudflared` (đã cài bằng Homebrew). Nếu điều khiển qua Codex
   có lean-ctx chặn, chủ máy cần tự chạy `lean-ctx allow caddy` và
   `lean-ctx allow cloudflared` trong Terminal rồi mới tiếp tục.
3. Mở `start-online.command`. Khi đã xác minh HTTPS thực tế, chương trình in URL.
   URL và trạng thái cũng ở `runtime/online/status.json`.
4. Đăng nhập với tài khoản `studio`; mật khẩu ngẫu nhiên trong
   `runtime/online/access.json` (quyền 0600, thư mục 0700). Không đưa tệp này lên Git
   hoặc chia sẻ. Một người có mật khẩu có toàn quyền ứng dụng, gồm cấu hình và tác vụ có phí.
   Mở URL bằng trình duyệt, gồm trình duyệt tích hợp Codex, rồi đăng nhập trên trang.
   Phiên có thời hạn 12 giờ; nút “Đăng xuất” kết thúc phiên trên trình duyệt đang dùng.
5. Giữ Mac bật, có mạng và cả hai chương trình đang chạy. Control+C trong cửa sổ
   online đóng đường truyền; ứng dụng local vẫn giữ nguyên.

Mỗi lần khởi động có URL `trycloudflare.com` mới, mật khẩu được giữ lại.
Đây là đường dẫn chạy thử online, chưa phải hosting 24/7 hoặc tên miền cố định.
Muốn URL cố định cần một Cloudflare named tunnel cùng tên miền/tài khoản của chủ máy.
Không tự đăng ký dịch vụ trả phí. Nguồn: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
và [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

Cổng trung gian chỉ bind `127.0.0.1:8766`; từ chối Host/Origin lạ và luôn đánh dấu
truy cập online để ứng dụng kiểm tra phiên. Cookie được ký, có Secure, HttpOnly,
SameSite=Strict và chỉ dùng trên đúng tên miền. Chưa đăng nhập: trang chủ chuyển
đến `/login`, API và tệp riêng trả 401. Đường truyền chỉ được đánh dấu sẵn sàng
sau kiểm tra API qua HTTPS công khai trả 401 khi chưa đăng nhập và 200 khi hợp lệ.
Đã kiểm tra biểu mẫu đăng nhập, API, ảnh xem trước và đăng xuất qua HTTPS thực tế;
kết quả ở `runtime/online/form-verification.json`. Kiểm tra tải PSD 42 MB trước đó
có SHA-256 khớp bản gốc, lưu ở `runtime/online/verification.json`.
Không gọi tạo ảnh trả phí trong các kiểm tra này.

## Hỏi Claude (tab trên trang v2)

Tab **Hỏi Claude** gửi câu hỏi tới Claude Code chạy trên chính Mac mini (`claude -p`) và hiện câu trả lời dần dần trên trang, cả khi mở qua link online (đã qua bước đăng nhập). Gửi kèm ảnh bằng nút 📎 hoặc dán (Cmd+V); ảnh và file Claude tạo ra hiện ngay trong khung chat.

Cài một lần trên Mac:

```bash
curl -fsSL https://claude.ai/install.sh | bash   # cài Claude Code
~/.local/bin/claude                              # gõ /login, chọn tài khoản Claude Pro/Max, rồi /exit
~/pod-3d/studio/restart.command
```

Ba chế độ, chọn cho từng câu hỏi:

| Chế độ | Claude được làm gì | Dùng ở đâu |
|---|---|---|
| Chỉ đọc | đọc và tìm file trong repo | mọi nơi |
| Tạo ảnh (mặc định) | thêm 4 công cụ Studio: `art2d` tách art (miễn phí), `resize` đổi kích thước/DPI (miễn phí), `ai_image` tạo/sửa ảnh OpenAI (tốn phí, chung trần ngân sách `runtime/ai-budget.json`), `budget`; chỉ ghi file vào thư mục kết quả của cuộc trò chuyện. Không có shell. | mọi nơi, kể cả link online |
| Toàn quyền | như Claude Code trong Terminal: chạy lệnh, sửa file, cài gói | chỉ khi mở Studio ngay trên Mac (`127.0.0.1`) |

- Chỉ đọc và Tạo ảnh chạy với `claude --restricted`: không có công cụ chạy lệnh, file chỉ trong repo, không đọc `runtime/online/`.
- Chi phí: Studio xoá `ANTHROPIC_API_KEY` khỏi môi trường của Claude, nên câu hỏi tính vào hạn mức gói Claude, không phát sinh tiền API. Chỉ `ai_image` tốn tiền OpenAI.
- Mọi câu hỏi được ghi vào `runtime/logs/claude-chat.jsonl` (giờ, chế độ, online hay không, nội dung). Ảnh gửi kèm ở `runtime/claude/<mã>/in/`, kết quả ở `runtime/claude/<mã>/out/`.
- Mỗi lần một câu hỏi; tối đa 8000 ký tự, 4 ảnh x 15 MB, 15 phút. **Cuộc trò chuyện mới** bỏ ngữ cảnh cũ.

## Mẫu sản phẩm và nhận diện tự động

Các bố cục mới trong thư mục `templates/` của ứng dụng, tách khỏi template gốc của engine:
- Hoodie Halloween: mặt sau trái / mặt trước phải; tay đen. Front, Back, Hood, Left, Right.
- Áo thun Stepback: chỉ Back, canvas làm việc 384×568 px ở 72 DPI; chưa có kích thước in xưởng.
- Hoodie bí ngô họa tiết: mặt trước trái / mặt sau phải, nền phòng. Front, Back, Hood, Left, Right.

Các mẫu mới là bản nháp hình học; hoodie dùng kích thước canvas hoodie có sẵn để thử, chưa xác nhận cho sản phẩm mới. Cho phép xử lý/duyệt/xuất PSD nháp nhưng chặn ZIP sản xuất đến khi xác nhận chuẩn xưởng. Không tự tạo mặt trước áo thun. Mỗi mẫu lưu ảnh tham chiếu, bản đồ và silhouette riêng; lịch sử và template gốc không bị ghi đè.

Khi tải ảnh lên, web gọi `POST /api/detect-template` để đối chiếu trên Mac: SHA-256 chính xác, ảnh gần trùng sau nén/resize, hoặc hình dáng nền trắng khi đủ bằng chứng. Đây là chọn bố cục, không phải truy xuất artwork gốc; thư viện gốc vẫn cần SHA-256 chính xác. Điểm so khớp không phải xác suất nhận dạng. Ảnh mơ hồ/đảo hướng/nền phức tạp khác biệt yêu cầu chọn thủ công. Lựa chọn thủ công luôn hủy kết quả tự động đang chờ.

Script độc lập, không có phí API:
```zsh
.venv/bin/python "studio/template_detection.py" "/Users/doviettu/Downloads/images.jpeg" --references "studio/templates/detection-references.json"
```

## Thư viện hoodie trên Shopify

`hoodie_market_library.json` lưu kiểu dáng, thương hiệu, trang sản phẩm chính hãng, nguồn xác minh Shopify và URL ảnh quan sát được. Giao diện có lọc hãng/kiểu và tìm kiếm cục bộ; ảnh từ hãng được tải trực tiếp với no-referrer, không sao chép về máy. Mẫu bán lẻ chỉ tham khảo cấu tạo, không có rập hay kích thước in xác nhận. Không đưa ảnh thị trường vào bộ tự chọn bố cục đã hiệu chuẩn.

## Thêm OpenAI Images API

1. Trong **Cài đặt OpenAI**, nhập OpenAI API key. Không gửi khóa trong chat.
2. Chọn `gpt-image-2.5-flare` (mặc định) hoặc `gpt-image-2.5-sunburst`, kích thước `1K` (mặc định) hoặc `2K`, chất lượng `medium` (mặc định), `low` hoặc `high`, rồi bấm lưu.
3. Bấm **Kiểm tra kết nối**. Bước này chỉ đọc thông tin model, chưa tạo ảnh.
4. Khi tạo công việc, bật OpenAI và chọn Front, Back, Sleeve hoặc kết hợp cả ba. Nút chạy ghi rõ số panel có phí.

Khóa được lưu ngoài thư mục mã nguồn, tại:
`~/.config/pod-studio/openai-settings.json`, thư mục riêng 0700 và tệp 0600.
API/status, báo cáo, nhật ký và trình duyệt không nhận lại giá trị khóa đã lưu.
Ứng dụng cũng nhận `OPENAI_API_KEY` nếu chưa có khóa lưu riêng.
Khóa lưu riêng được ưu tiên. Ô khóa để trống khi lưu giữ nguyên khóa hiện có; ô nhập được xóa sau khi lưu, kể cả khi lỗi.
Không lưu khóa hoặc cài đặt vào localStorage. Tải trang và lưu cài đặt không gọi OpenAI; kiểm tra kết nối chỉ chạy khi bấm nút.
Cài đặt Gemini cũ tại `~/.config/pod-studio/settings.json` và lịch sử cũ được giữ nguyên. Job Gemini cũ vẫn hiển thị nhãn Gemini.

Mỗi mảnh đã chọn tương ứng một yêu cầu tạo/chỉnh ảnh, trừ khi tìm được bộ artwork gốc khớp chính xác và bỏ qua API. Nút phục hồi một vùng gửi đúng một yêu cầu riêng. Không tự thử lại nếu lỗi hoặc hết thời gian.
OpenAI tính phí theo token ảnh và văn bản; ứng dụng không báo giá cố định mỗi ảnh (`estimated_image_cost_usd: null`).
Cần tài khoản API có thanh toán và quyền dùng model; đọc được thông tin model chưa xác nhận hạn mức tạo ảnh.
Máy chủ gửi JSON tới `/v1/images/edits` với hai ảnh trong `images`: mockup gốc và hướng dẫn tỉ lệ/canvas, cùng `prompt`, `model`, `quality`, `size` và `n: 1`. Không gửi bản trải lỗi làm nguồn artwork.
Nguồn chính thức: [chỉnh sửa ảnh](https://developers.openai.com/api/reference/resources/images/methods/edit) và [bảng giá](https://developers.openai.com/api/docs/pricing).

## Tạo file in

1. Tải lên một mockup JPEG, PNG hoặc WebP, tối đa 32 MB và 50 triệu pixel.
2. Chọn template đúng dáng và góc chụp. Khuôn thiếu bản đồ hoặc silhouette bị vô hiệu hóa.
3. Chọn upscale local nếu cần. Với OpenAI, chỉ nâng nét artwork sau phục hồi; bản local đối chiếu bỏ upscale để không phóng lớn lỗi cũ. Chế độ local mặc định không gọi OpenAI.
4. Bấm chạy. Hệ thống xử lý tuần tự một công việc để tránh chạy nhiều model cùng lúc trên RAM 16 GB.
5. Kiểm tra mockup gốc, các bản nháp local và OpenAI riêng (`openai.zip`). Trạng thái xử lý xong luôn chờ duyệt, kể cả công việc local cũ.

Mỗi công việc nằm trong `runtime/jobs/<mã>/`; không ghi đè nguồn hoặc đầu ra của công việc khác.
Phải đủ tất cả mảnh và đúng kích thước khuôn mới báo xử lý xong. Đây là kiểm tra tệp, không xác nhận chất lượng artwork. Ảnh là PNG, giữ DPI của template.
ZIP chỉ đóng gói tên mảnh trong khuôn; tự bỏ qua tệp phụ `._...` mà macOS tạo trên ổ ngoài.
Nếu OpenAI lỗi, bản local đã hoàn tất vẫn tải được; công việc được đánh dấu thất bại, không báo sai là hoàn tất.
Khi khởi động lại, công việc đang dở được đánh dấu gián đoạn; ứng dụng không tự gửi lại yêu cầu có phí.

Luồng OpenAI mới (`shared-artwork-v2`, giữ tương thích lịch sử `artwork-first-v1`): từ mockup gốc phục hồi riêng Front / Back / Sleeve thành artwork 2D, lưu `artwork/<panel>.png` ở độ phân giải model; nâng nét local nếu đã chọn; sau đó ghép lên canvas cố định và lưu `openai/<panel>.png`. Hướng dẫn riêng Sleeve yêu cầu các dải hoa văn ngang, tránh kéo vùng khuất thành sọc dọc. Ghi chú thiết kế tối đa 2000 ký tự được gửi cùng prompt. Cách này vẫn cần thử ảnh thật và duyệt thủ công, không bảo đảm khôi phục chính xác phần bị che.
Prompt yêu cầu bỏ chữ / số / logo, giữ chỗ trống trên nền để dựng chữ và logo thành lớp riêng. Người dùng phải đối chiếu font và bổ sung các lớp này; hệ thống chưa tự nhận diện font hoặc xác nhận AI đã bỏ hết chữ/logo. Canvas 300 DPI sau resize không đồng nghĩa có chi tiết gốc 300 DPI.
Luôn đối chiếu chữ, logo, chân dung, vị trí hoa văn và phần áo bị che trước khi in. Bản local được giữ riêng để so sánh.
Luồng offline mới loại nền trắng nối ra biên trước khi lấy dải tay; dùng một dải nguồn/pha cố định, không đổi theo từng khối hàng. Dải lặp đã khai báo và duyệt trong mô tả chung được kiểm tra ít nhất hai chu kỳ và sai số trước khi kéo dài. Pixel vùng được bản đồ coi là nhìn thấy được giữ lại trước/sau upscale. Bản đồ không xác nhận đúng ngữ nghĩa chữ; vẫn cần duyệt. Vùng còn thiếu được đánh dấu xám, không dùng LaMa để che mất bằng chứng thiếu dữ liệu. Báo cáo `local-quality.json` phân biệt vùng nhìn thấy, suy luận và chưa phục hồi.
Có bản xem trên áo 2D dựa vào bản đồ hiệu chuẩn; chưa có dựng áo 3D hoặc trình sửa template. Codex điều phối trong chat; web này không tự gọi subagent nội bộ.

## Thư viện mẫu, mô tả chung và phục hồi một vùng

- **Ưu tiên bộ artwork gốc** mặc định bật. Chỉ khớp SHA-256 mockup, đủ panel và đúng kích thước mới lấy bộ gốc từ thư viện samples của engine. Đầu ra PNG giữ nguyên pixel giải mã và DPI mẫu; báo cáo ghi rõ `source-original`, không gọi API hoặc nhận là suy ngược thành công. Tắt tra cứu khi đánh giá thuật toán.
- **Mô tả thiết kế chung** tạo trước khi gửi ảnh, không gọi API. Có thể nạp JSON với palette, motifs, bands, text_layers và unknowns. Bounds chuẩn hóa theo panel; period/phase có axis và đo trên canvas phẳng. Không tự suy ra chữ, font, phần khuất hay số đo từ một ảnh. Bản JSON tải xuống có spec/hash và prompt cho từng panel từ cùng dữ liệu; các trường khai báo vẫn cần kiểm chứng.
- Mỗi job lưu `design-plan.json`; prompt/hash được ghim theo model/cấu hình lúc gửi. Prompt bỏ chữ/logo và không tự sao chép Front sang Back hoặc chân dung lên Sleeve.
- Trong vùng tọa độ của **Duyệt chữ & logo**, nút **Phục hồi vùng bằng OpenAI · Có phí** yêu cầu lưu các lớp trước. Ảnh đầu tiên là canvas phẳng với mask, ảnh thứ hai là mockup gốc. Backend khóa pixel ngoài vùng chọn, vùng chữ đã khai báo và các lớp chữ/logo đã lưu; kiểm tra sai khác pixel trước khi lưu PNG. Mask API chỉ hướng dẫn model; compositor local mới khóa thật.
- Phục hồi vùng tạo revision nháp mới, giữ nguyên file nguồn/lịch sử và các lớp, hủy duyệt mảnh vừa sửa. Chữ còn dính trong ảnh nền chưa được tự phát hiện; người dùng phải khoanh tránh hoặc tách riêng. Không tự thử lại yêu cầu có phí.
- Unit test dùng phản hồi API giả; chức năng mask cần một lần kiểm chứng ảnh API thật trước khi đánh giá chất lượng model. Chưa gọi tạo ảnh trả phí để triển khai.

## Duyệt chữ, logo và xuất PSD có lớp

Quy trình: **Mockup → Phục hồi artwork 2D → Duyệt chữ / logo trên canvas khuôn → Xem trên áo → Xuất tệp**.
Mỗi mảnh có bản kiểm tra phẳng và có thể chiếu ngược lên mockup bằng bản đồ đã hiệu chuẩn; chưa phải mô phỏng áo 3D.

1. Mở một công việc đã xử lý xong có ảnh local trong lịch sử. Nếu bước OpenAI thất bại nhưng đã có ảnh local, vẫn có thể duyệt ảnh đó.
2. Trong **Duyệt chữ & logo**, chọn mảnh và nền local hoặc bản OpenAI đã có. Chọn bản OpenAI ở đây không tạo ảnh mới.
3. Thêm tối đa 4 dòng chữ và 4 lớp logo PNG. Nhập X/Y tính từ góc trên bên trái mảnh, cỡ chữ và chiều rộng logo bằng pixel; chọn font và màu chữ. Logo giữ tỉ lệ ảnh.
4. Với ảnh local và công việc OpenAI cũ, chữ / logo cũ vẫn nằm trong nền phẳng. Muốn thay, thêm vùng che màu phẳng hoặc tải nền sạch PNG cùng kích thước mảnh. Với luồng OpenAI mới, kiểm tra nền phục hồi đã bỏ chữ/logo đúng yêu cầu rồi mới thêm các lớp riêng. Không có nhận diện/xóa chữ tự động bảo đảm chính xác.
5. Bấm **Lưu và xem trước**, đối chiếu bản đã lưu với ảnh nền. Chỉnh sửa tiếp sẽ bỏ dấu duyệt; kiểm tra lại rồi đánh dấu xác nhận thủ công và lưu.
6. Bấm **Xuất PSD có lớp** để lưu các thay đổi và xuất PSD của đúng công việc / mảnh đang chọn. Có thể xuất nháp chưa duyệt; chỉ bản đã duyệt mới dùng tên PNG để in. Các tệp tải về ghi rõ nháp hoặc đã duyệt.
7. Duyệt đủ mọi mảnh của template (kể cả cổ, gấu và cổ tay). **Xem trên áo 2D** dùng bản duyệt hiện tại, nếu chưa có thì dùng OpenAI hoặc local và ghi rõ bản nháp. Vùng không có map / không nhìn thấy giữ ảnh mockup gốc và có cảnh báo; trên mockup nền trắng, vùng áo chưa được phủ artwork còn được đánh dấu vạch chéo xám. Không được hiểu là đã phục hồi toàn bộ áo. Bản xem giới hạn mép ảnh để tránh chiếu artwork tràn ra nền trắng.
8. Sau lần duyệt cuối, tạo **Đối chiếu đủ các mảnh** và bản xem trên áo rồi **Xuất bộ đã duyệt**. Bảng đối chiếu gồm hình và mép canvas của mọi mảnh theo kích thước mẫu xưởng; không phải chứng nhận đường may/3D. Backend từ chối khi thiếu duyệt, preview cũ hoặc bảng đối chiếu không khớp phiên bản/hash tệp hiện tại. ZIP chứa đúng PNG đã duyệt cùng `approval.json` ghi phiên bản; lần sửa sau không ghi đè ZIP cũ. Đây là xác nhận thủ công của người dùng, không phải chứng nhận chất lượng tự động.

Các tham số chữ, logo, vùng che, nguồn nền và trạng thái duyệt được lưu trong `review.spec`.
Logo đã lưu được gửi lại bằng `asset_id`; chỉ thay hoặc bỏ khi người dùng yêu cầu. Ảnh nguồn và các đầu ra cũ được giữ riêng, không ghi đè.
Thay đổi chưa lưu được giữ trong bộ nhớ trang khi chuyển công việc / mảnh; tải lại hoặc đóng trang sẽ mất phần chưa lưu. **Bỏ thay đổi chưa lưu** mở lại bản trên máy chủ.
PSD có lớp nền, các lớp chữ và logo theo khả năng bộ xuất local. Để chỉnh chữ native trong Photoshop, cần cài đúng font trên máy; khác font có thể làm đổi bố cục.

### Font và DaFont

Mở **Thư viện font & so sánh DaFont**. Arial (`system-arial`) và Times (`system-times`) có sẵn.
Có thể tải TTF / OTF tối đa 5 MB, kèm đường dẫn trang font trên [DaFont](https://www.dafont.com/) nếu có.
Giấy phép tùy từng tác giả, đặc biệt với sử dụng thương mại. Checkbox giấy phép khi tải font là xác nhận của người dùng.
Thông tin xác nhận được lưu để đối chiếu; ứng dụng không tự xác minh giấy phép font.
Gõ tối đa 120 ký tự và chọn 2–3 font để xem PNG mẫu local rồi tự so sánh với panel. Không có nhận diện hoặc ghép font tự động; không tải font tự động từ DaFont và không gọi API có phí.

### Công cụ local tùy chọn

Nhập vùng **X, Y, Rộng, Cao** theo pixel trên nguồn local / OpenAI đã có:

- **Tách vùng SAM 2** dùng SAM 2 tiny local để trả logo PNG và thêm thành một lớp logo tái sử dụng được. Lưu bản duyệt để giữ lớp vừa thêm; tổng số logo vẫn tối đa 4.
- **Tạo SVG VTracer** ưu tiên logo đã lưu/tách cuối cùng; khi chưa có logo thì dùng vùng đã chọn. Có thể nối bước SAM 2 → VTracer → sửa SVG.

SAM 2 đọc vùng từ ảnh nguồn đã chọn, chưa gồm các lớp mới trong bản duyệt. VTracer có thể đọc PNG logo đã tách. Chúng không tự chạy khi mở trang, không phát sinh phí API; nút tạm khóa khi xử lý.
SAM 2 và VTracer là thành phần tùy chọn do backend quản lý, không cần để nhập chữ / logo thủ công. Máy chủ thông báo bằng tiếng Việt nếu chưa sẵn sàng; `GET /api/status` có `tools.sam2`, `tools.vtracer`, `tools.psd` để kiểm tra trạng thái.
Chưa xác nhận được bản DeepIron sẵn chạy phù hợp; OmniFabric hiện chưa phát hành code/model. Garment3DGen dựa trên CUDA và chưa được tích hợp. Giao diện chưa dựng áo 3D.

Đã cài: `ag-psd@33.0.0` (npm, `package-lock.json`), VTracer/Hydra/iopath trong `.vendor` (phiên bản ở `review-requirements.txt`); [SAM 2 chính thức](https://github.com/facebookresearch/sam2) tại commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`, model tiny chính thức `sam2.1_hiera_tiny.pt` trong `runtime/models`. SAM 2 được gọi bằng tiến trình CPU riêng và giới hạn ảnh làm việc 1024 px; mỗi lần chỉ một tác vụ hậu kỳ để giữ mức RAM phù hợp máy 16 GB.

PSD xuất RGB 8 bit, giữ canvas/DPI và cache hiển thị của từng lớp. Lớp chữ có dữ liệu text/font gốc, lớp logo là raster riêng; SVG VTracer tải riêng để sửa vector. Chưa kiểm tra mở trực tiếp bằng Photoshop; Photoshop có thể yêu cầu cập nhật lớp chữ và cần font tương ứng. Xuất PSD không gọi API ảnh.

## Kiểm tra cài đặt

```zsh
.venv/bin/python "studio/studio.py" --check
.venv/bin/python "studio/test_openai_api.py"
.venv/bin/python "studio/test_studio.py"
.venv/bin/python "studio/test_review.py"
.venv/bin/python "studio/test_garment_preview.py"
node "studio/test_psd_export.cjs"
```

Các bài kiểm tra dùng ảnh và phản hồi API giả; không gọi tạo ảnh trả phí.
Khả năng MPS cần kiểm tra trong tiến trình ứng dụng thật vì môi trường hạn chế có thể không nhìn thấy GPU.
