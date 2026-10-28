# Phục hồi typography và logo chính xác từ garment mockup

Mốc đối chiếu: **27/10/2026**. Nghiên cứu tài liệu chính thức, repo của tác giả, package do tác giả phát hành và paper gốc; không benchmark, cài software, tải font/model, gọi paid API hoặc chỉnh app. Các URL `main`, `master`, `latest` là snapshot tài liệu lúc tra cứu, không phải lịch sử release bất biến.

Bối cảnh do người dùng cung cấp, chưa kiểm toán lại installation: POD Studio trên Mac mini 16GB; SAM2 tiny CPU, VTracer 0.6.15, ag-psd 33; native PSD text và logo RGBA đã có; manual DaFont import/preview cho 2–3 font, chưa auto-identification. Prompt tạo ảnh mới bỏ lettering/logos. Mẫu Home Alone 1500×1500 cần chính xác hai chuỗi `MERRY CHRISTMAS` và `YA FILTHY ANIMAL`; chữ phía sau có vùng bị che. Chưa xem trực tiếp ảnh nguồn trong lượt này, nên không xác nhận vị trí, kiểu dáng hoặc font identity của mẫu.

Kế thừa kết luận tách chữ/logo trong [báo cáo trước](</Volumes/ViTu/Claude Codex/POD/reports/Từ mockup đến rập in.md>); chỉ nghiên cứu phần typography/logo, không lặp lại workflow phục hồi nền và ghép rập.

## 1. OCR local nào hợp lý, rectify ra sao và confidence thực sự nói được gì?

### Takeaway

Ưu tiên **Apple Vision `VNRecognizeTextRequest` trên crop chữ**, so raw crop với crop đã rectify và luôn có người duyệt. OCR cung cấp transcript và bằng chứng hỗ trợ đọc; không xác định font, không chứng minh hình dạng nguyên bản, không phục hồi được bằng chứng đã bị che. Đây là khuyến nghị thiết kế từ các khả năng và giới hạn dưới đây, chưa phải kết quả chạy mẫu.

### Cited Findings

