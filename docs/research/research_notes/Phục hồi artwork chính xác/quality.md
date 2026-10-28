# Kiểm chứng chất lượng độc lập và benchmark tái lập: garment POD mockup → print

Mốc đối chiếu: **2026-10-27**, múi giờ Asia/Ho_Chi_Minh. Chỉ dùng tài liệu chính thức, repo tác giả và bài nghiên cứu gốc. Đây là nghiên cứu và thiết kế benchmark, **không phải kết quả benchmark đã chạy**.

Bối cảnh do người dùng cung cấp: Mac mini 16 GB; đã có OpenAI restoration cho Front/Back/Sleeve, Real-ESRGAN tùy chọn, calibrated 2D projection, duyệt thủ công mọi panel và bộ PSD/PNG ZIP bất biến; SAM 2 và VTracer đã cài. Ca Home Alone: nguồn 1500×1500, artwork chỉ chiếm ít pixel nhìn thấy, chất lượng local đã bị đánh giá kém. Các thông tin này là đầu vào của nhiệm vụ, chưa được đo hoặc kiểm tra lại trong lần nghiên cứu này.

Đã đọc [báo cáo trước](/Volumes/ViTu/Claude%20Codex/POD/reports/Từ%20mockup%20đến%20rập%20in.md). Ghi chú này bổ sung cách chứng minh chất lượng, không lặp lại quy trình restoration chung. Không gọi API trả phí, không cài đặt, không chạy model, không sửa app hoặc file của agent khác. Chỉ tạo file ghi chú này. Hướng dẫn nghiên cứu đã đọc ở đường dẫn người dùng chỉ định; lean-ctx chặn lần đọc hướng dẫn trong `~/.codex`, không mở rộng quyền hoặc đi vòng đường dẫn đó.

## 1. Kiểm chứng độc lập thế nào để bắt lỗi chữ, chân dung, repeat, detail và tính nhất quán giữa panel?

### Takeaway

Không dùng một điểm “trông giống” để duyệt in. Cần các gate riêng cho nội dung, hình học, chi tiết và bằng chứng nguồn; mọi so sánh ảnh phải có correspondence hợp lệ. Các quy tắc dưới đây là thiết kế đề xuất, chưa có ngưỡng được hiệu chuẩn trên dữ liệu POD này.

### Cited Findings

