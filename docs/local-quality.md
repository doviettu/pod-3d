# Tăng chất lượng chế độ Local

Ngày: 28/10/2026. Mọi kỹ thuật dưới đây đều chạy trên Mac mini 16 GB, miễn phí, và dùng license cho phép bán áo. Đây là kết quả tìm hiểu, chưa đo trên mẫu của mình; phải đo bằng bộ test vàng (mục G) trước khi bật mặc định.

## Ưu tiên làm trước

| # | Kỹ thuật | Vì sao đáng làm | Công sức |
| --- | --- | --- | --- |
| 1 | Tìm bản ảnh gốc lớn hơn (A1) | Ảnh 450 px là giới hạn lớn nhất hiện nay; có ảnh 2000 px thì mọi bước sau đều tốt hơn | Nhỏ |
| 2 | Dựng lại lưới mũi len / pixel (D1) | Áo ugly sweater và pixel art vốn là lưới ô vuông: dựng lại lưới thì nét tuyệt đối ở mọi kích thước | Vừa |
| 3 | Khử vân len bằng FFT + tách bóng (C1, C2) | Bỏ được vân vải, nếp gấp in vào artwork, là lỗi dễ thấy nhất khi in | Nhỏ |
| 4 | Vector hoá phần màu phẳng (D3) | Chữ, logo, mảng màu ra SVG thì phóng bao nhiêu cũng nét | Vừa |
| 5 | Bộ test vàng (G) | Không đo thì không biết kỹ thuật nào thật sự giúp | Nhỏ |

## A. Ảnh đầu vào

1. **Tìm bản lớn nhất của cùng ảnh**
   - Tự đổi link ảnh shop về bản gốc: Shopify bỏ đuôi `_800x`, Etsy `il_794xN` → `il_fullxfull`, Amazon bỏ đoạn `._AC_SX679_`. Thêm luật mới vào một file cấu hình.
   - Nút tra ngược (Google Lens, TinEye, Yandex) để tìm bản to hơn. Tool tự chọn ảnh có cạnh dài nhất.
2. **Gộp nhiều ảnh của cùng thiết kế** (nhiều góc, nhiều màu áo, ảnh trước + ảnh cận): căn khớp bằng SIFT/RoMa (đã có trong engine) rồi lấy trung vị. Cách này giảm nhiễu và nhiễu nén, lấy lại được chi tiết mà một ảnh đơn bị mất.
3. **Khử nhiễu nén JPEG trước mọi bước**: FBCNN hoặc SCUNet (cả hai đều Apache-2.0). Ảnh web thường nén mạnh; khử trước thì bước tách nền và phóng to không phóng đại các khối vuông nén.

## B. Hình học

1. **Nắn phẳng mịn hơn**: ngoài 4 góc, dùng các đường thẳng có trong thiết kế (viền, sọc, chữ) làm ràng buộc, rồi nắn bằng lưới TPS để chúng thẳng lại.
2. **Đối xứng**: nhiều thiết kế đối xứng trái phải. Tìm trục, so hai nửa; nửa nào rõ hơn thì dùng để sửa nửa bị nếp hoặc bị che, có kiểm tra độ khớp trước khi thay.

## C. Bóng và vân vải

