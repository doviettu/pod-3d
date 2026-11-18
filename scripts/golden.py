"""Golden test set: score the local 3D pipeline against real print files.

Each sample folder holds Mockup.jpg plus the real panels (Front.jpg, Back.jpg, ...).
Panels are compared at the pipeline's working size (before upscaling), so the score
measures unwrap + fill + clean, which is what most quality techniques change.

    .venv/bin/python scripts/golden.py samples/sweater_homealone:sweater samples/hoodie_jack:hoodie
    .venv/bin/python scripts/golden.py ... --json report.json --label baseline

Scores per panel: SSIM (structure, 1 = identical) and mean colour error ΔE (CIE76,
lower is better; under ~5 is hard to see), each split into the area seen on the mockup
and the area the pipeline had to guess. Also ΔE00 (CIEDE2000, closer to what the eye sees)
as a mean and as the 95th percentile (the worst 5% of pixels, where defects show), measured
after a small blur so a one-pixel shift of a knit stitch is not counted as a colour error.

Quality gate: compare with an earlier --json run and exit 1 when a panel got worse.

    .venv/bin/python scripts/golden.py ... --json new.json --baseline golden-baseline.json
"""
import argparse
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pod3d import clean, fill, io, pipeline, template  # noqa: E402
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


def srgb_to_lab(bgr):
    """8-bit BGR -> float CIELAB (D65), the standard formulas (no extra dependency)."""
    c = bgr[..., ::-1].astype(np.float64) / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124564, 0.2126729, 0.0193339], [0.3575761, 0.7151522, 0.1191920],
                        [0.1804375, 0.0721750, 0.9503041]])
    xyz /= [0.95047, 1.0, 1.08883]
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def ciede2000(lab1, lab2):
    """CIEDE2000 colour difference (Sharma, Wu & Dalal 2005), per pixel."""
    L1, a1, b1 = np.moveaxis(lab1, -1, 0)
    L2, a2, b2 = np.moveaxis(lab2, -1, 0)
    cbar = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(cbar ** 7 / (cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dL, dC = L2 - L1, c2p - c1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(c1p * c2p == 0, 0, dh)
    dH = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))
    Lbar, Cbar = (L1 + L2) / 2, (c1p + c2p) / 2
    hsum = h1p + h2p
    hbar = np.where(np.abs(h1p - h2p) > 180, np.where(hsum < 360, hsum + 360, hsum - 360), hsum) / 2
    hbar = np.where(c1p * c2p == 0, hsum, hbar)
    t = (1 - 0.17 * np.cos(np.radians(hbar - 30)) + 0.24 * np.cos(np.radians(2 * hbar))
         + 0.32 * np.cos(np.radians(3 * hbar + 6)) - 0.20 * np.cos(np.radians(4 * hbar - 63)))
    sl = 1 + 0.015 * (Lbar - 50) ** 2 / np.sqrt(20 + (Lbar - 50) ** 2)
    sc, sh = 1 + 0.045 * Cbar, 1 + 0.015 * Cbar * t
    rt = (-2 * np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7))
          * np.sin(np.radians(60 * np.exp(-(((hbar - 275) / 25) ** 2)))))
    return np.sqrt((dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2 + rt * (dC / sc) * (dH / sh))


def delta_e00(a_bgr, b_bgr, blur=1.0):
    """CIEDE2000 per pixel. blur: Gaussian sigma in pixels applied to both images first (a light
    S-CIELAB-style low-pass, so a one-pixel shift of a stitch is not a colour error)."""
    lab = lambda x: srgb_to_lab(cv2.GaussianBlur(x, (0, 0), blur))  # noqa: E731
    return ciede2000(lab(a_bgr), lab(b_bgr))


# A panel fails the gate when it is this much worse than the baseline.
GATE = {'ssim': -0.005, 'de00': 0.5, 'de00_p95': 1.5}


def score_panel(tpl, panel, mockup, truth, twin=None):
    work, cov = unwrap(tpl, panel, mockup)
    work = fill.fill(work, cov, log=lambda m: None, mode="tile" if panel.method in ("strip", "tube") else "bands",
                     twin=twin)
    work, _, _ = clean.clean(work)
    h, w = work.shape[:2]
    ref = cv2.resize(truth, (w, h), interpolation=cv2.INTER_AREA)
    seen = cv2.resize(cov, (w, h)) >= 0.5
    s = ssim_map(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY), cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY))
    d = delta_e(work, ref)
    d00 = delta_e00(work, ref)
    part = lambda m, x: round(float(x[m].mean()), 4) if m.any() else None  # noqa: E731
    p95 = lambda m, x: round(float(np.percentile(x[m], 95)), 2) if m.any() else None  # noqa: E731
    return {
        'seen_fraction': round(float(seen.mean()), 3),
        'ssim': round(float(s.mean()), 4), 'ssim_seen': part(seen, s), 'ssim_guessed': part(~seen, s),
        'delta_e': round(float(d.mean()), 2), 'delta_e_seen': part(seen, d), 'delta_e_guessed': part(~seen, d),
        'de00': round(float(d00.mean()), 2), 'de00_seen': part(seen, d00), 'de00_guessed': part(~seen, d00),
        'de00_p95': p95(np.ones_like(seen), d00), 'de00_p95_seen': p95(seen, d00),
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
            other = next((p for p in tpl.panels if p.name == pipeline.TWINS.get(panel.name)), None)
            twin = unwrap(tpl, other, mockup) if other is not None and tpl.map_path(other.name).exists() else None
            score, work, ref = score_panel(tpl, panel, mockup, io.read(truth_path), twin)
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
             '| Mẫu | Mảnh | Thấy | SSIM | SSIM thấy | SSIM đoán | ΔE00 | ΔE00 thấy | ΔE00 đoán | ΔE00 p95 | ΔE00 p95 thấy |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    fmt = lambda v: '–' if v is None else (f'{v:.3f}' if isinstance(v, float) and v < 1.0001 else f'{v:.1f}')  # noqa: E731
    for sample, panels in results.items():
        for name, s in panels.items():
            lines.append(f"| {sample} | {name} | {s['seen_fraction']:.0%} | {fmt(s['ssim'])} | {fmt(s['ssim_seen'])} | "
                         f"{fmt(s['ssim_guessed'])} | {fmt(s['de00'])} | {fmt(s['de00_seen'])} | "
                         f"{fmt(s['de00_guessed'])} | {fmt(s['de00_p95'])} | {fmt(s['de00_p95_seen'])} |")
    return '\n'.join(lines)


def gate(results, baseline):
    """Panels that got worse than the baseline by more than GATE allows."""
    bad = []
    for sample, panels in results.items():
        for name, s in panels.items():
            b = baseline.get(sample, {}).get(name)
            if not b:
                continue
            for key, limit in GATE.items():
                if key not in b or s.get(key) is None or b.get(key) is None:
                    continue
                diff = s[key] - b[key]
                if (limit < 0 and diff < limit) or (limit > 0 and diff > limit):
                    bad.append(f'{sample} {name}: {key} {b[key]} -> {s[key]}')
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('samples', nargs='+', help='folder:template')
    ap.add_argument('--json', help='write scores here')
    ap.add_argument('--images', help='write side-by-side result | truth images here')
    ap.add_argument('--label', default='')
    ap.add_argument('--baseline', help='earlier --json run; exit 1 if any panel got worse (quality gate)')
    ap.add_argument('--keep-background', action='store_true', help='old behaviour: backdrop counts as seen')
    a = ap.parse_args()
    if a.keep_background:
        unwrap_module.EXCLUDE_BACKGROUND = ()
    results = run(a.samples, a.images)
    print(table(results, a.label))
    if a.json:
        Path(a.json).write_text(json.dumps({'label': a.label, 'results': results}, indent=2))
    if a.baseline:
        bad = gate(results, json.loads(Path(a.baseline).read_text())['results'])
        print('\nGate: ' + ('OK' if not bad else 'TỆ HƠN mốc:\n  ' + '\n  '.join(bad)))
        sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
