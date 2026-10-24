"""LaMa inpainting (Apache-2.0) through spandrel, for the parts of a panel the mockup hides."""
import cv2
import numpy as np

from .upscale import MODELS, _device

MODEL = "big-lama.pt"
_model = None


def _load():
    global _model
    if _model is not None:
        return _model
    try:
        import spandrel_extra_arches
        from spandrel import ModelLoader
        spandrel_extra_arches.install()
    except ImportError:
        return None
    path = MODELS / MODEL
    if not path.exists():
        return None
    _model = ModelLoader().load_from_file(str(path)).eval().to(_device())
    return _model


def available():
    return _load() is not None


WORK_SIDE = 768  # LaMa keeps structure best near its training size; big holes smear at full size


def inpaint(img, mask):
    """img: BGR uint8, mask: bool (True = fill). Fills at reduced size, pastes back upscaled."""
    h, w = img.shape[:2]
    s = min(1.0, WORK_SIDE / max(h, w))
    if s < 1.0:
        small = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        sm = cv2.resize(mask.astype(np.uint8), small.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0
        filled = cv2.resize(_inpaint(small, sm), (w, h), interpolation=cv2.INTER_CUBIC)
        out = img.copy()
        out[mask] = filled[mask]
        return out
    return _inpaint(img, mask)


def _inpaint(img, mask):
    import torch
    model = _load()
    dev = next(model.model.parameters()).device
    h, w = img.shape[:2]
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    rgb = cv2.copyMakeBorder(rgb, 0, ph, 0, pw, cv2.BORDER_REFLECT)
    m = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8))
    m = cv2.copyMakeBorder(m, 0, ph, 0, pw, cv2.BORDER_REFLECT)
    t = torch.from_numpy(rgb.astype(np.float32) / 255).permute(2, 0, 1)[None].to(dev)
    mt = torch.from_numpy(m.astype(np.float32))[None, None].to(dev)
    with torch.inference_mode():
        r = model(t, mt)[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
    r = (r[:h, :w] * 255 + 0.5).astype(np.uint8)
    out = img.copy()
    out[mask] = cv2.cvtColor(r, cv2.COLOR_RGB2BGR)[mask]
    return out
