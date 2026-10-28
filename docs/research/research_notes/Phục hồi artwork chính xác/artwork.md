# Phục hồi garment mockup thành flat 2D print artwork chính xác — PODStudio

Ngày đối chiếu: **27/10/2026**. Máy đích: Apple Silicon Mac mini 16 GB unified memory. Đây là kiểm chứng nguồn và thiết kế thí nghiệm, **không phải benchmark**.

Bối cảnh do người dùng cung cấp, chưa audit lại app: unwrap → bands/tilefill → paletteclean → RealESRGAN đang mất chữ, mặt loang, tay áo lặp và kéo giãn. Nhánh mới OpenAI selected Front/Back/Sleeve nhận original mockup + neutral aspect guide, yêu cầu clean textless artwork, giữ native artwork trước optional upscale/placement; chưa có real paid test. Báo cáo nền đã đọc: [Từ mockup đến rập in](/Volumes/ViTu/Claude%20Codex/POD/reports/Từ%20mockup%20đến%20rập%20in.md). Notes này bổ sung kiểm chứng code/weights và xử lý fidelity, không lặp workflow sản xuất tổng quát.

Quy ước: **Confirmed** = đã đọc trực tiếp tài liệu/file hoặc listing public; **Inference** = suy luận/đề xuất; **Untested** = chưa chạy. Listing checkpoint không chứng minh tải thành công, checksum hợp lệ hay inference tái lập. GitHub connector lỗi transport; đã dùng web để đọc public author repositories/raw source. Các trang main và cache không thay thế snapshot bất biến cùng commit.

## 1. Cần giữ điều gì để gọi là phục hồi chính xác?

### Takeaway

Đề xuất ưu tiên **geometric unwarping + giữ vùng quan sát đáng tin + restoration có mask và reference thật**. Global generative redraw có thể tạo thiết kế tương tự; không đủ làm bằng chứng lấy lại bản gốc, nhất là chân dung in trên áo, chữ và phase của dải tay áo.

### Cited Findings

