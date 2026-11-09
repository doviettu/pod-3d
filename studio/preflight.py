"""Print-file preflight: check finished files against a print supplier's profile before sending.

Profiles live in suppliers.json (edit numbers to match the supplier's real guide). Checks:
format, file size, pixel size, DPI, transparency (none allowed for AOP), semi-transparent edge
pixels (print as a faint halo on DTG/DTF), lines thinner than the minimum, tiny specks, and
optionally colors that fall outside CMYK when ~/.config/pod-studio/cmyk.icc exists.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms

HERE = Path(__file__).resolve().parent
PROFILES = HERE / 'suppliers.json'
CMYK_ICC = Path.home() / '.config/pod-studio/cmyk.icc'
LEVELS = ('ok', 'warn', 'error')
Image.MAX_IMAGE_PIXELS = 120_000_000   # AOP hems are ~13k px wide


def profiles(path=PROFILES):
    data = json.loads(Path(path).read_text())['profiles']
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def catalog(path=PROFILES):
    return [{'id': k, 'label': v['label'], 'kind': v.get('kind'), 'verified': v.get('verified')}
            for k, v in profiles(path).items()]


def _px(mm, dpi):
    return max(0, int(round(mm / 25.4 * dpi)))


def _gamut(rgb, icc=CMYK_ICC):
    """Share of pixels whose sRGB -> CMYK -> sRGB round trip moves by more than ΔE 10."""
    if not icc.is_file():
        return None
    small = Image.fromarray(rgb)
    small.thumbnail((512, 512))
    srgb = ImageCms.createProfile('sRGB')
    cmyk = ImageCms.getOpenProfile(str(icc))
    to_cmyk = ImageCms.buildTransform(srgb, cmyk, 'RGB', 'CMYK')
    back = ImageCms.buildTransform(cmyk, srgb, 'CMYK', 'RGB')
    round_trip = ImageCms.applyTransform(ImageCms.applyTransform(small, to_cmyk), back)
    a = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2LAB).astype(np.float32)
    b = cv2.cvtColor(np.asarray(round_trip), cv2.COLOR_RGB2LAB).astype(np.float32)
    a[..., 0] *= 100 / 255   # OpenCV 8-bit Lab stores L as 0..255
    b[..., 0] *= 100 / 255
    return float((np.linalg.norm(a - b, axis=2) > 10).mean() * 100)


def check_file(path, profile, expected_size=None):
    """Returns {'file', 'status', 'items': [{'level', 'text'}], 'stats'} for one print file."""
    path = Path(path)
    items, stats = [], {}

    def add(level, text):
        items.append({'level': level, 'text': text})

    mb = path.stat().st_size / 1024 / 1024
    stats['mb'] = round(mb, 1)
    with Image.open(path) as image:
        fmt, size = image.format, image.size
        dpi = image.info.get('dpi')
        image.load()
        rgba = np.asarray(image.convert('RGBA'))
    stats.update(format=fmt, size=list(size))
    if fmt not in profile.get('formats', ['PNG']):
        add('error', f'Định dạng {fmt}; nhà in nhận {", ".join(profile["formats"])}.')
    if profile.get('max_mb') and mb > profile['max_mb']:
        add('error', f'File {mb:.0f} MB, vượt giới hạn {profile["max_mb"]} MB.')
    want = expected_size or profile.get('size')
    if want and list(size) != list(want):
        add('error', f'Kích thước {size[0]} × {size[1]} px, khuôn cần {want[0]} × {want[1]} px.')
    dpi_value = round(float(dpi[0])) if dpi else None
    stats['dpi'] = dpi_value
    if dpi_value is None:
        add('warn', 'File không ghi DPI; nhà in có thể hiểu là 72 DPI.')
    elif dpi_value < profile.get('min_dpi', 300):
        add('error', f'{dpi_value} DPI, thấp hơn mức tối thiểu {profile["min_dpi"]} DPI.')
    elif dpi_value < profile.get('good_dpi', profile.get('min_dpi', 300)):
        add('warn', f'{dpi_value} DPI: nhà in nhận nhưng nên đạt {profile["good_dpi"]} DPI.')
    work_dpi = dpi_value or profile.get('min_dpi', 300)

    alpha = rgba[..., 3]
    ink = alpha >= 128
    covered = int(ink.sum())
    stats['coverage_pct'] = round(covered / ink.size * 100, 1)
    if covered == 0:
        add('error', 'File trống: không có pixel nào được in.')
        return {'file': path.name, 'status': 'error', 'items': items, 'stats': stats}
    has_alpha = bool((alpha < 255).any())
    if has_alpha and not profile.get('transparent_ok', True):
        share = float((alpha < 255).mean() * 100)
        add('error', f'Còn {share:.1f}% diện tích trong suốt; in tràn cần phủ kín tới mép.')
    if profile.get('transparent_ok', True):
        semi = (alpha > 0) & (alpha < 255)
        pct = float(semi.sum() / max(1, int((alpha > 0).sum())) * 100)
        stats['semi_alpha_pct'] = round(pct, 2)
        if pct > profile.get('max_semi_alpha_pct', 3):
            add('warn', f'{pct:.1f}% pixel bán trong suốt: khi in DTG/DTF dễ thành viền mờ hoặc lốm đốm. '
                        'Nên làm cứng viền (alpha 0 hoặc 255) hoặc thêm nền.')
        line_px = _px(profile.get('min_line_mm', 0), work_dpi)
        if line_px >= 2:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (line_px, line_px))
            mask = ink.astype(np.uint8)
            lost = mask & (1 - cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel))
            # Opening also shaves 1-2 px slivers off every curved edge; only long runs are real thin lines.
            _, _, parts, _ = cv2.connectedComponentsWithStats(lost, connectivity=8)
            areas = parts[1:, cv2.CC_STAT_AREA]
            thin = int(areas[areas >= line_px * line_px * 4].sum())
            stats['thin_pct'] = round(thin / covered * 100, 2)
            if thin > 1000:   # ~3 cm of hairline; anti-aliased corners of normal shapes stay far below
                add('warn', f'Có nét mảnh hơn {profile["min_line_mm"]} mm ({stats["thin_pct"]}% phần in) '
                            'có thể bị mất hoặc đứt khi in.')
        speck_px = _px(profile.get('min_speck_mm', 0), work_dpi)
        if speck_px >= 2:
            count, _, comp, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
            small = int((comp[1:, cv2.CC_STAT_AREA] < speck_px * speck_px).sum())
            stats['specks'] = small
            if small >= 5:
                add('warn', f'{small} chấm nhỏ hơn {profile["min_speck_mm"]} mm: thường là rác còn sót, '
                            'in ra thành hạt bẩn.')
    gamut = _gamut(np.ascontiguousarray(rgba[..., :3]))
    if gamut is not None:
        stats['out_of_gamut_pct'] = round(gamut, 1)
        if gamut > 5:
            add('warn', f'{gamut:.0f}% màu nằm ngoài dải CMYK của nhà in: sẽ in ra nhạt hoặc lệch màu.')
    status = 'error' if any(i['level'] == 'error' for i in items) else 'warn' if items else 'ok'
    return {'file': path.name, 'status': status, 'items': items, 'stats': stats}


def run(files, profile_id, path=PROFILES):
    """files: [(label, path, expected_size or None)] -> report for the whole job."""
    table = profiles(path)
    if profile_id not in table:
        raise ValueError('Hồ sơ nhà in không tồn tại.')
    profile = table[profile_id]
    results = []
    for label, file, expected in files:
        try:
            result = check_file(file, profile, expected)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            result = {'file': Path(file).name, 'status': 'error', 'stats': {},
                      'items': [{'level': 'error', 'text': f'Không đọc được file: {exc}'[:200]}]}
        results.append({'label': label, **result})
    worst = max((LEVELS.index(r['status']) for r in results), default=0)
    return {'profile': profile_id, 'label': profile['label'], 'verified': profile.get('verified'),
            'status': LEVELS[worst], 'files': results, 'gamut_check': CMYK_ICC.is_file()}