- Apple công bố text recognition chạy **on-device**, có đường `.fast` và `.accurate`; phần trình diễn trên Mac cũng cảnh báo thời gian ví dụ không đại diện mọi tình huống. Không cần suy ra dịch vụ cloud hoặc paid OCR API từ framework này. — [Apple WWDC19, Text Recognition in Vision Framework](https://developer.apple.com/videos/play/wwdc2019/234/).
- `VNRecognizeTextRequest` có `recognitionLevel`, `recognitionLanguages`, `supportedRecognitionLanguages()`, `usesLanguageCorrection`, `customWords` và `minimumTextHeight` tính tương đối theo chiều cao ảnh. Ngôn ngữ hỗ trợ cần hỏi theo request/revision đang dùng. — [Apple VNRecognizeTextRequest](https://developer.apple.com/documentation/vision/vnrecognizetextrequest).
- `VNRecognizedText` có `string`, `confidence` cho kết quả nhận dạng và `boundingBox(for:)`; observation cho phép lấy nhiều candidate. API được mô tả không có trường confidence riêng cho từng ký tự. Không được biến confidence của cả chuỗi thành xác suất đúng độc lập của mỗi chữ. — [Apple VNRecognizedText](https://developer.apple.com/documentation/vision/vnrecognizedtext).
- **Giới hạn quan trọng:** `boundingBox(for:)` với `.fast` có character precision; với `.accurate`, yêu cầu một khoảng ký tự vẫn có thể trả box của cả từ. Apple nói box không luôn ôm sát chữ và không khuyến nghị lấy nội dung box làm đầu vào image processing. — [Apple boundingBox(for:)](https://developer.apple.com/documentation/vision/vnrecognizedtext/boundingbox(for:)).
- Vision dùng tọa độ normalized 0–1, origin ở góc dưới trái; Apple có hàm đổi normalized rectangle sang image coordinates. Cần phân biệt tọa độ ảnh với UI origin trên trái. — [Apple Vision](https://developer.apple.com/documentation/vision?embed=true), [Apple Recognizing Text in Images](https://developer.apple.com/documentation/vision/recognizing-text-in-images?changes=_5).
- OpenCV có `getPerspectiveTransform`, `warpPerspective`, `remap`; các phép này lấy mẫu lại pixel qua ánh xạ hình học. Paper **RARE, CVPR 2016** dùng TPS rectification trước recognizer để xử lý chữ perspective/curved. Đây là bằng chứng cho hướng rectify trước OCR, không phải benchmark riêng cho nếp vải Home Alone. — [OpenCV geometric transformations](https://docs.opencv.org/4.13.0/da/d54/group__imgproc__transform.html), [paper gốc RARE tại CVF](https://www.cv-foundation.org/openaccess/content_cvpr_2016/html/Shi_Robust_Scene_Text_CVPR_2016_paper.html).

| Lựa chọn | Platform/runtime đã có nguồn | Confidence và license đã kiểm tra | Quyết định đề xuất cho 16GB |
|---|---|---|---|
| Apple Vision | Native macOS, on-device; dùng helper Swift/native nếu toolchain đã sẵn có. Tài liệu tham chiếu không phải một package Python đa nền tảng. | Confidence của recognized string; box theo giới hạn nêu trên. Không gọi Vision là mã nguồn mở MIT/Apache: đây là framework của Apple. | Ưu tiên đầu tiên vì không phải bổ sung model OCR bên thứ ba; chưa đo RAM/latency. |
| PaddleOCR | Package OCR công bố Python ≥3.8; engine là phụ thuộc riêng. Tài liệu macOS của PaddlePaddle nêu x86_64/arm64 và **CPU**, còn Quick Start yêu cầu PaddlePaddle ≥3.0 cho nhánh PaddleOCR 3.x. Không suy ra Metal/MPS từ CUDA hoặc backend khác. | `rec_scores` ứng với các kết quả text, không phải cam kết calibrated per-letter probability. `return_word_box` trả word boxes theo tài liệu hiện tại. Repo Apache-2.0. | Phương án B: recognizer tiếng Anh nhỏ, batch=1, chỉ crop; tránh thêm cả document/VLM pipeline. Chỉ cân nhắc sau khi Vision có lỗi đo được. |
| Surya, snapshot metadata 0.22.1 | Python ≥3.10,<4; README hiện mô tả OCR 650M và backend `vllm` cho NVIDIA, `llama.cpp`/`llama-server` cho CPU/Apple Silicon. Có thêm torch/transformers dependencies; không còn cơ sở nói chỉ một lời gọi torch MPS là đủ. | Code Apache-2.0; **weights có license riêng**. OCR schema hiện có block confidence là mean per-token probability, không phải per-character confidence. | Không ưu tiên cho hai câu chữ. Có đường local được công bố nhưng footprint, tốc độ và lợi ích trên mockup chưa xác minh. |

Nguồn cho bảng: [Apple WWDC19](https://developer.apple.com/videos/play/wwdc2019/234/); [PaddleOCR installation](https://www.paddleocr.ai/main/en/version3.x/installation.html), [PaddlePaddle macOS](https://paddlepaddle-static.cdn.bcebos.com/documentation/docs/en/install/pip/macos-pip_en.html), [PaddleOCR Quick Start](https://www.paddleocr.ai/main/en/quick_start.html), [PaddleOCR result schema](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html), [PaddleOCR LICENSE](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE); [Surya README/schema/backend](https://github.com/datalab-to/surya), [Surya pyproject](https://raw.githubusercontent.com/datalab-to/surya/master/pyproject.toml).

- **Không đơn giản hóa license Surya thành “miễn phí dưới $5M”.** MODEL_LICENSE hiện hạn chế commercial use nếu gross revenue năm trước vượt $5M hoặc tổng equity/debt funding vượt $5M, và có điều khoản về sản phẩm/dịch vụ cạnh tranh với licensor. Chưa xác minh POD Studio thuộc trường hợp được phép. Đây là điều khoản cần đối chiếu nguyên văn trước khi chọn, không phải kết luận pháp lý về dự án. — [Surya MODEL_LICENSE, Attachment A](https://raw.githubusercontent.com/datalab-to/surya/master/MODEL_LICENSE).
- Cả PaddleOCR và Surya có bước chuẩn bị model/backend; tài liệu có hướng dùng model local. Không được gọi một environment “offline-ready” chỉ vì package import được. — [PaddleOCR local model paths](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html), [Surya prerequisites](https://github.com/datalab-to/surya).

### Inferences

**Phase 1 — giữ bằng chứng và chốt transcript.** Các bước dưới đây là thiết kế đề xuất, không phải tính năng đã triển khai hoặc thông số do vendor bảo đảm.

1. Tạo ROI thủ công cho từng dòng chữ ở ảnh trước/sau; giữ ảnh gốc, crop offset, orientation, polygon và mask `visible/occluded`. 1500×1500 là kích thước toàn ảnh, không phải độ phân giải riêng của lettering. SAM2 hiện có chỉ dùng hỗ trợ mask nếu cần; không xem segmentation là OCR/rectification.
2. Thử raw crop trước. Nhánh thứ hai sửa rotation/perspective bằng bốn điểm do người đặt; nếu baseline cong, chỉ thêm mesh/remap với ít landmark được duyệt. Homography phù hợp xấp xỉ mặt phẳng; không kỳ vọng xử lý mọi nếp gấp hoặc vùng bị chồng che. Giữ transform và inverse để đối chiếu trở lại mockup, không ghi đè ảnh nguồn.
3. Rectify từ pixel nguồn một lần, giữ aspect ratio, hạn chế denoise/threshold làm mất distress hoặc serif. Nếu thử upscale, lưu là một biến thể đọc; không dùng nó làm bằng chứng bổ sung nét gốc. Không cắt glyph bằng box `.accurate` của Vision để phục vụ font matching.
4. Vision `.accurate`, ngôn ngữ tiếng Anh đã kiểm tra qua supported languages, lấy tối đa ba candidate. Chạy đối chứng `usesLanguageCorrection=false`; nhánh correction=true phải gắn nhãn riêng. Lượt đọc mù không đưa toàn bộ hai câu đích vào `customWords`, tránh đo khả năng lặp lại đáp án được mớm.
5. Dùng `.fast` hoặc marker thủ công để định vị chữ trên overlay khi cần. Nếu `.accurate` chỉ có word box thì hiển thị word box, không giả là character segmentation. Các glyph shape mask để so font phải được người chỉnh từ crop có thể nhìn thấy.
6. Cho người xem cùng lúc raw crop, rectified crop, các OCR candidate và chuỗi chốt. Đánh dấu riêng `ocr_observed`, `human_read`, `user_supplied`, `occluded_unverified`; mọi disagreement/insert/delete và nhầm `I/L/1`, `O/0`, `M/N`, `R/P` phải được rà.
7. Với Home Alone, hai câu trong brief là **nội dung được người dùng yêu cầu**, không phải kết quả OCR đã quan sát. Có thể dựng lại text theo nội dung đó sau duyệt; phần back bị che vẫn không chứng minh font, glyph outline, kerning hoặc vị trí gốc. Nếu yêu cầu exact hình dạng ở vùng khuất, cần artwork/ảnh bổ sung; thiếu thì để pending, không để model tự đoán rồi đóng dấu exact.

**Đầu ra confidence/approval tối thiểu, đề xuất:**

| Mức | Trường cần giữ | Quy tắc diễn giải |
|---|---|---|
| ROI/run | engine, version/revision, raw/rectified, orientation, correction on/off, transform, runtime | Đủ để tái hiện phép đọc và map overlay về nguồn. |
| Candidate | raw string, rank, `sequence_confidence`, confidence scope | Giữ nguyên số vendor; không so điểm Vision/Paddle/Surya như cùng một thang đã calibrated. |
| Letter/span | Unicode index/character, bbox hoặc null, `box_scope`, `letter_confidence=null` nếu engine không có, visibility | Có box không đồng nghĩa có per-letter confidence. Không chia/copy confidence cả dòng vào từng chữ. |
| Human | approved string, các index đã sửa, transcript approval, font approval, geometry approval | Người duyệt phải nhìn bằng chứng; vùng khuất có provenance riêng, không tự chuyển sang observed. |

Có thể thêm cờ `unstable_across_variants` khi raw/rectified khác nhau, nhưng đây là diagnostic, không phải xác suất nhận dạng. Nếu sau này thật sự cần calibrated per-letter score, phải chọn decoder có output phù hợp và đánh giá calibration trên tập gán nhãn riêng; không cần xây điều đó trước cho hai dòng này.

### Gaps

Chưa chạy Apple Vision, PaddleOCR hay Surya; chưa đo OCR/CER, RAM hoặc latency. Chưa xác minh OS/chip/toolchain, Python ABI và wheel tương thích của Mac mini cụ thể; tài liệu PaddlePaddle macOS được mở thuộc nhánh 3.0 beta cũ, không dùng danh sách Python ở đó để cam kết wheel của release mới. Nguồn công bố platform support không thay cho thử runtime. Chưa có calibration letter confidence hay ảnh back không che. Không có cơ sở kết luận engine nào đọc đúng mẫu này.

## 2. Duyệt font DaFont như thế nào để không nhầm “giống” với “đúng font gốc”?

### Takeaway

Giữ luồng **người chọn 2–3 candidate → import thủ công → render đúng câu → overlay và duyệt**. Tách ba trạng thái: transcript đúng; hình dạng/metrics được duyệt; identity font gốc được xác nhận. Hai trạng thái đầu không tự chứng minh trạng thái thứ ba.

### Cited Findings

- DaFont xác nhận font thuộc tác giả, có nhiều loại license; nhãn bên nút download chỉ là chỉ dẫn. Cần đọc license/readme của font hoặc website tác giả; thiếu thông tin không có nghĩa là free. — [DaFont FAQ](https://www.dafont.com/faq.php).
- Core Text có cap-height, bounding metrics, glyph advances; `CTFontGetAdvancesForGlyphs` trả advance từng glyph và tổng. `CTFontCreatePathForGlyph` tạo outline path, có thể trả null khi lỗi. Đây là nền tảng local để đo/render, không phải API tìm font từ ảnh. — [Apple CTFont](https://developer.apple.com/documentation/coretext/ctfont?changes=la__8), [Apple glyph advances](https://developer.apple.com/documentation/coretext/ctfontgetadvancesforglyphs(_:_:_:_:_:)), [Apple glyph path](https://developer.apple.com/documentation/coretext/ctfontcreatepathforglyph(_:_:_:)).
- SVG text layout phụ thuộc font/glyph metrics và các thuộc tính như font family, spacing, position, text-on-path; giữ chữ dạng `<text>` đòi hỏi kiểm soát font khi render. — [W3C SVG 2 Text](https://www.w3.org/TR/SVG2/text.html).

### Inferences

**Phase 2 — quy trình candidate nhỏ, không auto-identification.**

- Người chọn candidate trên DaFont/website tác giả theo kiểu chữ quan sát được; chỉ dùng file font họ đã chủ động cung cấp và có quyền dùng. Không scrape, bulk-download, đoán tên font từ tên bộ phim hoặc gọi font-identification API. Không cần thêm thư viện/model so font trong lần đầu.
- Mỗi candidate lưu filename/hash, family, style/weight, full name/PostScript name, font version, nguồn tác giả, license và người duyệt. Dùng đúng face đã import; không âm thầm fallback hoặc bật faux bold/italic. Preview phải báo nếu glyph thiếu. Metadata và checksum giúp tái hiện font, không chứng minh font đó là bản gốc.
- Render riêng `MERRY CHRISTMAS` và `YA FILTHY ANIMAL` với đúng capitalization, spaces, line breaks đã duyệt. Cho phép hai dòng dùng hai font khác nhau; không gộp thành một mặt chữ chỉ vì cùng thiết kế.
- So ở **cùng cap-height và baseline**, trước hết chỉ translation + uniform scale. So letter width, stroke thickness, counters, terminals, serif, chân `R`, cấu trúc `M/Y/A`, dấu kết thúc và khoảng cách giữa các chữ. Dùng nhiều chữ lặp `M/R/A/I` ở vùng thấy rõ để tránh chọn theo một glyph tình cờ giống.
- Sau khi loại candidate sai shape, mới chỉnh tracking/kerning và line spacing. Nếu phải bóp ngang từng chữ hoặc chỉnh méo tùy ý mới khớp, ghi rõ là **custom lettering reconstruction**; không gọi đó là xác nhận font gốc. Distress/texture nên là layer/mask riêng để không chọn nhầm font vì vết xước của mockup.
- Dùng overlay alpha 50%, blink, contour difference và số đo width/cap-height/spacing trên mask visible đã duyệt. Có thể tính silhouette IoU và khoảng cách contour hai chiều sau alignment; đây là công cụ so candidate, không phải detector identity. Chấm từng glyph quan trọng, đừng chỉ chấm pixel similarity toàn dòng vì màu vải, noise và occlusion có thể chi phối điểm.
- So ứng viên bằng cách chiếu bản render phẳng trở lại vùng mockup theo transform đã chốt; đồng thời giữ bản phẳng cho layer in. Chưa có transform đúng thì kết luận so hình dạng phải ghi là bị giới hạn bởi warp.
- Quyết định duyệt có ba ô độc lập: `transcript_approved`, `appearance_approved`, `font_identity_verified`. Với nguồn hiện tại, ô thứ ba **unknown**. Cho phép “tái dựng đã được người dùng duyệt” để vận hành, nhưng nhãn “exact original font” chỉ khi có asset/designer/font provenance và đối chiếu phù hợp.
- Kiểm tra license cho commercial POD/merchandise và việc phân phối/embedding font nếu có. Không gửi nguyên file font kèm PSD/SVG chỉ vì font render được. Outlining không tự thay thế điều kiện license; quyền dùng font cũng không tự xác nhận quyền dùng artwork/logo Home Alone. Chưa chọn font nên chưa thể kết luận license của bất kỳ candidate cụ thể nào.

### Gaps

Không xác định được font gốc từ brief và không đưa tên font suy đoán. Chưa có 2–3 candidate được người dùng import cho phép thử, chưa đo shape/metrics hoặc đọc license của font cụ thể. Chưa biết lettering là font nguyên bản, font biến đổi, hay nét vẽ tay; vùng khuất không thể dùng để xác nhận identity. Chỉ ảnh mockup 1500×1500 không đủ căn cứ chứng nhận mọi đường cong ở kích thước in.

## 3. VTracer, SVG và native PSD nên nối vào pipeline thế nào; ba thử nghiệm nào quyết định triển khai?

### Takeaway

Dùng native text cho nội dung cần sửa, SVG outlines làm bản vector đã duyệt khi cần; giữ logo RGBA hiện có làm baseline. Chỉ chọn trace khi qua đối chiếu contour và topology. SVG tồn tại hoặc PSD mở được không đủ chứng minh chính xác, đúng font hay còn vector bên trong PSD.

### Cited Findings

- Package **VTracer 0.6.15** do maintainer phát hành ghi MIT, Python ≥3.7 và có các tham số `colormode`, `hierarchical`, `mode`, `filter_speckle`, `color_precision`, `layer_difference`, `corner_threshold`, `length_threshold`, `splice_threshold`, `path_precision`. Đây là API Python cần đối chiếu thay vì sao chép cờ CLI khác phiên bản. — [VTracer 0.6.15, package chính thức trên PyPI](https://pypi.org/project/vtracer/0.6.15/).
- Tác giả mô tả VTracer là raster-to-vector và lưu ý ở nhánh 0.6.x rằng JPEG noise ảnh hưởng trace, cutout còn có seams. Repo hiện đã mô tả engine 1.0 với khả năng khác. Không được gán seam-free/adaptive cleanup của engine mới cho bản 0.6.15 đang dùng. — [README VTracer tag 0.6.4](https://raw.githubusercontent.com/visioncortex/vtracer/0.6.4/README.md), [README hiện tại](https://github.com/visioncortex/vtracer), [changelog](https://raw.githubusercontent.com/visioncortex/vtracer/master/CHANGELOG.md).
- `ag-psd` metadata đang là **33.0.0**, MIT. README nêu text layer chưa hoàn chỉnh, không tự redraw bitmap text/composite khi thay text/vector; có thể xuất cảnh báo cập nhật trong Photoshop. `invalidateTextLayers` yêu cầu Photoshop redraw, không thay thế renderer local. Vertical text có giới hạn nghiêm trọng. — [package.json](https://raw.githubusercontent.com/Agamnentzar/ag-psd/master/package.json), [README ag-psd](https://raw.githubusercontent.com/Agamnentzar/ag-psd/master/README.md).
- Types của ag-psd có text string, affine transform `[xx,xy,yx,yy,tx,ty]`, font name/size, tracking, kerning, scale và warp. `textPath` được đánh dấu **read-only**, thay đổi không được lưu. Có type warp không phải bằng chứng renderer local sẽ tính đầy đủ mọi biến dạng. — [ag-psd src/psd.ts](https://raw.githubusercontent.com/Agamnentzar/ag-psd/master/src/psd.ts).
- Canvas PSD `width/height` tính bằng pixels; layer có bounds và bitmap/image data. ag-psd chỉ viết RGB, 8 bits/channel theo tài liệu. — [ag-psd PSD structure](https://raw.githubusercontent.com/Agamnentzar/ag-psd/master/README_PSD.md).
- SVG xác định viewport, user coordinates, `viewBox`, transforms và mapping bằng `preserveAspectRatio`. Vì vậy cần ghi rõ kích thước hiển thị và hệ tọa độ khi đưa vào panel. — [W3C SVG 2 Coordinates](https://www.w3.org/TR/SVG2/coords.html).
- Adobe xác nhận missing fonts có thể bị thay bằng font mặc định, làm đổi layout hoặc appearance; kích hoạt font qua Adobe Fonts cần mạng/đăng nhập. Đây là rủi ro cần kiểm tra khi nhận PSD, không phải khuyến nghị dùng dịch vụ này trong thử nghiệm. — [Adobe Resolve missing fonts](https://helpx.adobe.com/fonts/web/getting-and-using-fonts/resolve-missing-fonts.html).

### Inferences

**Phase 3 — layer có kiểm soát kích thước và bằng chứng.**

1. Giữ group riêng cho từng dòng chữ và logo; lettering được duyệt được đặt lên nền không chữ do luồng hiện có cung cấp. Rà residual/ghost lettering trước composite để không in hai lần. Không gọi lại image generation chỉ để sửa chính tả, font hoặc tọa độ.
2. Ưu tiên font đã duyệt → native text với font metadata/geometry. Khi cần bản không phụ thuộc font ở nơi nhận, tạo SVG glyph outlines từ cùng layout đã duyệt; giữ native text cho sửa nội dung. Outline là đường hình học của font đã chọn, không phải bằng chứng font identity.
3. Text đang thẳng chỉ cần transform đơn giản. Text trên đường cong hoặc custom warp phải test thực tế; `textPath` read-only khiến không thể mặc định author curved text mới bằng trường đó. Nếu native PSD chưa giữ đúng hình dạng, giữ bản editable phẳng ẩn và một layer raster/outline render đã duyệt; không ghi nhãn layer raster là editable text hoặc vector.
4. Với logo: ưu tiên asset gốc được phép dùng. Nếu chỉ có crop mockup, dùng RGBA sạch làm baseline; VTracer chỉ được trace vùng đã rectify và loại bóng/nếp. So default với một cấu hình bảo toàn chi tiết nhỏ (giảm `filter_speckle`, giảm simplification phù hợp API 0.6.15); không có bộ tham số “exact” dùng cho mọi ảnh. Giá trị cụ thể chỉ chốt sau thử, không bê nguyên default web vào sản xuất.
5. Các lỗi phải tìm: mất đốm distress/chấm nhỏ; bít counter ở `A/R`; nối hai nét vốn tách; mất serif; rounded corner; thêm vùng do JPEG/shading; thay màu; seam/halo; quá nhiều nodes. Trace có thể lưu lại cả lỗi ảnh nguồn. Rasterize SVG ở nhiều kích thước để so trên nền sáng và tối, đồng thời kiểm tra topology của holes/components, không chỉ cảm giác tổng thể.
6. Đặt artwork trong **panel coordinates**, không lấy tọa độ crop 1500×1500 làm pixel in. Giữ chain source → ROI → rectified art → panel và mapping ngược cho preview. Bbox có normalized anchor cộng size in mm hoặc px/DPI đích; origin panel thống nhất trên trái. Với SVG đặt `viewBox` và dimensions rõ, không âm thầm stretch khác trục.
7. Công thức đặt kích thước: `px = mm × DPI / 25.4`; nếu fontSize được engine nhận theo point thì `pt = px × 72 / DPI` trước transform, nhưng phải kiểm tra convention thực tế của writer/renderer bằng reopen. Không giả định fontSize CSS px, PSD point và chiều cao glyph là cùng một số. Cap-height thực đo từ render mới là đại lượng đối chiếu.
8. ag-psd cần bitmap layer/composite nhất quán với native text. Dùng renderer hiện có với cùng font/layout đã duyệt để tạo preview; reopen trong editor đích và so sau khi editor redraw. Ghi nhận font name thực tế, thiếu glyph, fallback và reflow. Không mặc định font được embed vì PSD có text record.
9. Logo RGBA đưa vào PSD vẫn là raster; giữ SVG nguồn riêng nếu cần vector master. Không có bằng chứng chỉ đưa SVG qua renderer rồi ghi RGBA sẽ tạo native vector shape/smart object. Hướng nhỏ nhất cho hiện trạng là native text + RGBA logo được duyệt; chỉ bổ sung native vector PSD nếu có yêu cầu riêng và kiểm chứng được roundtrip.
10. Trên 16GB, xử lý một crop/panel/layer mỗi lượt và tránh giữ nhiều composite lớn. Chi phí tối thiểu của một buffer RGBA 8-bit là `W×H×4` bytes; đây chỉ là số học buffer, chưa gồm bản sao, decoder và editor. Không lấy benchmark GPU của repo làm cam kết cho Mac này.

**Ba thử nghiệm theo thứ tự — tất cả còn chưa chạy.** Các ngưỡng dưới đây là gate thử nghiệm do nghiên cứu đề xuất, không phải dung sai nhà in hoặc kết quả công bố. Không cài hoặc tải thêm trong lượt nghiên cứu; nếu runtime/font/model chưa sẵn, nhánh liên quan được ghi `not run`.

| Thử nghiệm | Thiết kế cụ thể | Acceptance criteria | Nếu không đạt |
|---|---|---|---|
| **1. OCR raw vs rectify + human transcript** | Hai ROI của mẫu, mỗi ROI có raw và manual-rectified crop; Vision accurate correction off/on, candidate top 3. Lượt đọc mù không dùng câu đích làm custom words. Fast chỉ để thử box/overlay. Giữ reference transcript và mask visible do người gán; phần khuất đánh dấu riêng. Đo cold time, median warm time qua 3 lần, peak memory và số ký tự người sửa. Paddle chỉ là nhánh đối chứng sau này nếu runtime/model hợp lệ đã sẵn. | Chuỗi cuối đúng 100% capitalization/spaces: `MERRY CHRISTMAS`, `YA FILTHY ANIMAL`, sau người duyệt; 0 ký tự khuất bị đánh dấu OCR-observed; 100% sửa đổi có provenance. Tính CER chỉ trên vùng nhìn thấy đã gán nhãn, không tính text người cung cấp là OCR đúng. Giữ rectification chỉ nếu giảm lỗi/sửa tay hoặc cứu ROI trước đó không đọc được; không tăng lỗi vùng rõ. Gate vận hành ban đầu: ≤30 giây cho toàn bộ 2 ROI sau warm-up, app không bị đứng; đo rồi mới quyết định ngưỡng phù hợp. | Transcript vẫn có thể do người gõ; ghi OCR chưa đạt, không ép thêm model nặng chỉ để có confidence cao. Vùng khuất cần tham chiếu bổ sung nếu muốn chứng nhận hình dạng gốc. |
| **2. Human font approval trên 2–3 candidate** | Dùng file đã được người import và license có bằng chứng; render cả hai câu. Chuẩn hóa cap-height/baseline, overlay ở vùng visible, chấm shape từng glyph và width/spacing; lưu metrics, biến đổi và thời gian chỉnh. Không cho auto-selector chốt. | Transcript 100% đúng; 0 fallback/thiếu glyph; người duyệt shape/kerning của mọi glyph thấy rõ; width và cap-height trong ±2% reference rectified đã duyệt, không còn khác biệt cấu trúc counter/serif/terminal. Metric chỉ là gate phụ. License phù hợp POD phải có trước production. Không candidate nào đủ bằng chứng thì kết quả hợp lệ là `no exact match / pending`, không phong candidate đứng đầu thành exact. | Giữ identity unknown; lấy font/asset từ designer hoặc dựng lettering thủ công rồi duyệt như reconstruction. Không tải hàng loạt font. |
| **3. Logo fidelity + SVG/PSD roundtrip ở size in** | Một logo crop rõ và hai text layer đã duyệt. So RGBA baseline với default VTracer0.6.15 và một cấu hình giữ chi tiết; rasterize SVG tại size đích và 2×. Xuất bằng ag-psd33 hiện có, reopen editor đích, cho redraw rồi so composite. Kiểm tra record text/font/transform và layer bounds, thử đổi một chữ rồi undo để chứng minh editable text. | Đúng toàn bộ transcript sau reopen; 0 font substitution/clip/reflow chưa được duyệt. Canvas/DPI đúng target; vị trí và bbox so với reference layout sai ≤1 pixel ở phép kiểm tra cùng resolution. Logo không mất/thêm component hoặc hole quan trọng; contour deviation 95th percentile ≤1 pixel trên **crop nguồn đã rectify**, IoU mask visible ≥0.98, cộng người duyệt nét nhỏ/alpha ở size in. Những số này chỉ so với baseline đã duyệt, không chứng nhận logo gốc. Nếu RGBA nhìn trung thực hơn SVG thì giữ RGBA và đánh dấu vector chưa đạt. | Chặn nhãn ready/exact tương ứng; sửa layer preview/font/geometry hoặc trace tay. Nếu chưa có editor đích để reopen, kết luận roundtrip là chưa kiểm tra, dù read/write lại bằng ag-psd thành công. |

**Quyết định triển khai đề xuất:** làm thử nghiệm 1 với Vision trước; giữ thao tác gõ chữ tay làm fallback. Sau đó thực hiện thử nghiệm 2 trong luồng DaFont preview đang có. Khi transcript và appearance đã được duyệt, làm thử nghiệm 3 trên exporter hiện có. Chỉ thêm PaddleOCR khi đo được Vision không đủ; tạm hoãn Surya vì backend và điều kiện weights làm tăng phạm vi mà chưa có lợi ích trên mẫu. Không cần thêm auto-font-identification, OCR ensemble thường trực hoặc engine vector mới để bắt đầu.

### Gaps

Đã xác nhận bằng nguồn: API/giới hạn box và confidence của Vision; cơ sở rectify trước recognition; các đường runtime được nhà phát triển công bố; license code/weights có phân biệt; tham số package VTracer0.6.15; ag-psd metadata33, giới hạn redraw/textPath; hệ tọa độ SVG và nguy cơ missing-font.

Chưa kiểm chứng thực nghiệm: OCR thực tế trên Home Alone; hiệu năng/RAM Mac mini; mức cong/nếp/che của ảnh nguồn; danh tính font; license từng candidate và quyền artwork/logo; trace có giữ đúng distress/logo hay không; việc renderer hiện có khớp Photoshop/editor đích; fontSize/DPI convention, placement và PSD roundtrip ở kích thước in. Có API và license phù hợp không tự chứng minh output exact. Chỉ file ghi chú này được tạo; không chỉnh app hoặc file của agent khác.
