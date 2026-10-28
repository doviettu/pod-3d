"""One bounded SAM 2 tiny image task in its own process; CPU avoids persistent GPU memory."""
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / '.vendor'))
sys.path.insert(0, str(HERE / 'repos/sam2'))
os.environ['OMP_NUM_THREADS'] = '4'

import numpy as np
from PIL import Image
import torch
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

source, target = Path(sys.argv[1]), Path(sys.argv[2])
x, y, width, height = map(int, sys.argv[3:7])
torch.set_num_threads(4)
with Image.open(source) as original:
    image = original.convert('RGB')
    assert image.width * image.height <= 50_000_000
    assert x >= 0 and y >= 0 and width > 0 and height > 0
    assert x + width <= image.width and y + height <= image.height
    # Give segmentation some context while bounding its working image to 1024 px.
    margin = max(16, min(width, height) // 8)
    box = (max(0, x - margin), max(0, y - margin), min(image.width, x + width + margin), min(image.height, y + height + margin))
    crop = image.crop(box)
    work = crop.copy()
    work.thumbnail((1024, 1024))
    sx, sy = work.width / crop.width, work.height / crop.height
    prompt = np.array([(x - box[0]) * sx, (y - box[1]) * sy,
                       (x + width - box[0]) * sx, (y + height - box[1]) * sy])
    predictor = SAM2ImagePredictor(build_sam2('configs/sam2.1/sam2.1_hiera_t.yaml',
        str(HERE / 'runtime/models/sam2.1_hiera_tiny.pt'), device='cpu', apply_postprocessing=False))
    with torch.inference_mode():
        predictor.set_image(np.asarray(work))
        masks, scores, _ = predictor.predict(box=prompt, multimask_output=True)
    mask = Image.fromarray((masks[int(np.argmax(scores))] * 255).astype('uint8')).resize(crop.size, Image.Resampling.NEAREST)
    rgba = image.crop((x, y, x + width, y + height)).convert('RGBA')
    alpha = mask.crop((x - box[0], y - box[1], x + width - box[0], y + height - box[1]))
    assert alpha.getbbox() is not None, 'No object found'
    rgba.putalpha(alpha)
    rgba.save(target, 'PNG')
