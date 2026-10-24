"""Upscaling with an ESRGAN-family model through spandrel (Apple Silicon GPU when available).

Falls back to Lanczos when torch or the model file is missing, so the pipeline always runs.
"""
from pathlib import Path

import cv2
import numpy as np

MODELS = Path(__file__).resolve().parent.parent / "models"
DEFAULT_MODEL = "RealESRGAN_x4plus_anime_6B.pth"
TILE = 384
PAD = 16

_cache = {}


def _device():
    import torch
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _load(name):
    if name in _cache:
        return _cache[name]
    path = MODELS / name
    try:
        import torch  # noqa: F401
        from spandrel import ModelLoader
    except ImportError:
        return None
    if not path.exists():
        return None
    model = ModelLoader().load_from_file(str(path)).eval().to(_device())
    _cache[name] = model
    return model


def available(name=DEFAULT_MODEL):
    return _load(name) is not None


def upscale(img, factor, model_name=DEFAULT_MODEL, log=print):
    """Upscale BGR img by `factor` (any value >1). Model passes are x4, the rest is Lanczos."""
    model = _load(model_name)
    out = img
    if model is not None and factor > 1.5:
        out = _run_model(model, img)
        log(f"    model x{model.scale} -> {out.shape[1]}x{out.shape[0]}")
    else:
        if model is None:
            log("    model not found, using Lanczos")
    th, tw = round(img.shape[0] * factor), round(img.shape[1] * factor)
    interp = cv2.INTER_AREA if out.shape[0] > th else cv2.INTER_LANCZOS4
    return cv2.resize(out, (tw, th), interpolation=interp)


def _run_model(model, img):
    import torch
    dev = next(model.model.parameters()).device
    s = model.scale
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    out = np.zeros((h * s, w * s, 3), np.float32)
    with torch.inference_mode():
        for y in range(0, h, TILE):
            for x in range(0, w, TILE):
                y0, x0 = max(0, y - PAD), max(0, x - PAD)
                y1, x1 = min(h, y + TILE + PAD), min(w, x + TILE + PAD)
                t = torch.from_numpy(rgb[y0:y1, x0:x1].transpose(2, 0, 1)).unsqueeze(0).to(dev)
                r = model(t).clamp(0, 1)[0].permute(1, 2, 0).float().cpu().numpy()
                ty, tx = (y - y0) * s, (x - x0) * s
                hh, ww = min(TILE, h - y) * s, min(TILE, w - x) * s
                out[y * s:y * s + hh, x * s:x * s + ww] = r[ty:ty + hh, tx:tx + ww]
    return cv2.cvtColor((out * 255 + 0.5).astype(np.uint8), cv2.COLOR_RGB2BGR)
