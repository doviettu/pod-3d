# Giữ bằng chứng, phục hồi artwork chính xác

**PODStudio nên giữ artwork quan sát được đáng tin, chỉ tổng hợp vùng thiếu, dựng chữ chính xác riêng và tham số hóa dải lặp tay áo.** Global OpenAI redraw hiện là baseline, chưa chứng minh trung thực với bản gốc. Geometry, mask và phép ghép local phải kiểm soát phần được giữ; prompt chỉ hướng dẫn phần tái dựng. Vùng khuất không có tham chiếu vẫn là unknown. Báo cáo tổng hợp nghiên cứu hoàn tất ngày 27/10/2026, kế thừa nghiên cứu trước; không cài model mới hoặc gọi API tạo ảnh có phí. Các thay đổi ứng dụng đã hoàn tất được ghi riêng bên dưới.

## Chức năng đã có chưa chứng minh fidelity

Theo cập nhật triển khai được cung cấp: chọn Front/Back/Sleeve tối đa ba panel; `reconstruct_artwork` nhận original + ratio guide; lưu native rồi optional upscale, đặt canvas cố định. Đã có duyệt cả sáu panel, ZIP bất biến gắn bản duyệt hiện hành/garment preview, calibrated 2D preview với hatch xám vùng unmapped, PSD chữ editable, logo, SAM2 và VTracer.

Trước đó **50 test Python logic/synthetic đã qua**; hôm nay lỗi reset UI được sửa, kiểm cú pháp và xác minh trực tiếp. Đây là dữ kiện từ phiên triển khai, không phải paper hay kiểm toán lại. **Chưa thử artwork OpenAI mới, in vật lý hoặc benchmark model nâng cao.** Masked compositor, OCR/font matching và repeat phase dưới đây đều là đề xuất mới.

## Geometry và lớp chữ phải giữ nguồn riêng