- SSIM là phép đánh giá full-reference; bài DISTS của các tác giả chỉ rõ nhiều IQA metric dựa vào căn chỉnh chính xác và nhạy với dịch chuyển/đổi patch texture. DISTS được thiết kế để chịu được texture resampling — một đặc tính hữu ích về cảm nhận nhưng không chứng minh đúng vị trí motif. [SSIM, trang tác giả](https://www.cns.nyu.edu/~lcv/ssim/); [Ding et al., DISTS, bản tác giả](https://www.cns.nyu.edu/pub/lcv/ding20a-preprint.pdf).
- LPIPS đo perceptual distance bằng deep features. Repo tác giả có lựa chọn mạng và triển khai spatial distance; bản thân metric không phải phép kiểm tra chính tả hoặc xác nhận danh tính. [LPIPS, repo tác giả](https://github.com/richzhang/PerceptualSimilarity); [triển khai spatial LPIPS](https://github.com/richzhang/PerceptualSimilarity/blob/master/lpips/lpips.py).
- OpenCV mô tả tìm homography từ các điểm tương ứng, cần ít nhất bốn điểm đúng; RANSAC trả inlier mask. Đây là công cụ đăng ký hình phối cảnh, không phải bằng chứng rằng toàn bộ bề mặt áo gấp nếp là một mặt phẳng. [OpenCV, Feature Matching + Homography](https://docs.opencv.org/4.13.0/d1/de0/tutorial_py_feature_homography.html).
- JiWER hỗ trợ CER và alignment chỉ rõ equal/substitute/delete/insert ở mức ký tự. Có thể lưu lỗi từng vị trí thay vì chỉ giữ điểm tổng. [JiWER, hướng dẫn tác giả](https://github.com/jitsi/jiwer/blob/master/docs/usage.md).
- `phaseCorrelate` của OpenCV ước lượng translational shift bằng Fourier shift theorem. Nó không tự giải quyết mơ hồ do nhiều motif lặp giống nhau. [OpenCV, Motion Analysis](https://docs.opencv.org/4.13.0/d7/df3/group__imgproc__motion.html).
- Real-ESRGAN là phương pháp blind super-resolution/restoration với dữ liệu suy giảm tổng hợp. Bài Perception-Distortion Tradeoff chứng minh chất lượng cảm nhận và độ méo so với ảnh thật là hai mục tiêu có thể xung đột; nhìn sắc nét hơn không đồng nghĩa phục hồi đúng hơn. [Real-ESRGAN, bài gốc](https://arxiv.org/abs/2107.10833); [Blau & Michaeli, bài gốc](https://arxiv.org/abs/1711.06077).
- Printful yêu cầu kích thước file phù hợp kích thước in; ví dụ của họ cho thấy phóng cùng số pixel lên gấp đôi kích thước vật lý làm mật độ giảm một nửa. Hướng dẫn chung nêu ít nhất 150 DPI và yêu cầu kiểm tra File guidelines của từng sản phẩm. [Printful, DPI và kích thước thực](https://help.printful.com/hc/en-us/articles/50264010605457-What-is-DPI-resolution-and-actual-print-file-size).
- Printful tuyên bố không thể khớp hoàn hảo hình/pattern từ Front sang Back trên AOP cut & sew. Do đó, chất lượng file và độ lệch sản xuất là hai vấn đề khác nhau. [Printful, Printing Techniques disclaimers](https://help.printful.com/hc/en-us/articles/50264395400337-Disclaimers-for-Printing-Techniques).

### Inferences

#### A. Bằng chứng và hình học phải cố định trước khi chấm

Đề xuất lưu hai lớp nhãn riêng, tránh đánh đồng “có pixel” với “biết nội dung”:

| Lớp | Nhãn tối thiểu | Cách dùng |
|---|---|---|
| Khả năng quan sát trong nguồn | `observed_readable`, `observed_low_detail`, `occluded_or_unseen` | Chỉ nhãn đầu đủ điều kiện kiểm chứng nội dung trực tiếp; nhãn thứ hai có thể kiểm bố cục nhưng không xác nhận mọi nét/chữ |
| Nguồn gốc output | `copied_or_reprojected`, `manually_rebuilt`, `model_generated` | Một vùng nhìn thấy nhưng được AI vẽ lại vẫn phải ghi là generated |

Mask phải được lập từ ảnh nguồn và annotation độc lập trước khi xem output. Không để model tự tuyên bố vùng nó vẽ là “observed”. Lưu độ phủ trên diện tích in của từng panel, không tính phần ngoài rập: `readable coverage`, `low-detail coverage`, `unseen coverage`, `generated coverage`. Không tự gán tỷ lệ chắc chắn khi chưa có phép đo.

Với mockup M, artwork A và map cố định P từ panel sang ảnh mockup, so **P(A) với M trong cùng không gian mockup**, tại giao của vùng nhìn thấy, vùng map có hiệu lực và vùng đủ chi tiết. Loại nền, nếp gấp che khuất, cạnh silhouette không ổn định, vùng chưa quan sát. Nếu không đủ correspondence, trả `not_verifiable`, không ép ra một điểm.

- Giữ calibrated 2D map hiện có. Dùng các landmark do người chấm chọn trước và giữ lại một phần không tham gia fit map; báo lỗi reprojection trên phần hold-out, bằng pixel ảnh nguồn và bằng mm khi có scale vật lý đáng tin.
- Một homography chỉ phù hợp một vùng có xấp xỉ phẳng. Với nếp gấp, dùng các correspondence cục bộ đã xác nhận hoặc bỏ vùng đó khỏi metric; không kéo giãn cả áo chỉ để tăng SSIM. Căn cứ về công cụ: [OpenCV homography](https://docs.opencv.org/4.13.0/d1/de0/tutorial_py_feature_homography.html).
- Freeze map, crop, vùng đánh giá và phép chuẩn hóa ánh sáng trước khi so các nhánh. Nếu fit một warp mềm riêng theo từng output, bộ chấm có thể “sửa hộ” sai vị trí, tỷ lệ hoặc nét chữ.
- Nếu cần SSIM, lấy SSIM map rồi chỉ tổng hợp các cửa sổ nằm trọn trong vùng hợp lệ; erode mask theo bán kính cửa sổ. Không tô đen phần ngoài mask rồi tính toàn ảnh: nền giống nhau có thể làm điểm tăng giả. Ghi coverage cùng điểm. Đây là quy tắc đánh giá đề xuất từ giới hạn alignment của [SSIM/DISTS](https://www.cns.nyu.edu/pub/lcv/ding20a-preprint.pdf).
- LPIPS, nếu đã có sẵn, chỉ dùng bổ sung trên các crop cùng tọa độ và cùng scale. Crop đủ xa rìa mask để giảm ảnh hưởng ngữ cảnh ngoài vùng; không coi spatial masking là cách loại sạch receptive-field contamination. Không cài mới chỉ để lấy một điểm tổng. [Triển khai LPIPS](https://github.com/richzhang/PerceptualSimilarity/blob/master/lpips/lpips.py).
- Mockup chứa shading, chất liệu, ánh sáng; artifact phẳng không có cùng màu pixel. Tách báo cáo hình học/edge trên ảnh chuẩn hóa luminance khỏi báo cáo màu trên artwork phẳng có profile. Không dùng phép chuẩn hóa ánh sáng để tuyên bố màu in chính xác.

Cycle consistency nên là kiểm tra phụ của map: panel → mockup → panel, hoặc ngược lại, chỉ ở vùng có ánh xạ hai chiều hợp lệ. Lưu residual và baseline resampling không có restoration. **Hai map nghịch đảo được dựng từ cùng một map có thể cycle-consistent ngay cả khi hình được vẽ sai.** Cycle không chứng minh chữ đúng, mặt đúng hoặc vùng khuất đúng. Cần landmark hold-out, view giữ lại, hoặc artwork GT độc lập để tránh tự chấm bằng chính phép biến đổi đã sinh mẫu. Đây là suy luận về cấu trúc phép thử, không phải kết quả đã đo.

#### B. Gate theo loại lỗi

| Rủi ro | Phép kiểm tối thiểu đề xuất | Điều kiện và giới hạn |
|---|---|---|
| Wrong letters / số / logo chữ | Transcript chuẩn do người duyệt xác nhận; exact string match từng vùng; CER và alignment để chỉ chỗ sai | Transcript không lấy từ cùng model restoration. Kiểm cả ảnh phẳng và crop reprojected. OCR đọc đúng chưa chứng minh hình dạng chữ đúng |
| Portrait drift | Crop mặt tương ứng, registration similarity cố định từ nguồn/GT; landmark mắt–mũi–miệng, silhouette, và duyệt mù side-by-side | Không dùng nonrigid face warp để che drift. Landmark gần nhau chưa bảo đảm cùng người; vùng mặt ít pixel phải được đánh dấu không đủ bằng chứng |
| Pattern repeat | Xác nhận motif, hướng, chu kỳ theo mm; autocorrelation/correlation hoặc phase correlation trên crop rectified; anchor riêng để khóa phase | Pattern có thể dịch đúng một chu kỳ mà correlation vẫn cao. Phải đối chiếu motif có định danh/anchor, không chỉ peak Fourier |
| Seam alignment | Cặp stitch line có định danh, seam/notch correspondence, chiều đi và band hai phía đường may; đo lệch motif/phase theo mm | Không so đơn thuần hai biên ảnh hoặc bleed. Sleeve cap/easing có thể không ánh xạ 1:1 theo chiều dài; thiếu seam map thì ghi chưa kiểm chứng |
| Scale / DPI / actual detail | Kích thước pixel, kích thước in mm, PPI xuất; số pixel nguồn thật trong từng ROI; kiểm nét nhỏ tại scale vật lý | DPI metadata và số pixel sau upscale không đo được lượng thông tin gốc |
| Destructive upscaling | So trước/sau tại cùng kích thước xuất và cùng crop; recheck chữ, mặt, nét, motif, halo và đường nối tile; GT nếu có | Sharpness/edge energy tăng chỉ là cảnh báo hoặc mô tả, không tự tạo bằng chứng fidelity |
| Front/Back/Sleeve không nhất quán | Danh mục panel, asset ID, scale mm, palette, orientation/mirror, motif phase và crop provenance | Không so trực tiếp Front với Back nếu thiết kế chủ ý khác nhau. Check phần được yêu cầu chung, không ép mọi panel thành cùng một ảnh |

Chi tiết vận hành đề xuất:

1. **Chữ:** `CER = (S + D + I) / N_ref`, công bố đơn vị ký tự là Unicode code point sau NFC và giữ nguyên case/dấu/dấu câu. Chính sách xuống dòng/whitespace phải được chốt trước, không tự bỏ khoảng trắng hay dấu tiếng Việt để làm đẹp điểm. Lưu raw transcript và chuỗi đã chuẩn hóa. Nếu `N_ref = 0`, exact-empty là pass; có chữ phát sinh là lỗi riêng, không chia cho 0. Báo CER từng ROI và micro-average, nhưng **mọi chuỗi bắt buộc phải exact-match**; một tên sai không được bù bởi đoạn chữ dài đúng. CER và alignment có công cụ tác giả: [JiWER](https://github.com/jitsi/jiwer/blob/master/docs/usage.md).
2. OCR nên là engine độc lập với model tạo ảnh nếu đã có sẵn. Nếu chưa có, dùng hai lượt đọc thủ công mù cho transcript/crop trước khi quyết định cài thêm. Chữ cách điệu có thể bị OCR đọc sai; lưu `OCR result` và `manual adjudication` riêng, không sửa kết quả OCR cho khớp. Không đủ pixel để đọc nội dung gốc thì trạng thái đúng là `unknown`, chưa phải chữ đã đúng.
3. **Mặt:** landmark error có thể chuẩn hóa theo inter-ocular distance nếu hai mắt nhìn thấy. Tên người/nhân vật do model đoán, similarity toàn áo, hoặc “trông đẹp hơn” không là bằng chứng giữ danh tính. Chấm riêng hình học mặt và kết luận fidelity; chưa có ngưỡng face-embedding được hiệu chuẩn cho chân dung in trên áo này, nên không thêm face-recognition dependency.
4. **Repeat/seam:** báo cả độ lệch phase và chu kỳ thực theo mm trên artwork phẳng; chấm band hai phía stitch line theo seam map, có notch để tránh đảo chiều. Dùng correlation như công cụ gợi vị trí theo [OpenCV](https://docs.opencv.org/4.13.0/d7/df3/group__imgproc__motion.html), kèm anchor thủ công để giải mơ hồ periodic. Duyệt thêm mô phỏng lệch trong dung sai xưởng nếu đã được xưởng xác nhận; không tự đặt con số dung sai chung.
5. **Scale/detail:** `PPI_export_x = width_px / (width_mm / 25.4)` và tương tự cho y. Tách với `source_sampling_PPI` từ kích thước ROI nguồn trước restoration/upscale. Khi có map mm → pixel nguồn, Jacobian có thể mô tả mật độ lấy mẫu cục bộ; singular value nhỏ nhất nhân 25.4 cho mật độ theo hướng yếu nhất. Đây chỉ là proxy mật độ lấy mẫu, không phải measured resolving power: blur, JPEG, che khuất vẫn làm mất detail. Ví dụ tính toán minh họa: **nếu** 1500 pixel thực phủ 20 inch thì chỉ có 75 pixel nguồn/inch; đó không phải số đo của Home Alone. Nguyên tắc kích thước vật lý dựa trên [Printful](https://help.printful.com/hc/en-us/articles/50264010605457-What-is-DPI-resolution-and-actual-print-file-size).
6. **Upscale:** giữ immutable pre-upscale. Đặt output Real-ESRGAN cạnh output resize thường tại đúng cùng kích thước và cùng pipeline màu; không so ảnh nhỏ với ảnh lớn ở zoom khác nhau. Kiểm lại CER/exact-match, mặt, đứt/dính nét, halo, texture mới, tile boundaries; downsample output về nguồn là phép thử phụ, vì chi tiết bịa vẫn có thể biến mất khi downsample. Có GT thì so crop với GT; không GT thì chỉ kết luận về phần có bằng chứng. Căn cứ phân biệt perception và fidelity: [Perception-Distortion Tradeoff](https://arxiv.org/abs/1711.06077).
7. **Panel consistency:** dùng cùng approved asset cho motif/chân dung/logo lặp nếu thiết kế yêu cầu; lưu hash asset trước khi đặt, rồi kiểm transform vị trí/scale/mirror của từng instance. Với Sleeve, ghi rõ trái/phải hoặc quy tắc tái sử dụng một file; không tự nhân đôi/lật khi template không chỉ định. Duyệt cả bộ trong một contact sheet có nhãn và từng panel riêng ở scale in; contact sheet nhỏ không thay thế kiểm chữ/mặt.

Gate chấp nhận đề xuất, không phải tiêu chuẩn ngành đã xác thực: (a) toàn bộ chữ bắt buộc đúng hoặc có adjudication rõ; (b) đúng panel/template/kích thước/hướng, nội dung quan trọng trong safe area và phủ bleed theo template; (c) mặt/logo/motif quan trọng được duyệt riêng; (d) unknown/generated được công khai và duyệt; (e) không có lỗi mới do upscale; (f) mọi panel đã được duyệt trên đúng checksum sẽ xuất. Không gộp thành một điểm trung bình để lỗi chữ hoặc mặt bị che bởi nền đẹp.

### Gaps

- Chưa có artwork GT, correspondence annotation, crop gốc hoặc transcript đã duyệt của Home Alone trong phạm vi lần này; không thể xác nhận bộ phận nào là irrecoverable, đo source PPI, CER, portrait drift hay coverage thực tế. 1500×1500 toàn ảnh không tự cho biết kích thước vùng mặt/chữ.
- Chưa có ngưỡng SSIM/LPIPS/landmark/seam-error được xác thực cho các mẫu này. Không đề xuất “SSIM > 0.9 là đạt in”, “face similarity > x là đúng người” hoặc một ngưỡng sharpness chung.
- Chưa có seam correspondence và dung sai sản xuất của sản phẩm/xưởng cụ thể. Artwork nối đúng trên máy không tương đương bảo đảm ráp đúng khi may; giới hạn này được nhà cung cấp công bố trong [Printful disclaimers](https://help.printful.com/hc/en-us/articles/50264395400337-Disclaimers-for-Printing-Techniques).

## 2. Benchmark nhỏ nào có thể tái lập, tránh leakage và gắn đúng bản duyệt với artifact bất biến?

### Takeaway

Dùng 5 mockup kiểm soát được, chấm ở cấp ROI/panel rồi cả bộ; giữ designer GT ngoài input restoration. Ưu tiên tái chấm output đã lưu và negative controls trước khi phát sinh lần generate mới. Một benchmark nhỏ giúp tìm lỗi và quyết định nhánh, không đủ để tuyên bố chất lượng chung cho mọi áo.

### Cited Findings

- SHA-256 có sẵn trong thư viện chuẩn Python; digest được tính từ bytes đầu vào. Nó phù hợp xác minh file đúng phiên bản, không đo tương đồng thị giác. [Python, hashlib](https://docs.python.org/3/library/hashlib.html).
- VTracer là raster-to-vector converter. Vai trò đó không cung cấp transcript chuẩn hay ground truth artwork. [VTracer, repo tác giả](https://github.com/visioncortex/vtracer).
- SAM 2 cung cấp promptable segmentation cho ảnh/video; chức năng mask không phải phép khôi phục nội dung hình bị che. [Meta, SAM 2](https://ai.meta.com/research/sam2/).
- OpenAI hướng dẫn dùng trường `usage` để theo dõi token consumption thực tế. Vì vậy số request, usage và chi phí thực trả nên được lưu từ lần chạy; không suy ra số tiền từ kích thước output hoặc một ví dụ pricing. [OpenAI, Image generation](https://developers.openai.com/api/docs/guides/image-generation).

### Inferences

#### A. Bộ 5 mockup đề xuất — chưa tạo hoặc chạy trong lần nghiên cứu này

Chọn **một SKU/template và một size** cho vòng đầu. Mỗi case phải gồm đủ view/panel mà thiết kế của case thực sự có; nếu chỉ có Front thì không gọi là benchmark Front/Back/Sleeve hoàn chỉnh.

| ID | Nội dung và nguồn đánh giá | Lỗi chính cần bắt | Cách giữ tính độc lập |
|---|---|---|---|
| Q1 | Thiết kế chữ/logo nét rõ do designer chuẩn bị, có transcript và artwork gốc; mockup chứa nhiều vị trí chữ | Thay/mất/thêm ký tự, mirror chữ ở Sleeve, logo bị vector hóa sai | GT và font/source riêng chỉ người chấm giữ; restoration nhận mockup, không nhận file designer |
| Q2 | Chân dung được phép dùng, GT có mặt đủ rõ, đặt trong thiết kế có Front/Back/Sleeve theo spec | Drift mặt, mắt/miệng thay đổi, các instance của cùng asset khác nhau | GT giữ kín; crop/view dùng chấm không được thêm lại làm reference khi chưa đổi nhãn nhánh |
| Q3 | Repeat có motif định danh, anchor/notch, chu kỳ mm đã biết, đi qua seam Front–Back và seam Sleeve thích hợp | Sai chu kỳ/phase/scale/hướng, sai seam correspondence hoặc panel bị tráo | GT pattern và seam map do designer/xưởng xác nhận, không fit theo output để tăng điểm |
| Q4 | Artwork nhiều nét nhỏ và chữ nhỏ, có bản high-resolution; mockup được giảm detail có kiểm soát, giữ degradation recipe | Metadata DPI giả an tâm, upscale làm dính nét/halo hoặc bịa texture | Ghi resize/filter/compression/seed nếu có; GT chỉ dùng chấm. Không render rồi invert bằng chính map đó để coi là validation độc lập |
| Q5 | Ca Home Alone nguồn 1500×1500 đã biết local kém | Phản ứng với nguồn nghèo thông tin: model có bịa chữ/mặt/vùng khuất rồi tự nhận đúng không | Nếu không có artwork gốc thì đây là real-world challenge, không tính full-reference fidelity cho phần chưa biết; giữ nhãn unknown |

Q1–Q4 có thể lấy từ mockup thật của artwork đã biết; nếu tạo mockup tổng hợp, lưu renderer/map/view và đánh dấu synthetic. Để tránh “inverse crime”, cần ít nhất một view/landmark không dùng hiệu chỉnh, hoặc một nguồn render/ảnh độc lập. Nếu chỉ forward rồi inverse bằng cùng calibrated map, kết quả là kiểm tra self-consistency của pipeline, chưa chứng minh khôi phục từ áo thực.

#### B. Các nhánh tối thiểu và cách phân rã lỗi

Không đổi model hoặc pricing. Nhánh dưới chỉ là kế hoạch đánh giá khi có output hợp lệ, không phải cho phép phát sinh paid calls:

| Nhánh | Đầu vào/đầu ra | So sánh để trả lời |
|---|---|---|
| A: baseline hiện có | Local/calibrated projection hiện tại; dùng resize thường đã có, không generative upscale | Mức lỗi trước restoration; lưu artifact đang có, không sửa production |
| B: restoration hiện có | Cấu hình OpenAI hiện tại, không Real-ESRGAN; nếu đã có output thì dùng nguyên output đó | Chữ/mặt/motif được cải thiện hay đổi sai so với A? |
| C: B + Real-ESRGAN tùy chọn | Lấy đúng bytes output B, chỉ thêm upscale; nhánh đối chứng resize thường cùng kích thước | Upscale có giúp phần đã đúng mà không phá detail quan trọng? |

Giữ cùng template, transform, nguồn, transcript, masks, profile và final pixel dimensions cho các so sánh cần cùng kích thước. Nếu output API có kích thước khác, lưu ảnh native rồi tạo bản đánh giá bằng một recipe resize cố định và ghi rõ; không đánh đồng bản resized với output gốc.

Chấm sau từng stage: restoration-native, sau projection và sau upscale/export. Nếu chữ đúng trước projection nhưng sai sau đó, lỗi thuộc stage khác với chữ sai ngay ở output model. Với SAM 2/VTracer đã cài, chỉ đưa vào một ablation riêng nếu nghi mask hoặc tracing là nguồn lỗi; không đổi cả hai đồng thời rồi gán lợi ích cho OpenAI. Vai trò công cụ có nguồn tại [SAM 2](https://ai.meta.com/research/sam2/) và [VTracer](https://github.com/visioncortex/vtracer).

Vòng đầu dùng một artifact sẵn có cho mỗi nhánh/case để kiểm bộ chấm; không tuyên bố robustness của model từ một lần. Nếu sau này được phép chạy lại, lưu mọi attempt, cả fail/retry/reject và thời gian sửa tay; báo `first attempt` riêng với `selected final`. Chỉ báo median/range khi thực sự có nhiều lần đo; không bịa p95 từ 5 mẫu hoặc giấu chi phí các bản bị loại. API generation có tính ngẫu nhiên; manifest giúp audit và tái chấm, không bảo đảm generate lại cùng bytes.

#### C. Negative controls và người chấm độc lập

Trước khi tin metric, tạo bản kiểm thử có lỗi cố ý trên bản sao test khi được triển khai: một chữ sai; một portrait khác nhưng áo/nền giống; repeat lệch một phần chu kỳ và đúng một chu kỳ; tráo Front/Back hoặc Sleeve trái/phải; đổi scale motif; làm halo/dính nét. Bộ chấm phải bắt đúng gate tương ứng, kể cả khi similarity tổng vẫn cao. Positive control là artwork chuẩn không sửa và round-trip baseline chỉ resampling. Đây là thiết kế kiểm định evaluator, chưa thực hiện.

Người chấm xem crop đồng vị trí và kích thước vật lý, ẩn tên phương pháp, thứ tự A/B/C ngẫu nhiên. Một người có thể chấm lượt đầu rồi lượt hai sau khi đảo thứ tự; tốt hơn là hai người chấm và lưu bất đồng/adjudication. Không dùng cùng model restoration làm người phán quyết duy nhất. Có thể dùng model để gợi ý vùng lỗi, nhưng quyết định xuất vẫn theo manual all-panel approval hiện có.

#### D. Duplicate và reference leakage

- Chia nhóm theo **artwork gốc/design identity**, không chia theo tên file/panel/crop. Front/Back/Sleeve, crop khác, ảnh nén lại và phiên bản độ phân giải khác của cùng thiết kế phải nằm trong cùng nhóm.
- Dùng hash bytes để tìm duplicate chính xác theo [hashlib](https://docs.python.org/3/library/hashlib.html); thêm hash pixel sau decode/orientation/profile normalization theo recipe cố định để tìm file chỉ khác container/metadata. Hash này vẫn không tìm được mọi crop, recolor hoặc near-duplicate; dùng contact sheet và provenance review. Không tự xem là đã sạch leakage chỉ vì SHA-256 khác.
- Lập danh sách **toàn bộ reference thực gửi** theo thứ tự, gồm ảnh toàn áo, crop, thumbnail, mask, previous outputs và history trong multi-turn. Prompt chứa nguyên nội dung chữ hoặc có asset portrait đúng là một dạng trợ giúp cần khai báo.
- Tách điều kiện `mockup-only` khỏi `reference-assisted`. Đưa artwork designer/GT/crop GT cho model thì case đó không còn là blind restoration. Không đưa GT vào prompt để sửa rồi tiếp tục báo điểm như lần mù.
- Transcript do người chấm giữ để đánh giá; nếu production chủ ý dùng transcript như một constraint, giữ một nhánh assisted riêng và ghi điều đó. Không coi khả năng nhớ nhân vật nổi tiếng là bằng chứng truy hồi đúng bản artwork Home Alone cụ thể.
- Cố định test set và rubric trước khi tune prompt. Khi đã dùng Q1–Q5 để chọn prompt thì đó là development set; cần mockup khác giữ lại trước mọi tuyên bố tổng quát. Không thể chứng minh dữ liệu public chưa từng xuất hiện trong training; chỉ kiểm soát được leakage trong benchmark này.

#### E. Manifest tối thiểu — đặc tả, không tạo thêm file trong nhiệm vụ này

Một manifest cho run và các revision nối bằng `parent_revision`, giữ các nhóm trường sau:

| Nhóm | Trường cần giữ |
|---|---|
| Định danh | `run_id`, `case_id`, `design_group_id`, revision/parent, thời gian và timezone, trạng thái first-attempt/selected/rejected |
| Inputs | Filename/path hoặc asset ID, SHA-256 bytes, kích thước pixel, ICC/profile, orientation; vai trò và thứ tự ảnh thực gửi; mockup-only/reference-assisted; GT chỉ nằm ở evaluator |
| Recipe | App commit/version thực dùng; tool/model weights checksum; backend CPU/MPS/khác, dtype, resize/filter; template SKU/size/revision và checksum; map/masks/seam correspondences và checksum |
| API | Endpoint và model identifier đúng cấu hình hiện có; snapshot trả về nếu có, không đoán; parameters thực gửi, prompt nguyên văn + checksum, reference order, request ID; revised prompt chỉ nếu API thực trả; liên kết các bước multi-turn và retry |
| Stages/output | File native API, pre/post upscale, panel outputs; kích thước px/mm, PPI metadata, embedded profile; mọi checksum và asset → panel transform; unknown/generated masks liên quan |
| Evidence | Transcript/normalization, rubric/ROI version, scores từng ROI, coverage, lỗi hard gate, ảnh bằng chứng, reviewer/adjudication; không dùng điểm chưa đo |
| Đo lường | Stage wall time, tổng wall time, số paid requests/attempts, human active minutes; RAM/RSS/MPS metric và cách đo; usage thực trả; actual billed cost nếu đối chiếu được, nếu chưa có thì null |
| Duyệt và gói cuối | Danh sách panel phải có và panel được duyệt; người duyệt/thời gian; checksum đúng file được xem; member checksums PSD/PNG và trạng thái sealed; outer ZIP checksum được ghi ở nơi xác nhận bên ngoài ZIP |

Không ghi credentials/API keys hoặc signed URL còn hiệu lực vào manifest. Seed chỉ lưu nếu surface/model thực hỗ trợ và đã gửi; không tạo seed giả để gọi là deterministic. Nếu alias model không cho biết snapshot, ghi `snapshot_unknown`.

SHA-256 là bằng chứng toàn vẹn **khi so với một digest được tin cậy giữ riêng**, không tự tạo tính bất biến hoặc chữ ký. Manifest bên trong ZIP liệt kê các member khác; không cố nhét checksum của chính nó hoặc checksum ZIP vào chính payload đó. Digest ZIP và approval binding giữ ở cơ chế lưu/duyệt ngoài gói hiện có. Bất kỳ sửa pixel, profile, layer hoặc export lại sau approval phải tạo revision và duyệt lại, không ghi đè bản đã sealed. PSD render mà người duyệt thấy phải tương ứng PNG xuất cuối; hash PSD một mình chưa chứng minh PNG có cùng diện mạo.

#### F. Bảng kết quả hiện tại và phép đo sau này

| Hạng mục | Trạng thái 2026-10-27 |
|---|---|
| Q1–Q5, A/B/C: chất lượng, CER, seam error, portrait drift | Chưa chạy; không có số đo |
| Wall time / human active minutes | Chưa đo |
| Peak RSS / Metal allocation / memory pressure / swap | Chưa đo |
| Chi phí mỗi case / mỗi bộ được duyệt / retries | Chưa đo; không suy diễn từ bảng giá |
| Mô hình hoặc pricing của app | Không thay đổi |

Khi được triển khai: đo end-to-end từ lúc đọc nguồn đến khi sealed ZIP sẵn sàng; đồng thời tách API wait, CPU/MPS processing, export và active manual correction/approval. Ghi cold/warm run và tải nền. Nếu đo MPS stage, đồng bộ GPU trước/sau cửa sổ thời gian theo API backend thực dùng; lưu sample interval của bộ đo RAM. Peak từ sampling chỉ là peak quan sát được, không phải bảo đảm peak tuyệt đối.

Số tiền chỉ ghi khi có usage/billing tương ứng và phương pháp đối chiếu; usage đơn thuần chưa là chi phí đã thanh toán. Chi phí per accepted set phải tính cả attempt bị loại. Không dùng số đo NVIDIA/repo hoặc một Mac chip khác để gán thời gian/RAM cho Mac mini 16 GB này.

### Gaps

- Chưa thu thập 5 case, designer GT, reference inventory hoặc output immutable để chấm. Không phát sinh paid calls để lấp khoảng trống.
- Chưa xác nhận version/commit của SAM 2, VTracer, Real-ESRGAN, OCR hoặc cấu hình OpenAI cục bộ; không đọc app để tranh quyền làm việc với agent khác. Manifest là đề xuất cho lượt thực nghiệm sau.
- Chưa kiểm tra ZIP/PSD/PNG thật, cơ chế sealing/approval hiện có, hoặc near-duplicate giữa bộ mẫu. Không thể gọi bộ hiện tại là benchmark đã tái lập hay artifact đã được audit.

## 3. Tài liệu OpenAI, yêu cầu in/ICC và Mac 16 GB đặt ra giới hạn gì; nên làm bước nào trước?

### Takeaway

Multiple references, mask và output resolution là khả năng điều khiển API, không phải bảo đảm fidelity cho in. Ưu tiên bộ đánh giá nhẹ và artifact sẵn có; chỉ thêm inference nặng khi lỗi đã được phân loại và có phép thử cho thấy lợi ích. Màu cần đúng profile đích; không có printer/material ICC phù hợp thì chưa thể tuyên bố soft-proof chính xác.

### Cited Findings

#### OpenAI Image edits, đối chiếu trực tiếp ngày 2026-10-27

- Endpoint edits nhận tối đa 16 ảnh. API reference nêu `input_fidelity` phụ thuộc model: `gpt-image-1.5` có high/low, mini chỉ low; `gpt-image-2` bỏ tham số này. Không áp một bộ tham số cho mọi model. [OpenAI, Create image edit](https://developers.openai.com/api/reference/resources/images/methods/edit).
- Mask hướng dẫn chỉnh sửa, không bảo đảm biên chính xác; khi có nhiều ảnh, mask áp vào **ảnh thứ nhất**. Mask cần alpha, cùng size/format với ảnh edit theo guide. Tài liệu cũng ghi giới hạn chữ, consistency và bố cục chính xác. [OpenAI, Image generation](https://developers.openai.com/api/docs/guides/image-generation).
- Resolution phụ thuộc model: API reference liệt kê 1024×1024, 1536×1024, 1024×1536; một số model hiện hỗ trợ custom dimensions. Guide nêu cạnh là bội 16, tối đa 3840 px, tỷ lệ không quá 3:1, tổng 655,360–8,294,400 pixel; trên 2560×1440 còn experimental. Không hiểu thành mọi cấu hình hiện tại đều hỗ trợ 4K. [OpenAI API reference](https://developers.openai.com/api/reference/resources/images/methods/edit); [OpenAI guide, size constraints](https://developers.openai.com/api/docs/guides/image-generation).
- **Reference ordering chỉ có căn cứ theo model/surface:** tài liệu trong repo chính thức OpenAI cho fallback Image API ghi `gpt-image-1.5` giữ fidelity cao hơn cho năm ảnh đầu. Đây không phải quy tắc “Front phải đứng trước Back/Sleeve”, cũng không chứng minh cùng ưu tiên trên mọi model. [OpenAI skills, Image API quick reference](https://github.com/openai/skills/blob/main/skills/.system/imagegen/references/image-api.md).

Suy ra: giữ nguyên model/pricing được người dùng yêu cầu; ghi model và tham số thực dùng, không tự chuyển sang model đang là mặc định trên trang docs. Repo quick reference có phạm vi các model cũ/fallback CLI, còn guide hiện có thêm model/size khác; khác biệt này phải được đọc theo phạm vi, không biến quick reference thành hợp đồng chung. Không dùng nội dung web để tự thay cấu hình app.

#### Print provider và ICC

- Printful khuyến nghị sRGB và kiểm tra thông số theo loại in/sản phẩm; họ nhận PNG/JPG tùy phương pháp và nói JPG thường thuận tiện hơn cho AOP. Đây là yêu cầu nhà cung cấp, không phải lý do đổi gói PSD/PNG ZIP lưu trữ đã có. [Printful, Graphics and embroidery guide](https://www.printful.com/graphics-and-embroidery-guide).
- ICC White Paper 23 mô tả giữ RGB master, dùng profile phù hợp cho chuyển đổi và soft proof; workflow chính xác cần thiết bị được calibration/characterization. Bài hướng dẫn có ví dụ mô phỏng paper/ink và rendering intent. [ICC, RGB Color Managed Workflow](https://www.color.org/ICC_white_paper_23_RGB_Workflow.pdf).

Đề xuất áp dụng: lưu embedded source profile hoặc ghi rõ untagged/unknown; phân biệt **assign profile** với **convert profile**. Không gán sRGB cho bytes không biết nguồn rồi tuyên bố màu được bảo toàn. Soft proof chỉ là preview không phá master, dùng output ICC đúng printer–ink–material–process của xưởng nếu họ cung cấp, kèm rendering intent, black point compensation và display profile đã biết. Giữ file nộp xưởng theo yêu cầu xưởng, không tự convert thành CMYK/generic offset profile.

Nếu chưa có ICC của vật liệu/quy trình đích, chỉ có thể kiểm profile và gamut theo thông tin hiện có; không được gọi preview sRGB là dự báo màu vải. So swatch Front/Back/Sleeve trong cùng color-managed flat space; không đo màu pixel áo trong ảnh mockup dưới các bóng khác nhau rồi gọi đó là sai palette. Physical sample/đo màu là bước xác thực sau này, chưa đặt mua hoặc thực hiện ở đây.

#### Mac mini 16 GB

- Demo SAM 2 có đường MPS/CPU; tài liệu cảnh báo MPS memory có thể làm worker crash và bản Docker macOS của demo chạy CPU. Đây là bằng chứng có giới hạn backend, không là benchmark RAM/tốc độ ảnh đơn trên máy này. [SAM 2 demo, repo tác giả](https://github.com/facebookresearch/sam2/blob/main/demo/README.md).
- Trong `RealESRGANer.tile_process`, tác giả chia tile nhưng vẫn khởi tạo tensor output toàn kích thước. Tiling giảm workload trung gian mỗi tile, không loại hết memory cho input/output toàn ảnh. [Real-ESRGAN, mã tác giả](https://github.com/xinntao/Real-ESRGAN/blob/master/realesrgan/utils.py).
- PyTorch phân biệt memory do Metal driver cấp, gồm cached allocations/framework allocations; số đó không đồng nghĩa một mình tensor memory hay tổng RAM máy. Apple hướng dẫn theo dõi Memory Pressure, Compressed và Swap Used. [PyTorch, driver_allocated_memory](https://docs.pytorch.org/docs/main/generated/torch.mps.driver_allocated_memory.html); [Apple, Activity Monitor memory](https://support.apple.com/en-gb/guide/activity-monitor/actmntr1004/10.14/mac/15.0).
- PyTorch cảnh báo tắt high-watermark limit có thể gây system failure khi OOM toàn hệ thống. [PyTorch, MPS environment variables](https://docs.pytorch.org/docs/stable/mps_environment_variables.html).

### Inferences

#### P0 — làm trước, dùng artifact hiện có và giữ manual approval

1. **Khóa bằng chứng:** lập transcript chuẩn, ROI quan trọng, visibility/origin masks, map/template revision và checksum cho một bộ có đủ Front/Back/Sleeve. Chưa đọc được chữ/mặt ở nguồn thì yêu cầu reference tốt hơn hoặc ghi unknown; không chạy upscale lặp để biến sự đoán thành “đã phục hồi”.
2. **Chấm trước/sau từng stage:** exact text + CER, portrait review, motif scale/phase, seam correspondence, kích thước vật lý và source sampling. Làm reprojection theo calibrated map cố định và landmark hold-out; không thêm global SSIM mockup–flat UV.
3. **Gắn duyệt với bytes:** xem crop tại scale in đã điều chỉnh bằng thước/đơn vị vật lý; 100% zoom màn hình tự nó chưa bằng kích thước in. Duyệt riêng mọi panel rồi cả bộ; seal đúng revision/checksum PSD/PNG ZIP. Bất kỳ export lại nào cần revision mới.

#### P1 — benchmark tối thiểu trên Mac, sau khi đủ nguồn đánh giá

4. Chuẩn bị Q1–Q5 và negative controls. Tái chấm output có sẵn A/B/C trước; mọi paid generation còn thiếu nằm ngoài nhiệm vụ này. Chọn nhánh dựa vào hard-gate pass và thời gian sửa tay thực đo, không dựa ảnh tổng thể đẹp.
5. Xử lý tuần tự một panel/crop, giải phóng model khi chuyển stage; dùng mask đã duyệt để khỏi chạy SAM 2 lại. Chỉ upscale crop cần thiết nếu composition cho phép, giữ cùng approved asset khi đặt sang panel khác. Bắt đầu với tile phù hợp cài đặt sẵn rồi đo, không công bố một tile size “chắc chắn vừa 16 GB”. Chưa xác nhận chip, macOS, backend của Mac mini nên không mặc định MPS hoạt động.
6. Theo dõi peak process memory với công cụ/đơn vị được ghi rõ; nếu có MPS, ghi cả current/driver metric mà version hiện có hỗ trợ và memory pressure/swap của hệ thống. Không cộng RSS và Metal allocations một cách máy móc vì có thể chồng lấp trên bộ nhớ dùng chung. Giảm crop/batch/tile hoặc chuyển backend phù hợp khi đo cho thấy thiếu RAM; không tắt memory guard để ép chạy.

Ước lượng số học để hiểu quy mô, **không phải số đo RAM**: một buffer RGBA8 6048×6048 cần `6048 × 6048 × 4 = 146,313,216 bytes`, khoảng 139.5 MiB. Nhiều panel, float tensors, layer PSD, model, cache và bản copy có thể lớn hơn nhiều; kích thước PNG/ZIP nén không phản ánh working set. Tile inference vẫn có output toàn ảnh trong [mã Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/blob/master/realesrgan/utils.py).

#### P2 — chỉ thêm khi kết quả chỉ ra nhu cầu

7. Dùng VTracer cho nét/mảng đã được xác nhận đúng, rồi re-rasterize cùng scale để kiểm contour/chữ; nó không phải bộ sửa chính tả. Dùng SAM 2 để hỗ trợ mask khi mask là nút thắt, không để model mask tự xác nhận vùng chưa quan sát. Chưa cần thêm full 3D pipeline, face embedding, OCR nặng hoặc metric network mới chỉ để tạo một điểm số.
8. Khi có output ICC/vật liệu đích và yêu cầu xưởng, thêm soft proof có ghi profile/hash/intent; kiểm physical sample ở một đợt được cho phép sau. File được provider nhận không có nghĩa artwork fidelity đã được chứng minh.

Điều kiện dừng vòng thử đề xuất: các hard gate của một bộ full-panel đã được chấm độc lập, lỗi negative controls được phát hiện, unknown được công khai, và approval gắn đúng artifact. Nếu nguồn không đủ detail để quyết định chữ/mặt, dừng ở kết luận thiếu bằng chứng và tìm reference/asset gốc; không tiếp tục tối ưu metric trên vùng đó.

### Gaps

- **Đã xác nhận qua nguồn:** multiple-image edit và mask-first-image; mask không bảo đảm biên; giới hạn chữ/consistency/layout; resolution phụ thuộc model; reference order có tài liệu riêng cho một số model; yêu cầu kích thước/profile và giới hạn seam của Printful; giới hạn alignment của metric; tiling không loại output buffer toàn ảnh; cảnh báo MPS và cách đọc memory của Apple/PyTorch.
- **Chưa kiểm thử:** hiệu quả của rubric, OCR/CER thực tế, portrait fidelity, seam error, unknown coverage, Real-ESRGAN có giúp hay phá, RAM/time/cost trên Mac này, export/approval binding và soft proof vật liệu thật. Không có con số benchmark mới hoặc chứng nhận print-ready.
- Chưa biết model/snapshot đang cấu hình và API surface thực tế của app; tài liệu web không xác nhận request cụ thể sẽ được tài khoản hiện có chấp nhận. Không đổi model hoặc giá và không gọi thử trả phí.
- Chưa có printer/material ICC hoặc xác nhận calibration của màn hình. Chưa đủ căn cứ để hứa khớp màu thành phẩm hoặc khớp seam tuyệt đối.
