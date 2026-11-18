"""Đo tốc độ các bước AI trên máy này (chạy trong container: docker exec pod-studio python deploy/oracle/bench.py).

In ra số giây cho: Real-ESRGAN x4 trên ô 512x512, LaMa 512x512, SAM 3 trên 1 mockup (nếu đã có quyền HF).
Không gọi API trả phí, không ghi gì ngoài thư mục tạm.
"""
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'studio'))


def timed(label, fn, repeat=1):
    try:
        fn()  # lần đầu gồm nạp model
        start = time.perf_counter()
        for _ in range(repeat):
            fn()
        print(f'{label}: {(time.perf_counter() - start) / repeat:.1f} s', flush=True)
    except Exception as exc:  # noqa: BLE001 - chỉ là đo
        print(f'{label}: lỗi {type(exc).__name__}: {exc}'[:300], flush=True)


def main():
    import torch
    print('torch', torch.__version__, '| luồng CPU', torch.get_num_threads(), '| nhân', os.cpu_count())
    rng = np.random.default_rng(0)
    tile = (rng.random((512, 512, 3)) * 255).astype(np.uint8)

    from pod3d import upscale
    model = upscale._load(upscale.DEFAULT_MODEL)
    if model is None:
        print('Real-ESRGAN: chưa có models/' + upscale.DEFAULT_MODEL)
    else:
        timed('Real-ESRGAN x4, ô 512x512 -> 2048x2048', lambda: upscale._run_model(model, tile))
        print('  (1 mảnh in 4500x5400 cần khoảng 6 ô như vậy)')

    from pod3d import inpaint
    if inpaint.available():
        mask = np.zeros((512, 512), np.uint8)
        mask[200:320, 200:320] = 255
        timed('LaMa 512x512', lambda: inpaint.inpaint(tile, mask))
    else:
        print('LaMa: chưa có models/big-lama.pt')

    mockups = sorted((ROOT / 'studio' / 'templates').glob('*/mockup.*')) + sorted((ROOT / 'templates').glob('*/silhouette.png'))
    if mockups:
        try:
            import panel_detect
            seg = panel_detect.sam3_segmenter(device='cpu')
            from PIL import Image
            img = Image.open(mockups[0]).convert('RGB')
            timed(f'SAM 3 trên {mockups[0].name} {img.size[0]}x{img.size[1]}', lambda: seg(np.array(img), ['hoodie', 'sleeve']))
        except Exception as exc:  # noqa: BLE001
            print(f'SAM 3: chưa chạy được ({type(exc).__name__}: {str(exc)[:200]})')


if __name__ == '__main__':
    main()