| Hướng | Bằng chứng đã kiểm tra | Giới hạn liên quan |
|---|---|---|
| Geometric unwarping | OpenCV mô tả destination-to-source mapping và interpolation; TPS của scikit-image ước lượng phép biến dạng từ các cặp điểm. [OpenCV](https://docs.opencv.org/4.13.0/da/d54/group__imgproc__transform.html), [TPS example](https://scikit-image.org/docs/stable/auto_examples/transform/plot_tps_deformation.html) | Lấy mẫu lại pixel nguồn; không phải mô hình suy ra artwork bị che. |
| Global redraw | OpenAI ghi nhận khó khăn với text placement/clarity, recurring-character consistency và bố cục chính xác. [Limitations](https://developers.openai.com/api/docs/guides/image-generation#limitations) | Không có cam kết fidelity tuyệt đối cho mẫu POD này. |
| Masked editing | API mask dùng vùng alpha bằng 0 để chỉ nơi sửa; nhiều ảnh thì mask áp vào ảnh đầu tiên, cùng kích thước với ảnh đó. [Images edit API](https://developers.openai.com/api/reference/java/resources/images/methods/edit) | Cần compositor local để tự bảo đảm vùng khóa không đổi. |
| Reference giữ mặt | Cookbook có ví dụ high input fidelity giữ khuôn mặt tốt hơn standard mode. [Face preservation](https://developers.openai.com/cookbook/examples/generate_images_with_high_input_fidelity#face-preservation) | Ví dụ provider, không phải phép đo chân dung được in, biến dạng và che trên áo. |
| Repeat synthesis | FabricDiffusion dùng circular padding; paper vẫn cho phép user-guided tiling và ghi nhận lỗi với non-repetitive patterns/chi tiết print phức tạp. [Paper §§3.3.3, 3.4, 5](https://arxiv.org/html/2410.01801) | Tileable không đồng nghĩa đúng số motif, mật độ hay phase gốc. |

### Inferences

**Phân biệt “known” với “unknown”.** Gọi S là ảnh nguồn, W là correspondence tới canvas phẳng, F là ảnh lấy mẫu từ S qua W. Chỉ vùng vừa nhìn thấy, không che khuất, đủ chi tiết và có correspondence đáng tin mới được gọi là observed. F vẫn chứa ảnh hưởng ánh sáng/nếp vải; “giữ pixel” không đồng nghĩa đã biết reflectance/artwork gốc.

Một phần bị tay người che, mặt sau không chụp, họa tiết mất trong nếp gấp hoặc clipped highlight là **unknown**. Pattern lặp chỉ cho phép suy ra có điều kiện nếu có căn cứ về tính tuần hoàn. Generated views, symmetry và prompt không tạo thêm quan sát thật. Crop khác của cùng ảnh giúp model thấy chi tiết, nhưng không cung cấp pixel bị che mới. Nhiều ảnh thật của cùng áo/cùng thiết kế có thể tăng coverage; ảnh áo tương tự hoặc mặt sau khác thiết kế không được nhập làm cùng evidence.

**Geometric mapping: chọn mức nhỏ nhất có đủ thông tin.**

- Nếu có chính mesh, UV, camera và mockup renderer gốc: inverse-project trên geometry đó, dùng visibility/depth để bỏ self-occlusion. Mesh ước lượng lại không tương đương geometry gốc.
- Với một raster mockup: affine/homography cho vùng gần phẳng; TPS hoặc piecewise affine mesh cho biến dạng mềm. Một homography toàn áo không mô tả hết thân cong và tay áo.
- Bắt đầu một map thưa với anchors ở contour và nội dung nhìn rõ. Piecewise affine dễ giới hạn ảnh hưởng theo vùng; TPS phù hợp biến dạng mượt nhưng có thể kéo lệch xa anchors. Không mặc định TPS luôn tốt hơn.
- Tách các mặt nhìn thấy trước khi warp. Không nối qua occlusion; kiểm tra Jacobian/triangle orientation, lỗi reprojection và mức phóng đại cục bộ. Map lật tam giác hoặc phóng rất lớn phải giảm confidence, không fill rồi coi là source.
- Resample RGB một lần từ original bằng map cuối. Tạo canvas unknown kèm validity/confidence mask, thay vì kéo mép ảnh để lấp hết.
- Warp có interpolation nên không hứa byte-identical với ảnh chụp. Cam kết phù hợp là giữ nguyên **F đã được duyệt** ở vùng khóa sau bước warp.

**Portrait identity và chữ.**

Chân dung phải đánh giá ở hình mặt **in trên áo**, không phải khuôn mặt người mặc. Giữ riêng crop, đường mắt/mũi/miệng, tỉ lệ và silhouette; ưu tiên warp/copy có kiểm soát. Nếu thiếu mắt/miệng vì che khuất, lấy artwork/ảnh tham chiếu thật; nếu không có, ghi rõ phần tái dựng phải duyệt thủ công.

Mặt loang có thể phát sinh ở fill, palette quantization, shading removal hoặc upscale; hiện chưa có ảnh từng stage để kết luận nguyên nhân. So sánh trước/sau từng stage, không sửa bằng thêm face model mặc định. Không ép vùng da/gradient chân dung qua paletteclean dành cho mảng màu phẳng. Có thể thử giảm shading tần số thấp trên nền, nhưng bóng tối và màu mực tối có thể mơ hồ từ một ảnh.

Textless là mục tiêu chủ động của nhánh mới: mất chữ ở đầu ra này không tự chứng minh lỗi. Nếu bộ in cuối phải khớp mockup, cần lớp chữ riêng với transcript được duyệt, font/shape/kerning/vị trí được kiểm tra. OCR chỉ hỗ trợ đọc, không xác minh logo/font hoặc những ký tự không thấy. Giữ text mask để chấm textless artwork công bằng.

**Tay áo periodic bands, repeat phase/density.**

1. Phân đoạn từng band; ghi thứ tự, hướng, bề rộng và boundary dọc trục tay áo. Band trang trí khác nhau không phải cùng một tile.
2. Rectify một patch ít méo trước; đo period T trong hệ tọa độ áo phẳng. Dùng autocorrelation để đề xuất, kiểm tra bằng màu/motif để tránh chọn nhầm T/2 hoặc 2T.
3. Dùng tối thiểu vài chu kỳ nhìn rõ để kiểm tra tính tuần hoàn. Một đoạn độc nhất hoặc motif thay đổi dọc tay không đủ căn cứ auto-repeat.
4. Lưu period T và origin/phase phi. Khi tái dựng, lấy mẫu theo (s - phi) modulo T, không resize một strip tới toàn chiều dài tay.
5. Giữ density theo số motif trên khoảng chiều dài đã calibration; nếu không có scale vật lý, chỉ báo được mật độ tương đối. Neutral aspect guide không bổ sung mm hoặc geometry drape.
6. Khóa phase ở boundary/đường may có evidence. Không mirror toàn bộ tay trái sang phải nếu có chữ, portrait hoặc ornament có hướng. Tay khác bị khuất vẫn là unknown.
7. Nếu band bị che: dùng repeat cùng band có chứng cứ hoặc thêm reference thật; không lấy tile từ vùng khác chỉ vì cùng màu.

**Boundary bleed.**

Tách contamination từ nền/mannequin/cổ/cuff khỏi print. Map validity phải loại các pixel đó trước interpolation; không dùng border replicate để biến chúng thành “mực in”. Khi blend, chỉ feather trong dải transition được phép sửa, không qua mắt/chữ/đường band cần khóa. Xử lý alpha nhất quán để tránh viền màu matte.

Bleed sản xuất là bước local sau artwork: tiếp tục đúng pattern/phase vượt cut line theo thông số xưởng. Không dùng màu background của mockup hay circular wrap của cả canvas làm bleed. Phase khớp đường ráp phải được xét tại seam line, không phải mép ngoài canvas/bleed. Chưa có template/xưởng đích nên không đặt một số mm chung.

### Gaps

- Không có artwork gốc và ảnh từng stage để đo lỗi hiện tại hoặc xác định stage làm hỏng mặt.
- Chưa biết source coverage, geometry/camera gốc, số chu kỳ thực trên tay và kích thước vật lý.
- Chưa có bằng chứng thực nghiệm rằng prompt mới giữ portrait hay exact band phase.
- Không phương pháp được kiểm tra nào cung cấp chứng cứ về đúng nội dung gốc ở pixel hoàn toàn chưa quan sát.

## 2. Repo, code, checkpoint, license và Mac: thực sự có gì?

### Takeaway

FabricDiffusion có inference và checkpoint public, nhưng code hiện tại không phải exact artwork extractor. OmniFabric chưa phát hành implementation; Garment3DGen thiếu texture-estimation code. NI-Tex là bổ sung mới có code/weights thật nhưng CUDA và licensing khiến nó không phải lựa chọn đầu cho POD trên Mac 16 GB.

### Cited Findings

| Dự án | Code/weights đã xác minh | License và runtime evidence | Quyết định |
|---|---|---|---|
| **DeepIron** | Trang tác giả có paper/arXiv/video; chưa tìm thấy author repo hoặc checkpoint qua các liên kết đó. [Author project](https://lava.kaist.ac.kr/?page_id=7340) | Chưa có software/weight license hay Mac runtime để kiểm tra. Paper không phải license cho implementation. | Nghiên cứu phương pháp; chưa là dependency có thể tích hợp. |
| **OmniFabric, 2026** | Root public chỉ hiện assets và README; README nói code/pretrained models đang chuẩn bị. [Tree](https://github.com/humansensinglab/OmniFabric), [Release status](https://github.com/humansensinglab/OmniFabric#code) | Chưa thấy software/weight license hoặc runnable release. | Theo dõi, không lên kế hoạch cài ngay. |
| **FabricDiffusion** | Có inference_print.py, inference_texture.py, pipeline.py và hai model repository public. [Code](https://github.com/humansensinglab/fabric-diffusion), [Print files](https://huggingface.co/Yuanhao-Harry-Wang/fabric-diffusion-print/tree/main), [Texture weights](https://huggingface.co/Yuanhao-Harry-Wang/fabric-diffusion-texture/tree/main/unet) | README công bố X11; weight licensing chưa rõ từ model cards đã đọc. CLI mặc định cuda:0. [License statement](https://github.com/humansensinglab/fabric-diffusion/blob/main/README.md#license), [CLI](https://github.com/humansensinglab/fabric-diffusion/blob/main/inference_print.py) | Có artifact thật; chỉ ứng viên normalize patch, chưa có lý do thay toàn pipeline. |
| **Garment3DGen** | Có geometry code; owner xác nhận không chia sẻ texture-estimation code. [Owner comment, 24/10/2024](https://github.com/nsarafianos/Garment3DGen/issues/12) | CC BY-NC 4.0. loop.py chọn CUDA trực tiếp và import nvdiffrast/PyTorch3D. [License](https://github.com/nsarafianos/Garment3DGen/blob/main/LICENSE.md), [Runtime code](https://github.com/nsarafianos/Garment3DGen/blob/main/loop.py#L63) | Không dùng như complete open-source texture restoration pipeline cho POD. |
| **NI-Tex, CVPR 2026** | Có inference.py; author-linked HF có step_100K.ckpt và step_200K.ckpt, mỗi file khoảng 19.8 GB. [Code](https://github.com/SII-Hui/NI-Tex/blob/main/inference.py), [Weights](https://huggingface.co/Hui-S-02/NI-Tex/tree/main/MODEL_CHECKPOINTS) | README môi trường CUDA 12.4/H100-H200; inference có CUDA autocast. Header có Hunyuan non-commercial terms và nhắc community terms. [Setup](https://github.com/SII-Hui/NI-Tex), [Source header/runtime](https://raw.githubusercontent.com/SII-Hui/NI-Tex/main/inference.py) | Học uncertainty-guided baking; không coi là Mac-ready hay thương mại permissive. |
| **FLUX.2 Klein 4B + MFLUX** | BFL có weight .safetensors thật; MFLUX có edit CLI mặc định 4B, nhận image_paths và quantize. [Weights](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/tree/main), [Actual edit CLI](https://github.com/mflux-community/mflux/blob/main/src/mflux/models/flux2/cli/flux2_edit_generate.py) | Weight 4B Apache-2.0; MFLUX MIT, tác giả triển khai MLX. Không nhầm với license 9B. [Weight license](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/blob/main/LICENSE.md), [MFLUX license](https://github.com/mflux-community/mflux/blob/main/LICENSE), [MLX implementation](https://github.com/mflux-community/mflux/tree/main/src/mflux/models/flux2) | Dự phòng local cụ thể hơn việc “đổi CUDA sang MPS”; chất lượng phục hồi trên mẫu POD vẫn untested. |

Các chi tiết có thể thay đổi quyết định:

- **DeepIron không phải deterministic UV unwrap.** Paper dùng latent mapping/VAE-StyleGAN và normal input; thí nghiệm giả định predefined sewing patterns và body ground truth, có giới hạn ngoài training distribution. [Paper §§3, 6](https://arxiv.org/html/2310.15447v2). Bản author project ghi Eurographics 2024 Short Paper; không dùng năm arXiv 2023 để khẳng định một release software.
- **OmniFabric** dùng estimated mesh, VLM để khởi tạo coarse texture và refinement trong sewing-pattern UV với 3D positional features. Đây là phương pháp được paper mô tả, không phải code đã chạy thử. [Paper](https://arxiv.org/abs/2609.30234).
- **FabricDiffusion code** resize input patch thành 256×256; texture branch đổi Conv2d padding sang circular; print branch chạy diffusion rồi suy alpha từ RGB brightness. [pipeline.py, load_patch_data/flatten_print](https://github.com/humansensinglab/fabric-diffusion/blob/main/pipeline.py). Vì vậy không được hiểu output RGBA là segmentation chính xác.
- HF texture listing có file diffusion_pytorch_model.safetensors khoảng 1.72 GB. Print root có unet/text_encoder/vae và tổng 2.13 GB; trang chi tiết print safetensors không truy cập được qua web tool nên chưa xác minh byte-level file đó. [Texture file listing](https://huggingface.co/Yuanhao-Harry-Wang/fabric-diffusion-texture/tree/main/unet), [Print root](https://huggingface.co/Yuanhao-Harry-Wang/fabric-diffusion-print/tree/main).
- README FabricDiffusion có command fabric-diffusion-print nhưng hyperlink “print” trỏ fabric-diffusion-logo. Đây là mismatch thực tế trong README, không nên tự động theo hyperlink đó khi chuẩn bị thử. [README raw, dòng 85–91](https://raw.githubusercontent.com/humansensinglab/fabric-diffusion/main/README.md).
- MFLUX công bố multi-image editing và quantization; số memory trong phần small-text-encoder được đo trên **M4 Pro 64 GB**. Không chuyển những số đó thành cam kết peak RAM/tốc độ Mac mini 16 GB hoặc multi-reference edit. [Implementation author's notes](https://github.com/mflux-community/mflux/blob/main/src/mflux/models/flux2/README.md).

### Inferences

**Khả năng Mac phải chia ba mức.**

1. Geometric warp/composite/repeat có thể thiết kế bằng CPU, không cần diffusion GPU. Đó là suy luận từ phép toán và API, chưa đo hiệu năng ứng dụng này.
2. FabricDiffusion có device parameter và dùng Diffusers float16, nên port MPS là giả thuyết có cơ sở để thử sau; CLI có --device không chứng minh mọi operator/dtype chạy được. Dòng “switch to mps” tự sinh trên HF không phải test của tác giả.
3. Garment3DGen chọn CUDA trực tiếp; NI-Tex có CUDA autocast/custom rasterizer. Không thể coi đổi một chuỗi device là đã port Mac. MFLUX cung cấp implementation MLX riêng, là evidence tốt hơn cho đường Apple Silicon; vẫn không chứng minh fit/tốc độ/fidelity trên máy đích.

Không nhầm **download size**, **loaded weights**, **activation peak**, **VRAM NVIDIA** và **unified memory còn trống trên Mac**. Ví dụ tự tính: canvas 6000×6000 RGBA uint8 khoảng 144 MB; float32 khoảng 576 MB, chưa có map/buffer/model. Dùng preview 1024 trước; final composite theo tile/chunk nếu thư viện hiện có hỗ trợ. Chưa cần thêm runtime mới chỉ để thay placement.

NI-Tex gợi ý hữu ích là ưu tiên view/texel theo uncertainty, không phải dùng tất cả view ngang nhau. Với PODStudio, có thể bắt đầu đơn giản bằng validity + reprojection/stretch confidence và lựa chọn reference tốt nhất; chưa cần port toàn bộ neural uncertainty model.

### Gaps

- Chưa download, checksum, install hay chạy bất kỳ checkpoint nào.
- DeepIron: chưa tìm thấy official software/weights, không khẳng định chúng không tồn tại ở mọi nơi.
- OmniFabric: chưa có release công khai để xác minh CUDA/MPS/memory.
- FabricDiffusion: X11 statement trên GitHub-rendered README không hiện ở raw cache đã đọc; root không có LICENSE riêng; model cards chưa giải quyết đầy đủ quyền weight/dependencies. Chưa kết luận commercial clearance.
- NI-Tex: header trộn tham chiếu non-commercial/community, không thấy license riêng bao quát toàn repo/weights trong listing; cần tác giả làm rõ trước sản phẩm thương mại. Kích thước checkpoint không phải yêu cầu RAM inference đã đo.
- MFLUX: đã kiểm tra CLI thật và artifact model thật, chưa tái lập model internals hoặc benchmark trên máy này.
- Các metrics/paper demos về texture realism không phải kiểm định portrait identity, exact typography, sleeve phase hay file print ở kích thước xưởng.

## 3. PODStudio nên đổi gì, và chỉ thử những gì?

### Takeaway

Giữ quyết định mới “native artwork → optional upscale/placement”, nhưng thêm khả năng khóa observed pixels và xử lý repeat bằng thông số hình học. Trong vòng đầu chỉ cần ba thí nghiệm nhỏ, chưa cần đổi sang một hệ 3D hoặc chạy nhiều model.

### Cited Findings

- OpenAI API có mask/multi-image input; input_fidelity phụ thuộc model. gpt-image-2 tự dùng high fidelity và phải bỏ tham số này; không áp cấu hình cũ một cách máy móc. [Image input fidelity](https://developers.openai.com/api/docs/guides/image-generation#image-input-fidelity).
- API reference hiện ghi gpt-image-1 đã shutdown 23/10/2026. Vì chưa biết exact selected model của app, cần kiểm tra ID còn hoạt động trước paid test; không suy ra app đang dùng model đó. [Current edit API](https://developers.openai.com/api/reference/java/resources/images/methods/edit).
- Real-ESRGAN demo tách face_enhance dùng GFPGAN khỏi nhánh upscale thường. Không có evidence app đang bật tùy chọn này. [Author inference code](https://github.com/xinntao/Real-ESRGAN/blob/master/inference_realesrgan.py#L110).

### Inferences

**Delta nhỏ đề xuất, chưa triển khai:**

- Giữ original mockup làm evidence. Neutral aspect guide chỉ mang canvas/aspect; không coi nó là correspondence hay sewing-pattern calibration.
- Sau warp có F_base, trusted mask và unknown mask. Chữ, portrait, band boundaries có vùng protected riêng; không global tilefill/paletteclean lên những vùng đó.
- Global output mới dùng như proposal ở vùng thiếu/nền. Khi mask edit: ảnh đầu là flat F_base cần sửa; reference sau là original và, nếu có, ảnh thật bổ sung đúng vùng. Gán rõ Front/Back/Sleeve để model không trộn mặt.
- Compositor local thực hiện F_out = F_base ngoài edit mask; chỉ lấy generated pixels trong mask. Feather nếu cần chỉ nằm phía được phép sửa. So L-infinity với F_base ở protected region phải bằng 0 trước upscale: đây là bảo đảm của thao tác copy, không phải lời hứa model.
- Nếu cần deshadow vùng protected, đó là thao tác khác phải được duyệt và đo riêng; không vừa sửa màu vừa gọi là pixel giữ nguyên.
- Tách portrait, chữ/logo, periodic bands và nền thành các loại xử lý có sẵn; không nhất thiết xây layer editor mới. Giữ mapping/period/phase/provenance tối thiểu cùng kết quả để có thể truy ngược.
- Chấm native output trước. Upscale chỉ sau khi nội dung đạt; so với resize không sinh chi tiết để phát hiện mặt/chữ bị biến đổi. Không dùng upscale để “sửa” missing content; không mặc định bật face enhancement.
- Chưa thêm MFLUX/FabricDiffusion vào app. MFLUX là lựa chọn local đã có artifact để cân nhắc sau, không phải thí nghiệm thứ tư bắt buộc.

**Ba thí nghiệm bounded — đề xuất cho lượt sau, không thực hiện trong nghiên cứu này. Các ngưỡng là acceptance criteria tự đề xuất, không phải kết quả đo.**

| Thí nghiệm | Phạm vi/cách làm | Pass và stop criteria |
|---|---|---|
| **E1 — giữ observed content** | 3 mẫu nhỏ: portrait+text, sleeve bands, vùng mép/shadow. Nếu có artwork gốc, dùng nó để tạo/đối chiếu test có ground truth; giữ bản đích khỏi input phục hồi. So unwrap hiện tại với một map sparse piecewise-affine; chỉ thêm TPS nếu có biến dạng mượt mà map đầu không diễn tả được. Preview 1024, chưa fill, paletteclean hay upscale. Tối đa 20 phút đặt anchor/mẫu. | Không foldover. Landmark reprojection median ≤1% cạnh dài panel ở observed mask; portrait không mất đặc điểm và chữ nhìn thấy không mất nét. Phải tốt hơn baseline ở ít nhất 2/3 mẫu. Dừng chỉnh geometry nếu không đạt trong ngân sách hoặc source không có chi tiết; không che lỗi bằng fill. |
| **E2 — sleeve repeat có phase** | Một sleeve có ít nhất 3 chu kỳ nhìn rõ; rectify 2 cửa sổ ít méo. Tối đa 2 period candidates; khóa thứ tự band, T, phi và hướng. Dựng phần missing cùng band, chấm ở seam/anchor có evidence. Không gọi model. | Sai lệch period ≤5%, phase tại anchor ≤0.05T, không nhân đôi/cắt mất motif, boundary không chảy sang band khác. Dừng auto-repeat nếu hai cửa sổ không thống nhất, họa tiết không thật sự periodic hoặc số chu kỳ quan sát không đủ; ghi unknown và yêu cầu source thật. |
| **E3 — global so với masked/reference** | 2 ca đã có native F_base: một portrait, một band/boundary. Mỗi ca 1 output global từ nhánh mới và 1 output masked có source references thật, compositor khóa vùng trusted. Cùng model/quality/output size, không upscale; tổng tối đa **4 requests**, không reroll. Chỉ chạy ở lượt được cho phép dùng paid API; lượt này không gọi. | Protected pixels phải bằng F_base trước upscale; portrait không đổi identity, không thêm/sửa chữ ngoài phạm vi, density/phase qua gate E2, boundary không nhiễm nền. Stop ngay khi vi phạm vùng khóa/identity. Chỉ tiếp tục tích hợp nếu có cải thiện cả 2 ca và giảm ≥30% thời gian sửa tay so global. N=1 mỗi nhánh chỉ là feasibility gate, chưa chứng minh độ ổn định production. |

Ở E3, nếu không có reference thật thứ hai, có thể dùng crop original nhưng phải ghi là **cùng nguồn**, không công bố kết quả “multi-view recovery”. Nếu không có artwork gốc, chỉ chấm observed region, reprojection, lỗi identity/typography và thời gian chỉnh; không báo accuracy của toàn artwork hoặc phần hidden.

**Tách ba trạng thái khi duyệt:** observed/warped; inferred repeat có giả định; generated/unknown. Tỉ lệ unknown nên báo riêng theo diện tích, đặc biệt khi nằm trong portrait/chữ. Không làm một bản trông hoàn chỉnh rồi xóa dấu vết vùng đã tưởng tượng.

### Gaps

- Cả E1–E3 chưa chạy; không có chất lượng, thời gian, peak memory, chi phí hoặc tỉ lệ pass mới.
- Exact selected model ID, fidelity settings và mask support của implementation mới chưa audit trong lượt này.
- Chưa kiểm tra real production seam tolerance/bleed của xưởng và chưa thử in vật lý.
- Giới hạn khó tránh: nếu không có reference chứa nội dung bị che, phần đó chỉ là reconstruction giả định; không được gọi là artwork gốc được xác thực.
