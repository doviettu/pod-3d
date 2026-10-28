# Luồng tách art 2D

Ngày 28/10/2026. Mục tiêu: từ ảnh áo trên mạng, lấy lại **chính art** (không phải khoanh rồi cắt) thành PNG trong suốt 4500 × 5400, 300 DPI, in được lên áo màu bất kỳ.

## Vấn đề của cách cũ

- Cách cũ: điểm ảnh nào khác màu vải thì giữ, giống màu vải thì bỏ. Phần art trùng màu áo (mèo đen trên áo đen) bị xoá mất.
- "Giữ nguyên khối" thì ngược lại: giữ cả phần vải đen quanh art, cạnh cắt cứng.
- Viền tranh vốn mờ dần vào màu áo bị cắt thành viền răng cưa, lấm tấm.

## Luồng mới (`studio/art2d.py`)

1. **Tìm áo và khung art**: nền ảnh là vùng trống chạm mép ảnh. Áo là phần còn lại. Khung art là vùng khác màu vải nhiều nhất. Người dùng chỉnh được khung, và khung chỉ cần bao quanh art.
2. **Màu vải tại chỗ**: lấy trung vị các điểm vải quanh art, rồi khử bóng sáng tối bằng phép chia kiểu Retinex.
3. **Tách mực khỏi vải (unmixing)**: mỗi điểm ảnh được coi là `I = a·F + (1 − a)·B`, với B là màu vải và F là màu mực đặc gần nhất. Độ phủ `a` là hình chiếu của I − B lên F − B. Nhờ vậy phần art mờ dần (vignette, khói, viền sáng) giữ được độ chuyển, không bị cắt cứng. Đây là dạng đơn giản của bài toán matting khi đã biết màu nền ([Levin và cộng sự, closed-form matting](https://people.csail.mit.edu/alevin/papers/Matting-Levin-Lischinski-Weiss-CVPR06.pdf)).
4. **Xác định vùng in (print area)**:
   - *Tranh có khung* (art phủ kín một hình chữ nhật, mực chạy dọc các cạnh): cả hình chữ nhật là vùng in. Phần trùng màu áo bên trong (mèo đen) được **giữ lại** với màu thật.
   - *Art tự do* (nhân vật, chữ): chỉ lấy phần mực. Lòng chữ O, khe giữa các nét để trong suốt. Trên áo cùng màu thì nhìn y hệt, còn trên áo màu khác thì vẫn đúng.
   - Người dùng ép được: "Giữ phần trùng màu áo" (lấp mọi vùng bị art bao kín) hoặc "Chỉ lấy phần mực".
5. **Viền hoà trộn**: trong vùng in, độ phủ tăng dần từ mép vào (smoothstep, rộng 5% với tranh có khung, 2% với art tự do). Mực mờ ngay ngoài vùng in vẫn giữ độ mờ vốn có. Dải viền được làm mượt để nhiễu JPEG không làm viền rách.
6. **Khử màu vải ở mép (decontamination)**: điểm bán trong suốt lấy màu mực thật `F = B + (I − B)/a`. Dưới lớp gần trong suốt thì lấy màu của mực đặc gần nhất, để khi phóng to hay in lên áo trắng không bị quầng đen.
7. **Cắt sát, phóng to, đặt lên khuôn**: cắt theo phần có art, phóng một lần bằng Real-ESRGAN (alpha phóng bicubic), căn giữa và căn trên khuôn 4500 × 5400.

## Lưu ý khi in

- Máy in DTG/DTF xử lý vùng bán trong suốt không đều trên áo tối, vì lớp lót trắng in theo alpha ([AM Custom Clothing: Transparency in DTG files](https://support.amcustomclothing.co.uk/index.php/knowledge-base/transparency-in-dtg-files/)). Tool cảnh báo khi art có nhiều vùng mờ. Có thể làm thêm: đổi viền mờ thành chấm halftone.
- Ảnh nguồn nhỏ (dưới ~1000 px) thì phóng 20 lần trở lên vẫn mờ. Cách tốt nhất vẫn là tìm ảnh lớn hơn (xem `docs/local-quality.md`, mục A).

## Việc có thể làm tiếp

- Matting đầy đủ (closed-form hoặc [PyMatting](https://pypi.org/project/pymatting), MIT) cho dải viền, khi ảnh nguồn đủ lớn.
- Tuỳ chọn chuyển viền mờ thành halftone cho DTG.
- Vùng in hình tròn/elip (huy hiệu) như hình chữ nhật.
- Thêm ảnh 2D có file gốc vào bộ test vàng để chấm điểm.
