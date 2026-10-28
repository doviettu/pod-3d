"""Conservative offline drafts with explicit coverage provenance, using existing calibrated maps."""
import json
import time
import zipfile
from pathlib import Path
import cv2
import numpy as np
from artwork_quality import exterior_background, locked_composite, stable_sleeve, repeat_bands


def run(backend, template, mockup_path, out_dir, log=print, use_model=True, fmt='png', force=False, design_spec=None):
    from pod3d.unwrap import load_map, remap_grid, unwrap
    started = time.monotonic()
    mockup = backend.io.read(mockup_path)
    iou = backend.silhouette.match(template, mockup)
    if iou is not None and iou < backend.silhouette.MIN_IOU and not force:
        raise backend.WrongMockup('Mockup không cùng dáng khuôn; cần hiệu chuẩn trước khi trải hình.')
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=False)
    exterior = exterior_background(mockup).astype(np.uint8) * 255
    reports, made = {}, {}
    for panel in template.panels:
        if panel.source == 'copy':
            continue
        if panel.source != 'unwrap':
            big, info = backend.make_panel(template, panel, mockup, log=log, use_model=False)
            reports[panel.name] = {'mode': 'sampled-solid', 'manual_review_required': True, **info}
        else:
            log('  ' + panel.name + ' · kiểm tra vùng nguồn và giữ nét đã nhìn thấy')
            raw, cov = unwrap(template, panel, mockup, color=False)
            mapping = load_map(template, panel)
            outside = remap_grid(mapping['grid'], exterior, tuple(int(v) for v in mapping['work_size']),
                                 int(mapping['step']), mapping['mockup_size'], interp=cv2.INTER_NEAREST) > 127
            known = backend.fill.solid_mask(cov) & ~outside
            if known.mean() < .01:
                raise ValueError(panel.name + ': không đủ dữ liệu nhìn thấy để trải an toàn.')
            bands, prescribed = [], {}
            for band in (design_spec or {}).get('bands', []):
                if band['panel'] == panel.name and band.get('approved') and band.get('axis', 'x') == 'x':
                    y0 = max(0, round(band['bounds'][1] * raw.shape[0]))
                    y1 = min(raw.shape[0], round(band['bounds'][3] * raw.shape[0]))
                    if y1 > y0:
                        bands.append((y0, y1))
                        prescribed[(y0, y1)] = max(1, round(band['period_px'] * raw.shape[1] / panel.size[0]))
            periods = []
            if panel.method in ('strip', 'tube'):
                work, left = stable_sleeve(raw, known)
                if bands:
                    repeated, repeat_missing, periods = repeat_bands(raw, known, bands=bands, periods=prescribed)
                    use = ~known & ~repeat_missing
                    work = locked_composite(work, repeated, use)
                    left[use] = False
                method = 'fixed-phase-mirror-draft'
            else:
                work, left, periods = repeat_bands(raw, known, bands=bands or None, periods=prescribed)
                method = 'observed-pixels-and-background-draft'
            # Background continuation is unverified; it must never overwrite the trusted drawing.
            work, left = backend.fill.extend(work, ~left)
            vertical, remaining = backend.fill.extend(work.transpose(1, 0, 2).copy(), ~left.T)
            work, left = vertical.transpose(1, 0, 2).copy(), remaining.T
            unresolved = float(left.mean())
            if left.any():
                # shortcut: show a neutral marker for unsupported holes; repair/approve explicitly before print.
                work[left] = (180, 180, 180)
            work = locked_composite(raw, work, ~known)
            width, height = panel.size
            big = backend.upscale.upscale(work, width / work.shape[1], log=log) if use_model else work
            big = cv2.resize(big, (width, height), interpolation=cv2.INTER_LANCZOS4)
            observed = cv2.resize(raw, (width, height), interpolation=cv2.INTER_LANCZOS4)
            trusted = cv2.resize(known.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
            big = locked_composite(observed, big, ~trusted)
            if not np.array_equal(big[trusted], observed[trusted]):
                raise ValueError('Vùng ảnh đã khóa bị thay đổi; chưa xuất panel.')
            reports[panel.name] = {'mode': method, 'observed_fraction': round(float(known.mean()), 6),
                'exterior_rejected_fraction': round(float((outside & (cov >= .5)).mean()), 6),
                'inferred_fraction': round(float((~known).mean()), 6), 'unresolved_fraction': round(unresolved, 6),
                'confirmed_repeats': periods, 'protected_pixels_changed': 0, 'manual_review_required': True,
                'text_font_verified': False}
        made[panel.name] = folder / (panel.name + '.' + fmt)
        backend.io.write(made[panel.name], big, dpi=template.dpi)
        del big
    for panel in template.panels:
        if panel.source == 'copy':
            image = backend.io.read(made[panel.copy_of])
            if panel.mirror:
                image = cv2.flip(image, 1)
            backend.io.write(folder / (panel.name + '.' + fmt), image, dpi=template.dpi)
            reports[panel.name] = {'mode': 'template-copy', 'copy_of': panel.copy_of, 'manual_review_required': True}
    report = {'version': '1', 'mode': 'offline-coverage-v1', 'panels': reports,
              'warning': 'Vùng suy luận, chữ/logo và màu cần duyệt; vùng xám là dữ liệu chưa phục hồi.',
              'seconds': round(time.monotonic() - started, 3), 'paid_calls': 0}
    (folder.parent / 'local-quality.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    target = folder.with_suffix('.zip')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as archive:
        for panel in template.panels:
            archive.write(folder / (panel.name + '.' + fmt), panel.name + '.' + fmt)
    return target
