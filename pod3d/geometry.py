"""Templates without sample print files: the map is drawn from the mockup's geometry.

Each panel is marked once on a reference mockup: a "quad" (4 corners, top-left, top-right,
bottom-right, bottom-left, of where the panel shows) or a "tube" (a curve down the middle of a
sleeve). The cloth is modeled as a cylinder, so the panel's sides squeeze toward the outline
and are not trusted there; what is hidden is filled like any other template.
Less exact than `calibrate` (no real print file to check against): center areas are close,
sides are a guess.
"""
import cv2
import numpy as np

from . import io, silhouette
from .calibrate import GRID_STEP, WORK_LONG_SIDE

VISIBLE_MIN = 0.3  # cos of the cloth angle below which it turns too far away to trust


def _across(u, wrap, turn):
    """Panel column (0..1) -> position across the visible outline (0..1), and how squarely
    the cloth there faces the camera (cos of its angle)."""
    phi = (u - 0.5) * np.pi + turn
    lin = np.clip(u + np.sin(turn) / 2, 0, 1)
    s = (1 - wrap) * lin + wrap * (np.sin(np.clip(phi, -np.pi / 2, np.pi / 2)) + 1) / 2
    face = np.where(np.abs(phi) < np.pi / 2, np.cos(phi), 0.0)
    return s, face


def _turn(center, x_left, x_right):
    """Angle that puts the panel's middle on the given mockup x (a 3/4 view)."""
    if center is None:
        return 0.0
    f = (center - x_left) / max(x_right - x_left, 1e-6)
    return float(np.arcsin(np.clip(2 * f - 1, -0.95, 0.95)))


def _quad_grid(panel, mw, mh, us, vs):
    q = np.array(panel.quad, np.float64) * [mw, mh]
    tl, tr, br, bl = q
    mid_top, mid_bot = (tl[0] + bl[0]) / 2, (tr[0] + br[0]) / 2
    turn = _turn(None if panel.center is None else panel.center * mw, mid_top, mid_bot)
    s, face = _across(us, panel.wrap, turn)
    s, v = s[None, :, None], vs[:, None, None]
    top = tl + (tr - tl) * s
    bot = bl + (br - bl) * s
    return top + (bot - top) * v, np.broadcast_to(face[None, :], (len(vs), len(us)))


def _tube_grid(panel, mw, mh, us, vs):
    c = np.array(panel.curve, np.float64) * [mw, mh]
    seg = np.linalg.norm(np.diff(c, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    t = vs * cum[-1]
    cx, cy = np.interp(t, cum, c[:, 0]), np.interp(t, cum, c[:, 1])
    tx, ty = np.gradient(cx), np.gradient(cy)
    n = np.hypot(tx, ty) + 1e-9
    nx, ny = ty / n, -tx / n  # normal across the sleeve
    s, face = _across(us, panel.wrap, 0.0)
    off = (2 * s - 1) * panel.halfwidth * mw
    gx = cx[:, None] - nx[:, None] * off[None, :]
    gy = cy[:, None] - ny[:, None] * off[None, :]
    return np.stack([gx, gy], -1), np.broadcast_to(face[None, :], (len(vs), len(us)))


def _hidden(panel, grid, mockup):
    """Grid points on the white background or inside a marked occluder (another view in front)."""
    mh, mw = mockup.shape[:2]
    fg = (mockup.min(2) < 235).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    x = np.clip(grid[..., 0], 0, mw - 1).astype(int)
    y = np.clip(grid[..., 1], 0, mh - 1).astype(int)
    out = (fg[y, x] == 0) | (grid[..., 0] < 0) | (grid[..., 1] < 0) | (grid[..., 0] >= mw) | (grid[..., 1] >= mh)
    for poly in panel.hide or []:
        m = np.zeros((mh, mw), np.uint8)
        cv2.fillPoly(m, [np.int32(np.array(poly) * [mw, mh])], 1)
        out |= m[y, x] > 0
    return out


def build_panel(template, panel, mockup, log=print):
    mh, mw = mockup.shape[:2]
    s = WORK_LONG_SIDE / max(panel.size)
    ww, wh = round(panel.size[0] * s), round(panel.size[1] * s)
    us = np.arange(0, ww, GRID_STEP) / (ww - 1)
    vs = np.arange(0, wh, GRID_STEP) / (wh - 1)
    make = _quad_grid if panel.method == "quad" else _tube_grid
    grid, face = make(panel, mw, mh, us, vs)
    cov = np.clip((face - VISIBLE_MIN) / 0.15, 0, 1)
    cov[_hidden(panel, grid, mockup)] = 0
    cov = cv2.GaussianBlur(cov.astype(np.float32), (0, 0), 1.5)
    ok = cov > 0.5
    jac = float(np.median(np.linalg.norm(np.diff(grid, axis=1), axis=-1)[ok[:, 1:]]) / GRID_STEP) if ok.any() else 0.0
    np.savez_compressed(template.map_path(panel.name), grid=grid.astype(np.float32), cov=cov,
                        work_size=np.array([ww, wh]), step=GRID_STEP, density=jac,
                        mockup_size=np.array([mw, mh]))
    log(f"  {panel.name}: {100 * ok.mean():.0f}% of the panel seen, {jac:.2f} mockup px per work px")
    return grid, cov


def run(template, mockup_path, log=print):
    """Build every quad/tube map from a reference mockup; writes a preview of the marked areas."""
    mockup = io.read(mockup_path)
    silhouette.save(template, mockup)
    template.color_path.unlink(missing_ok=True)  # no print file to learn the shading from
    preview = mockup.copy()
    colors = [(0, 0, 255), (0, 200, 0), (255, 0, 0), (0, 200, 200), (200, 0, 200)]
    for i, panel in enumerate(p for p in template.panels if p.method in ("quad", "tube")):
        grid, cov = build_panel(template, panel, mockup, log)
        pts = grid[cov > 0.5].astype(np.int32)
        overlay = preview.copy()
        overlay[np.clip(pts[:, 1], 0, mockup.shape[0] - 1), np.clip(pts[:, 0], 0, mockup.shape[1] - 1)] = colors[i % 5]
        preview = cv2.addWeighted(preview, 0.5, overlay, 0.5, 0)
        y, x = np.median(pts, 0).astype(int)[::-1]
        cv2.putText(preview, panel.name, (int(x) - 15, int(y)), 0, max(0.35, mockup.shape[1] / 1500), (255, 255, 255), 2)
        cv2.putText(preview, panel.name, (int(x) - 15, int(y)), 0, max(0.35, mockup.shape[1] / 1500), colors[i % 5], 1)
    out = template.dir / "layout_preview.jpg"
    cv2.imwrite(str(out), preview)
    log(f"  preview: {out}")
