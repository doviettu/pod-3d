"""Image read/write helpers that handle large files and DPI."""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def read(path) -> np.ndarray:
    """Read any image as 8-bit BGR (alpha dropped onto white)."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = np.asarray(im.convert("RGB"))
    return cv2.cvtColor(im, cv2.COLOR_RGB2BGR)


def write(path, img: np.ndarray, dpi=300, quality=95):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    if path.suffix.lower() in (".jpg", ".jpeg"):
        im.save(path, quality=quality, subsampling=0, dpi=(dpi, dpi))
    else:
        im.save(path, dpi=(dpi, dpi))
