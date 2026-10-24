"""Apply a calibrated map: mockup -> flat panel at work size, plus a coverage mask."""
import cv2
import numpy as np

def load_map(template, panel):
    d = np.load(template.map_path(panel.name))
    return {k: d[k] for k in d.files}


def remap_grid(grid, mockup, work_size, step=None, mockup_size=None, interp=cv2.INTER_CUBIC):
    """Sample the mockup through a coarse grid map at work size."""
    from .calibrate import GRID_STEP
    step = step or GRID_STEP
    ww, wh = work_size
    gh, gw = grid.shape[:2]
    sx = sy = 1.0
    if mockup_size is not None:
        sx = mockup.shape[1] / mockup_size[0]
        sy = mockup.shape[0] / mockup_size[1]
    xs = np.arange(ww) / step
    ys = np.arange(wh) / step
    mx = (_interp2(grid[..., 0], xs, ys, gw, gh) * sx).astype(np.float32)
    my = (_interp2(grid[..., 1], xs, ys, gw, gh) * sy).astype(np.float32)
    return cv2.remap(mockup, mx, my, interp, borderMode=cv2.BORDER_REPLICATE)


def unwrap(template, panel, mockup, color=True, m=None):
    """m: a map made elsewhere (marks.make_map); default is the template's calibrated map."""
    m = m if m is not None else load_map(template, panel)
    ww, wh = (int(v) for v in m["work_size"])
    out = remap_grid(m["grid"], mockup, (ww, wh), int(m["step"]), m["mockup_size"])
    gh, gw = m["cov"].shape
    cov = _interp2(m["cov"], np.arange(ww) / int(m["step"]), np.arange(wh) / int(m["step"]), gw, gh)
    return (apply_color(template, out) if color else out), cov.astype(np.float32)


def apply_color(template, img):
    if not template.color_path.exists():
        return img
    M = np.load(template.color_path)["M"]
    flat = img.reshape(-1, 3).astype(np.float32)
    flat = np.hstack([flat, np.ones((len(flat), 1), np.float32)]) @ M.astype(np.float32)
    return np.clip(flat, 0, 255).astype(np.uint8).reshape(img.shape)


def _interp2(a, xs, ys, gw, gh):
    """Bilinear sample of a coarse grid at fractional grid coordinates."""
    xs = np.clip(xs, 0, gw - 1)
    ys = np.clip(ys, 0, gh - 1)
    x0 = np.floor(xs).astype(int); x1 = np.minimum(x0 + 1, gw - 1); fx = xs - x0
    y0 = np.floor(ys).astype(int); y1 = np.minimum(y0 + 1, gh - 1); fy = ys - y0
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return top * (1 - fy)[:, None] + bot * fy[:, None]
