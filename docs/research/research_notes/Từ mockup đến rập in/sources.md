# Ghi chú nguồn

Đối chiếu trực tiếp ngày 26/10/2026; nghiên cứu song song bị giới hạn sử dụng nên thực hiện tra cứu trực tiếp. Các agent lỗi đã đóng, không có kết quả nghiên cứu từ chúng.

- GoCustom: https://gocustom.com/faq/faqs/can-you-produce-garments-from-ai-generated-designs — hướng dẫn tách đồ họa 2D từ mockup, không phải chứng minh AOP end-to-end.
- HTT Apparel: https://www.httapparel.com/artwork-faq — dùng mockup làm tham chiếu, dựng lại artwork/từng panel; nhấn mạnh giới hạn upscale chữ/logo.
- Printful: https://help.printful.com/hc/en-us/articles/50264019148177-How-should-I-prepare-my-print-file-for-the-best-results — template theo sản phẩm và yêu cầu xuất. Không áp dụng chung DPI/format cho mọi xưởng.
- Subliminator: https://www.subliminator.com/blog-posts/how-to-prepare-all-over-print-designs — size, bleed, seam allowance, dung sai ráp; nguồn nhà cung cấp.
- DIY Printz: https://diyprintz.com/pages/diy-printz-studio — ví dụ công bố skill nối bước; nguồn marketing, luồng chính artwork→mockup/DTF.
- DeepIron: https://arxiv.org/abs/2310.15447 — texture unwarping vào sewing patterns; chưa xác minh triển khai dùng ngay.
- OmniFabric: https://github.com/humansensinglab/OmniFabric — README hiện nói code/model chuẩn bị phát hành; abstract về coarse init + UV refinement.
- Garment3DGen: https://github.com/nsarafianos/Garment3DGen — README thử Windows/CUDA11.8, phụ thuộc nvdiffrast/PyTorch3D; không suy ra hỗ trợ Mac16GB.
- VTracer: https://github.com/visioncortex/vtracer — MIT core, raster→SVG, Python/Rust/JS; chưa benchmark tại đây.
- SAM 2 demo: https://github.com/facebookresearch/sam2/blob/main/demo/README.md — MPS/CPU, Docker macOS CPU, caveat bộ nhớ MPS; đây là segmentation, không phục hồi texture.
- OpenAI: https://developers.openai.com/api/docs/guides/image-generation — giới hạn text/composition/consistency, không cam kết giữ đúng rập.
- Local read: pod-studio/openai_api.py — conservative cleanup, một input image, resize1600/pad, output1K/2K, crop/resize về canvas; không đọc file key và không gọi API.

Tìm cụm nguyên văn screenshot chưa xác định được bài gốc. Không gán danh tính hoặc khẳng định tìm thấy prompt/skill gốc. Các gợi ý flow và tiêu chí A/B trong báo cáo là đề xuất riêng, chưa có kết quả đo.
