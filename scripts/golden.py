"""Golden test set: score the local 3D pipeline against real print files.

Each sample folder holds Mockup.jpg plus the real panels (Front.jpg, Back.jpg, ...).
Panels are compared at the pipeline's working size (before upscaling), so the score
measures unwrap + fill + clean, which is what most quality techniques change.

    .venv/bin/python scripts/golden.py samples/sweater_homealone:sweater samples/hoodie_jack:hoodie
    .venv/bin/python scripts/golden.py ... --json report.json --label baseline

Scores per panel: SSIM (structure, 1 = identical) and mean colour error ΔE (CIE76,
lower is better; under ~5 is hard to see), each split into the area seen on the mockup
and the area the pipeline had to guess.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pod3d import clean, fill, io, template  # noqa: E402
from pod3d import unwrap as unwrap_module  # noqa: E402
from pod3d.unwrap import unwrap  # noqa: E402


def ssim_map(a, b):
    a, b = a.astype(np.float64), b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    ma, mb = blur(a), blur(b)
    va, vb, cov = blur(a * a) - ma ** 2, blur(b * b) - mb ** 2, blur(a * b) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))


def delta_e(a_bgr, b_bgr):
    lab = lambda x: cv2.cvtColor(x, cv2.COLOR_BGR2LAB).astype(np.float64) * [100 / 255, 1, 1]  # noqa: E731
    return np.linalg.norm(lab(a_bgr) - lab(b_bgr), axis=-1)


def score_panel(tpl, panel, mockup, truth):
    work, cov = unwrap(tpl, panel, mockup)
    work = fill.fill(work, cov, log=lambda m: None, mode="tile" if panel.method in ("strip", "tube") else "bands")
    work, _, _ = clean.clean(work)
    h, w = work.shape[:2]
    ref = cv2.resize(truth, (w, h), interpolation=cv2.INTER_AREA)
    seen = cv2.resize(cov, (w, h)) >= 0.5
    s = ssim_map(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY), cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY))
    d = delta_e(work, ref)
    part = lambda m, x: round(float(x[m].mean()), 4) if m.any() else None  # noqa: E731
    return {
        'seen_fraction': round(float(seen.mean()), 3),
        'ssim': round(float(s.mean()), 4), 'ssim_seen': part(seen, s), 'ssim_guessed': part(~seen, s),
        'delta_e': round(float(d.mean()), 2), 'delta_e_seen': part(seen, d), 'delta_e_guessed': part(~seen, d),
    }, work, ref


def run(specs, out_dir=None):
    results = {}
    for spec in specs:
        folder, name = spec.rsplit(':', 1)
        folder = Path(folder)
        tpl = template.load(name)
        mockup = io.read(folder / 'Mockup.jpg')
        sample = {}
        for panel in tpl.panels:
            truth_path = folder / f'{panel.name}.jpg'
            if panel.source != 'unwrap' or not truth_path.exists() or not tpl.map_path(panel.name).exists():
                continue
            t0 = time.time()
            score, work, ref = score_panel(tpl, panel, mockup, io.read(truth_path))
            score['seconds'] = round(time.time() - t0, 1)
            sample[panel.name] = score
            if out_dir:
                target = Path(out_dir) / folder.name
                target.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(target / f'{panel.name}.jpg'), np.concatenate([work, ref], 1))
        results[folder.name] = sample
    return results


def table(results, label=''):
    lines = [f'### {label}' if label else '', '',
             '| Mẫu | Mảnh | Thấy | SSIM | SSIM thấy | SSIM đoán | ΔE | ΔE thấy | ΔE đoán |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    fmt = lambda v: '–' if v is None else f'{v}'  # noqa: E731
    for sample, panels in results.items():
        for name, s in panels.items():
            lines.append(f"| {sample} | {name} | {s['seen_fraction']:.0%} | {s['ssim']} | {fmt(s['ssim_seen'])} | "
                         f"{fmt(s['ssim_guessed'])} | {s['delta_e']} | {fmt(s['delta_e_seen'])} | {fmt(s['delta_e_guessed'])} |")
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('samples', nargs='+', help='folder:template')
    ap.add_argument('--json', help='write scores here')
    ap.add_argument('--images', help='write side-by-side result | truth images here')
    ap.add_argument('--label', default='')
    ap.add_argument('--keep-background', action='store_true', help='old behaviour: backdrop counts as seen')
    a = ap.parse_args()
    if a.keep_background:
        unwrap_module.EXCLUDE_BACKGROUND = ()
    results = run(a.samples, a.images)
    print(table(results, a.label))
    if a.json:
        Path(a.json).write_text(json.dumps({'label': a.label, 'results': results}, indent=2))


if __name__ == '__main__':
    main()
