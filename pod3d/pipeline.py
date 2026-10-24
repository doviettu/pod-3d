"""Mockup in, print-ready panels out."""
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np

from . import clean, fill, io, silhouette, upscale
from .template import Template
from .unwrap import apply_color, unwrap


def _solid_color(template, panel, mockup):
    h, w = mockup.shape[:2]
    x0, y0, x1, y1 = panel.sample
    region = mockup[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    region = apply_color(template, region)
    return np.median(region.reshape(-1, 3), 0).astype(np.uint8)


def make_panel(template, panel, mockup, log=print, use_model=True):
    out_w, out_h = panel.size
    if panel.source == "solid":
        c = _solid_color(template, panel, mockup)
        return np.full((out_h, out_w, 3), c, np.uint8), {"color": c.tolist()}
    work, cov = unwrap(template, panel, mockup)
    work = fill.fill(work, cov, log=log, mode="tile" if panel.method == "strip" else "bands")
    work, pal, frac = clean.clean(work)
    log(f"    {panel.name}: {100 * (cov >= 0.5).mean():.0f}% seen on mockup, {100 * frac:.0f}% flat colors")
    factor = out_w / work.shape[1]
    big = upscale.upscale(work, factor, log=log) if use_model else cv2.resize(
        work, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
    big = cv2.resize(big, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
    big, _ = clean.snap(big, pal, dist=clean.SNAP_DIST * 1.5)  # crisp flat areas after upscaling
    return big, {"seen": float((cov >= 0.5).mean()), "flat": float(frac)}


class WrongMockup(Exception):
    pass


def run(template: Template, mockup_path, out_dir, log=print, use_model=True, fmt="jpg", force=False):
    t0 = time.time()
    mockup = io.read(mockup_path)
    iou = silhouette.match(template, mockup)
    if iou is not None and iou < silhouette.MIN_IOU and not force:
        raise WrongMockup(
            f"Ảnh mockup không cùng dáng với template '{template.name}' (giống {100 * iou:.0f}%, cần "
            f"{100 * silhouette.MIN_IOU:.0f}%). Kết quả sẽ cắt nhầm người mẫu hoặc nền. Cần hiệu chỉnh "
            f"một template mới cho dáng này (python -m pod3d calibrate), hoặc thêm --force để vẫn chạy.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = {}
    for panel in template.panels:
        if panel.source == "copy":
            continue
        if panel.source == "unwrap" and not template.map_path(panel.name).exists():
            log(f"  {panel.name}: template has no map for this panel yet, skipped")
            continue
        log(f"  {panel.name} ...")
        img, info = make_panel(template, panel, mockup, log, use_model)
        made[panel.name] = img
        path = out_dir / f"{panel.name}.{fmt}"
        io.write(path, img, dpi=template.dpi)
    for panel in template.panels:
        if panel.source == "copy":
            img = made[panel.copy_of]
            if panel.mirror:
                img = cv2.flip(img, 1)
            io.write(out_dir / f"{panel.name}.{fmt}", img, dpi=template.dpi)
    zpath = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for p in sorted(out_dir.glob(f"*.{fmt}")):
            z.write(p, p.name)
    log(f"  done in {time.time() - t0:.0f}s -> {zpath}")
    return zpath