Nếu có mesh/UV/camera gốc, dùng inverse projection kèm visibility. Với raster, dùng homography cho vùng gần phẳng; piecewise affine hoặc TPS theo landmark cho biến dạng cục bộ. Loại tam giác lật, occlusion và vùng phóng đại quá mức; lấy mẫu từ original một lần. Không kéo mép để lấp unknown. Warp vẫn giữ bóng/nếp: “quan sát được” chưa đồng nghĩa biết màu mực gốc. ([OpenCV](https://docs.opencv.org/4.13.0/da/d54/group__imgproc__transform.html))

Duyệt flat base F rồi khóa portrait, chữ/logo và ranh band. Compositor lấy F ngoài edit mask, chỉ nhận generated pixels bên trong; feather chỉ trong vùng được phép. Pixel khóa phải bằng F trước resize/upscale. **Mask API không tự bảo đảm biên**; ảnh đầu phải là flat target, original reference đứng sau. Chân dung thiếu mắt/miệng cần asset thật hoặc nhãn reconstruction; không palette-clean toàn mặt. ([OpenAI mask](https://developers.openai.com/api/docs/guides/image-generation#edit-an-image-using-a-mask))

OCR offline ưu tiên Apple Vision trên crop raw và rectified; so candidate với language correction tắt/bật, người duyệt transcript. Confidence của chuỗi không phải từng ký tự; box accurate có thể chỉ bao từ. Hai câu `MERRY CHRISTMAS`, `YA FILTHY ANIMAL` là nội dung yêu cầu, không phải OCR đã đọc đúng. Không mớm chúng vào lượt OCR mù. ([Apple Vision](https://developer.apple.com/documentation/vision/vnrecognizetextrequest), [giới hạn box](https://developer.apple.com/documentation/vision/vnrecognizedtext/boundingbox(for:)))

Giữ luồng import thủ công 2–3 font: render đúng câu, cùng cap-height/baseline, so contour rồi chỉnh kerning. Tách duyệt transcript, appearance và xác nhận identity font; font giống không chứng minh font gốc. Kiểm license từng font cho POD. Ưu tiên native text, SVG outline khi cần; logo gốc/RGBA trước VTracer. Trace phải giữ counter, distress, component và alpha. ag-psd không tự redraw text/composite đầy đủ; reopen editor đích để kiểm fallback/layout. Logo RGBA trong PSD vẫn là raster. ([DaFont](https://www.dafont.com/faq.php), [VTracer 0.6.15](https://pypi.org/project/vtracer/0.6.15/), [ag-psd](https://github.com/Agamnentzar/ag-psd#readme))

Tay áo cần từng band với thứ tự, hướng, bề rộng; rectify patch, xác nhận chu kỳ P bằng nhiều motif rồi lưu phase φ theo cuff/seam: `(s−φ) mod P`. Không stretch strip toàn tay, không mirror trái/phải mặc định. Autocorrelation chỉ gợi chu kỳ; anchor giải nhầm P/2, 2P hoặc dịch nguyên chu kỳ. Thiếu chứng cứ thì không auto-repeat. Bleed tiếp tục pattern theo rập xưởng; so khớp tại stitch line, không mép canvas. ([OpenCV correlation](https://docs.opencv.org/4.13.0/d7/df3/group__imgproc__motion.html))

Trên Mac mini 16GB, xử lý tuần tự crop/panel, dùng preview trước, tránh giữ nhiều buffer lớn. Đo memory pressure/swap thực tế; không suy RAM inference từ dung lượng checkpoint. Tiling Real-ESRGAN vẫn giữ output tensor toàn ảnh. ([mã tác giả](https://github.com/xinntao/Real-ESRGAN/blob/master/realesrgan/utils.py))

## Repo có thật chưa đồng nghĩa chạy được trên Mac

Bảng phản ánh nguồn đã đối chiếu trong ghi chú, **không model nào được benchmark tại đây**.

| Hướng | Availability, license, runtime và quyết định |
|---|---|
| DeepIron | Trang tác giả có paper; chưa tìm thấy author code/weights, software license hay Mac runtime. Chỉ học phương pháp. ([nguồn](https://lava.kaist.ac.kr/?page_id=7340)) |
| OmniFabric | README còn chuẩn bị code/pretrained; chưa có runnable release/license/runtime để xác minh. Theo dõi. ([repo](https://github.com/humansensinglab/OmniFabric#code)) |
| FabricDiffusion | Code và weights public; README ghi X11, quyền weights chưa rõ; CLI mặc định CUDA, input patch 256². Không coi là exact extractor/Mac-ready. ([code/license](https://github.com/humansensinglab/fabric-diffusion), [weights](https://huggingface.co/Yuanhao-Harry-Wang/fabric-diffusion-print/tree/main)) |
| Garment3DGen | Có geometry, tác giả không chia sẻ texture-estimation code; CC BY-NC 4.0, CUDA/nvdiffrast/PyTorch3D. Hoãn. ([tác giả](https://github.com/nsarafianos/Garment3DGen/issues/12), [license](https://github.com/nsarafianos/Garment3DGen/blob/main/LICENSE.md)) |
| NI-Tex | Inference/checkpoints public; CUDA, môi trường H100/H200; header trộn non-commercial/community terms, quyền thương mại chưa rõ. Không ưu tiên Mac. ([code](https://github.com/SII-Hui/NI-Tex), [weights](https://huggingface.co/Hui-S-02/NI-Tex/tree/main/MODEL_CHECKPOINTS)) |
| MFLUX + Klein 4B | Có multi-image edit/quantization trên MLX; MFLUX MIT, weights 4B Apache-2.0. Ứng viên local về sau; chưa chứng minh fit 16GB/fidelity. ([MFLUX](https://github.com/mflux-community/mflux), [weights/license](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)) |

## Prompt builder local đi trước phân tích trả phí

Đề xuất `design_spec` được người duyệt → builder deterministic: kiểm enum, bounds, ảnh/thứ tự, evidence và approval rồi serialize thứ tự cố định. Cùng spec cho cùng prompt, **không bảo đảm cùng ảnh**. Unknown quan trọng chưa được duyệt thì chặn yêu cầu. Component chung Front/Back dùng một master đã duyệt và transform local; không generate độc lập hoặc suy Back từ Front.

Mini contract dưới đây là ví dụ chưa duyệt; tọa độ chuẩn hóa theo `flat_target`, P/φ theo pixel flat. Transcript chỉ cấp cho lớp chữ local, không chèn vào image prompt.

```json
{
  "panel":"Sleeve",
  "image_roles":[
    {"id":"I1","role":"flat_target"},
    {"id":"I2","role":"original_evidence"}
  ],
  "known":[{"fact":"cuff_down","source":"human:mockup-A"}],
  "unknown":["occluded_band"],
  "protected_regions":{"frame":"flat_target","boxes":[[0,0,1,0.2]]},
  "shared_components":[{"asset":"approved-master","transform":null}],
  "sleeve_band":{"side":"wearer-left","order":[],"period_px":null,"phase_px":null,"anchor":"cuff"},
  "approved_transcript":{"text":null,"destination":"local_text_layer_only"},
  "approved":false
}
```

Giữ tham số tách khỏi lời nhắc: `model=gpt-image-2.5-flare; size=1024x1024; quality=medium; n=1; output_format=png`, đúng mặc định 1K/medium hiện tại. Không thêm `input_fidelity`, `negative_prompt` hay seed chưa được xác lập hỗ trợ. “300 DPI” trong prompt không tạo detail; pixel/mm/DPI thuộc ghép/xuất local. ([model](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare), [edit API](https://developers.openai.com/api/reference/resources/images/methods/edit))

Bốn template dùng placeholder từ trường đã duyệt; vai trò ảnh đổi rõ theo chế độ. Masked/correction cần pipeline tương lai và mask alpha=0 tại vùng sửa.

**Global baseline — maskless draft**
```text
Create flat artwork for {panel}.
I1 is original garment evidence; I2 is geometry only.
Fill {target_rect}; retain white exterior.
Match {approved_subject_palette_layout}.
Remove folds, lighting and perspective. Use panel-visible motifs only.
Omit lettering/logos; continue approved backgrounds.
Unknowns: {approved_policy}. Return one flat draft.
```

**Masked restoration — flat target trước, original sau**
```text
I1 is the approved flat base; I2 is original evidence.
Restore only the transparent mask: {single_repair}.
Match {approved_scale_colors_landmarks}.
Preserve portrait identity and surrounding layout.
Generate no lettering/logos. Unknowns: {approved_policy}.
Return the full canvas; protected pixels are composited locally.
```

**Sleeve band targeting**
```text
Use only the {wearer_side} sleeve in I1 original evidence.
I2 defines geometry; fill {target_rect}, cuff downward.
Band order/widths: {bands}. Period/phase: {P_phi_frame}.
Align to {anchors}; preserve {motifs_palette_orientation}.
Exclude unseen torso motifs and all lettering/logos.
Unknowns: {approved_policy}. Return flat artwork.
```

**One-step correction**
```text
I1 is the reviewed flat target; I2 is original evidence.
Within the transparent mask, change only {region}: {one_correction}.
Preserve {identity_layout_palette_repeat_invariants}.
Generate no lettering/logos or unsupported details.
Return the full canvas; retain protected pixels locally.
```

Web app không tự gọi internal Codex subagents. Optional vision→JSON cần model khác hỗ trợ Structured Outputs, thêm request/chi phí ngoài panel allowance và người duyệt trước generate; schema đúng không bảo đảm quan sát đúng. Ưu tiên builder local; không append ghi chú tự do. ([prompting/compositing](https://developers.openai.com/api/docs/guides/image-prompting#prompting-fundamentals), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs))

## Ưu tiên theo bằng chứng, kiểm native trước upscale

| Ưu tiên | Đề xuất |
|---|---|
| Ngay | Chốt source/unknown, transcript, flat base; builder local; duyệt native thấp phân giải trước upscale. |
| Tiếp | Geometry/masked compositor, Vision/font overlay, repeat phase; kiểm SVG/PSD và seam theo xưởng. |
| Hoãn | Model local mới, full 3D, auto-font, paid vision; chỉ xét sau lỗi thực đo. |

Phép A/B tương lai gồm **hai ca × hai prompt = bốn attempt**: Front portrait A/B, Sleeve B/A; A nguyên prompt hiện tại, B builder mới. Trong từng cặp giữ cùng bytes ảnh, thứ tự, guide, mask=null, compositor/placement, model, 1024², medium, n=1 và upscale tắt; **chỉ prompt đổi**. Không thêm crop/reference, vision hoặc correction; lỗi không gọi bù. N=1/nhánh không chứng minh consistency; masked restoration cần thử riêng.

Offline checks đề xuất: exact approved transcript và CER trên chữ nhìn thấy; không tính chữ khuất được cung cấp là OCR đúng. Kiểm protected pixels bằng flat base trước upscale; baseline chưa có lock phải ghi N/A. Duyệt portrait, period/phase, orientation và stitch-line correspondence riêng. Reproject bằng map cố định, landmark giữ lại để chấm; không fit warp theo output. Lưu provenance/unknown, hash input/prompt/output, parameters, reviewer, thời gian sửa tay, usage và billing thực có. **Mọi gate/ngưỡng là đề xuất chưa đo**, không gộp thành điểm chất lượng tùy ý.

Sau native approval mới so upscale với resize thường cùng kích thước; kiểm lại mặt/chữ/halo/repeats. Độ nét tăng không chứng minh detail đúng. Chưa có physical print/ICC vật liệu thì chưa xác nhận màu và seam thành phẩm. ([Perception–Distortion](https://arxiv.org/abs/1711.06077), [ICC](https://www.color.org/ICC_white_paper_23_RGB_Workflow.pdf))

## Kết luận

Đơn vị cần duyệt là artwork kèm bằng chứng và giới hạn phục hồi. Nếu nguồn thiếu chữ, mặt hoặc phase, kết quả hợp lệ là pending/reference-needed; prompt dài hơn không bổ sung quan sát. Giữ gate sáu panel và ZIP hiện hành để mọi cải tiến sau này phải qua cùng bản duyệt trước khi xuất.
