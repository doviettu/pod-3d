"""Unwrap without calibration files: 8 points clicked around each panel on the mockup.

The points go clockwise from the panel's top-left corner: top-left, top middle, top-right,
right middle, bottom-right, bottom middle, bottom-left, left middle. Each side is a curve
through its 3 points and the area between the 4 curves (a Coons patch) is stretched onto the
whole panel: the side seams sit about where the photo's outline is, and filling a thin hidden
strip on a non-band design only adds stripes. Less exact than a calibrated map at folds and
edges, but works on any mockup photo.
"""
import json
from pathlib import Path

import numpy as np

from .calibrate import GRID_STEP, WORK_LONG_SIDE

ORDER = ["top-left", "top", "top-right", "right", "bottom-right", "bottom", "bottom-left", "left"]


def path_for(mockup_path):
    p = Path(mockup_path)
    return p.with_name(p.stem + ".marks.json")


def load(path):
    """{"mockup_size": [w, h], "panels": {"Front": [[x, y] * 8], ...}} in mockup pixels."""
    return json.loads(Path(path).read_text())


def _curve(a, m, b, t):
    """Quadratic through a (t=0), m (t=0.5) and b (t=1)."""
    c = 2 * m - (a + b) / 2  # Bezier control point that puts the curve through m
    t = t[..., None]
    return (1 - t) ** 2 * a + 2 * (1 - t) * t * c + t ** 2 * b


def coons(points, u, v):
    """Mockup position of panel coordinates (u, v) in 0..1 for 8 clockwise points."""
    tl, t, tr, r, br, b, bl, l = (np.asarray(p, np.float64) for p in points)
    top, bot = _curve(tl, t, tr, u), _curve(bl, b, br, u)
    left, right = _curve(tl, l, bl, v), _curve(tr, r, br, v)
    u, v = u[..., None], v[..., None]
    corners = (1 - u) * (1 - v) * tl + u * (1 - v) * tr + (1 - u) * v * bl + u * v * br
    return (1 - v) * top + v * bot + (1 - u) * left + u * right - corners


def make_map(panel, points, mockup_size):
    """The same map a calibration would store (see calibrate.calibrate_panel), from 8 points."""
    s = WORK_LONG_SIDE / max(panel.size)
    ww, wh = round(panel.size[0] * s), round(panel.size[1] * s)
    gw, gh = ww // GRID_STEP + 1, wh // GRID_STEP + 1
    U, V = np.meshgrid(np.minimum(np.arange(gw) * GRID_STEP / ww, 1), np.minimum(np.arange(gh) * GRID_STEP / wh, 1))
    cov = np.ones(U.shape, np.float32)
    grid = coons(points, U, V).astype(np.float32)
    return {"grid": grid, "cov": cov, "work_size": np.array([ww, wh]), "step": GRID_STEP,
            "mockup_size": np.array(mockup_size)}
