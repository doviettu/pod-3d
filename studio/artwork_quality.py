"""Offline pixel locks, exterior-background rejection and stable repeat extension."""
import cv2
import numpy as np


def exterior_background(image):
    """Only near-white connected to the outer border, preserving enclosed white artwork."""
    white = (image.min(2) > 240).astype(np.uint8)
    _, labels = cv2.connectedComponents(white, connectivity=8)
    edge = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    return np.isin(labels, edge[edge != 0])


def locked_composite(original, candidate, editable):
    if original.shape != candidate.shape or editable.shape != original.shape[:2]:
        raise ValueError('Mặt nạ và artwork phải cùng kích thước.')
    if editable.dtype != np.bool_:
        raise ValueError('Vùng được sửa phải là mặt nạ boolean.')
    return np.where(editable[..., None], candidate, original)


def stable_sleeve(image, known):
    """Keep every observed pixel; use one fixed donor/phase for all missing rows."""
    valid = known.mean(0) >= .98
    # Use the longest continuous strip, never bridge a hole in the donor.
    edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
    if not len(edges):
        raise ValueError('Tay áo không có dải liên tục đủ dữ liệu; cần chọn lại vùng nguồn.')
    spans = list(zip(edges[::2], edges[1::2]))
    a, b = max(spans, key=lambda pair: pair[1] - pair[0])
    if b - a < 16:
        raise ValueError('Dải tay áo quá hẹp; cần phục hồi hoa văn và duyệt thủ công.')
    margin = max(1, (b - a) // 10)
    a, b = a + margin, b - margin
    length = b - a
    phase = (np.arange(image.shape[1]) - a) % (2 * length)
    donor = a + np.where(phase < length, phase, 2 * length - 1 - phase)
    candidate = image[:, donor]
    eligible = known[:, donor]
    # Missing donor rows remain explicit holes for the caller; do not certify invented pixels.
    editable = ~known & eligible
    return locked_composite(image, candidate, editable), ~known & ~eligible


def repeat_bands(image, known, bands=None, max_error=12.0, periods=None):
    """Extend explicit full-band regions; never choose a new period per arbitrary row block."""
    result = image.copy()
    missing = ~known.copy()
    height, width = known.shape
    smooth = cv2.GaussianBlur(image, (3, 3), 0).astype(np.float32)
    supplied_periods = periods or {}
    confirmed = []
    for y0, y1 in (bands or [(0, height)]):
        if not (type(y0) is int and type(y1) is int and 0 <= y0 < y1 <= height):
            raise ValueError('Vị trí dải hoa văn không hợp lệ.')
        valid = known[y0:y1].all(0)
        edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
        spans = list(zip(edges[::2], edges[1::2]))
        if not spans:
            continue
        a, b = max(spans, key=lambda pair: pair[1] - pair[0])
        segment = smooth[y0:y1, a:b]
        if b - a < 64 or segment.std(axis=1).mean() < 8:
            continue
        expected = supplied_periods.get((y0, y1))
        if expected is not None and (type(expected) is not int or not 1 <= expected <= (b - a) // 2):
            continue
        candidates = [expected] if expected is not None else range(24, (b - a) // 2 + 1)
        scored = [(float(np.abs(segment[:, p:] - segment[:, :-p]).mean()), p) for p in candidates]
        error, period = min(scored)
        if error > max_error:
            continue
        x = np.arange(width)
        donor = a + (x - a) % period
        editable = missing[y0:y1]
        result[y0:y1] = locked_composite(result[y0:y1], image[y0:y1, donor], editable)
        missing[y0:y1] = False
        confirmed.append({'rows': [y0, y1], 'period_px': period, 'error': round(error, 3), 'phase_origin': 'source-grid-x'})
    return result, missing, confirmed