1. **Tách bóng (kiểu Retinex)**: ước lượng ánh sáng tần số thấp trong vùng áo bằng guided filter cỡ lớn, rồi chia ra. Kèm cân bằng màu theo màu vải nền. Không cần model.
2. **Khử vân len / vân vải bằng FFT**: vân dệt lặp đều nên hiện thành các đỉnh trên phổ tần số. Lọc chặn (notch) các đỉnh đó, chỉ trong vùng áo.
3. **Model học sâu** (thử sau): có các nghiên cứu tách màu gốc khỏi bóng cho ảnh quần áo ([Learning Intrinsic Images for Clothing](https://arxiv.org/abs/2111.08521)). Phải kiểm tra license từng model trước khi dùng.

## D. Màu và đồ hoạ

1. **Dựng lại lưới mũi len / pixel**
   - Đo kích thước ô bằng tự tương quan, hoặc dùng `unfake.py`.
   - Mỗi ô lấy màu trung vị rồi khớp về bảng màu. Kết quả là ảnh lưới sạch, phóng bằng nearest-neighbor tới 4500 × 5400 mà không vỡ.
   - Rất hợp với bộ Home Alone và Street Fighter.
2. **Bảng màu chuẩn tốt hơn**: k-means trong không gian Lab, chỉ lấy ở vùng tin cậy. Sau đó gán màu theo từng siêu điểm ảnh (superpixel) thay vì từng pixel, để không có lấm tấm.
3. **Vector hoá lai**
   - Lớp màu phẳng (chữ, logo, mảng) qua VTracer (MIT, đã cài) hoặc potrace, rồi vẽ lại ở kích thước in.
   - Lớp ảnh chụp hoặc chuyển màu giữ dạng raster.
   - Ghép hai lớp ở bước cuối.
4. **Chữ**: đọc chữ bằng OCR, so với font trong thư viện (Codex đã có trang so font), gõ lại thành lớp chữ thật.

## E. Phóng to

1. **Chọn model theo loại ảnh** (đều cho phép bán):

| Loại ảnh | Model | License |
| --- | --- | --- |
| Tranh vẽ, màu phẳng | RealESRGAN x4plus anime 6B (đang dùng) | BSD-3 |
| Tranh vẽ, ảnh nén nhiều | [4x-HFA2k](https://openmodeldb.info/models/4x-HFA2k) | CC-BY-4.0 (ghi công) |
| Ảnh chụp | RealESRGAN x4plus | BSD-3 |

   **Không dùng** 4x-UltraSharp hay [4x-AnimeSharp](https://openmodeldb.info/models/4x-AnimeSharp), vì license không cho dùng thương mại (CC-BY-NC-SA). Tra license từng model trên [OpenModelDB](https://openmodeldb.info) trước khi thêm.
2. **Chạy theo ô có chồng mép** để vừa RAM, và chỉ phóng **một lần ở cuối**: phóng nhiều lần làm lỗi to dần.
3. **Mép trong suốt (2D)**: tách alpha bằng BiRefNet bản HR/matting ([arXiv](https://arxiv.org/abs/2401.03407), MIT). Sau đó khử viền màu vải còn dính ở mép (color decontamination), để in trên áo khác màu không bị viền.

## F. Phần bị che (3D)

1. **Đo chu kỳ lặp 2 chiều** bằng FFT/tự tương quan, rồi lát đúng chu kỳ (đang có cho tay áo, mở rộng cho thân).
2. **Tổng hợp texture kiểu PatchMatch** cho nền hoạ tiết không lặp đều.
3. **Vẽ bù bằng SDXL inpainting chạy local** (tùy chọn). License OpenRAIL++ cho phép dùng thương mại. Trên Mac 16 GB chạy được nhưng chậm, khoảng 1–2 phút cho mỗi ô 1024 px. Chỉ dùng cho vùng nhỏ, và luôn tô màu đánh dấu "AI vẽ bù".

## G. Đo chất lượng (bộ test vàng)

- Dùng 3 bộ mẫu đã có file in thật (Home Alone, Street Fighter, Jack hoodie), cộng vài ảnh 2D có file gốc.
- Mỗi lần đổi thuật toán, tự chạy lại và chấm điểm:
  - SSIM so với file thật
  - ΔE màu trung bình
  - Độ khớp đường biên
  - Tỷ lệ vùng phải đoán
- Xuất bảng so sánh trước/sau. Kỹ thuật nào không tăng điểm thì không bật mặc định.

## Nguồn

- [BiRefNet (arXiv 2401.03407)](https://arxiv.org/abs/2401.03407) · [BiRefNet HR matting ONNX](https://huggingface.co/PinkPixel/birefnet-hr-matting-onnx)
- [FBCNN (arXiv 2109.14573)](https://arxiv.org/abs/2109.14573v1)
- [OpenModelDB: 4x-HFA2k](https://openmodeldb.info/models/4x-HFA2k), [4x-AnimeSharp](https://openmodeldb.info/models/4x-AnimeSharp)
- [Learning Intrinsic Images for Clothing](https://arxiv.org/abs/2111.08521)
- [Pattern Guided UV Recovery for Video Garment Texturing](https://arxiv.org/abs/2407.10137): dùng hoạ tiết vải để tìm bản đồ UV, ý tưởng gần với bước hiệu chỉnh của mình
