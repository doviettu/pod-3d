"""Build an unwrap map (print panel -> mockup pixel) for each panel of a template.

Needs one or more calibration pairs made with the same 3D mockup: the mockup image plus
the real print files of that design. The map is learned from feature matches and then
reused for every new mockup of that garment.
"""
import cv2
import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree

from . import io
from .template import Template

WORK_LONG_SIDE = 1600  # panels are matched at this size; maps are stored at this size
GRID_STEP = 4
FLOW_ITERS = 4
SMOOTHING = 20.0
TEXTURE_MIN = 25.0  # gradient strength needed before a flow vector is trusted
COVER_FULL = 20.0  # work px from the nearest correspondence that are fully trusted
COVER_FADE = 30.0
AGREE_OK = 12.0  # Lab structure error that is still trusted
AGREE_FADE = 10.0


def _work_scale(size):
    return WORK_LONG_SIDE / max(size)


def _match(panel_img, mockup, region):
    """SIFT matches between a panel (work size) and the mockup, limited to a region."""
    h, w = mockup.shape[:2]
    x0, y0, x1, y1 = region
    mask = np.zeros((h, w), np.uint8)
    mask[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 255
    sift = cv2.SIFT_create(30000)
    k1, d1 = sift.detectAndCompute(cv2.cvtColor(panel_img, cv2.COLOR_BGR2GRAY), None)
    k2, d2 = sift.detectAndCompute(cv2.cvtColor(mockup, cv2.COLOR_BGR2GRAY), mask)
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10:
        return np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32)
    pairs = cv2.BFMatcher().knnMatch(d1, d2, k=2)
    good = [a for a, b in (p for p in pairs if len(p) == 2) if a.distance < 0.75 * b.distance]
    P = np.float32([k1[g.queryIdx].pt for g in good])
    Q = np.float32([k2[g.trainIdx].pt for g in good])
    return P, Q


def _robust_filter(P, Q, thresh):
    """Keep matches that agree with one loose homography, then with a smooth local fit."""
    if len(P) < 12:
        return P, Q
    _, inl = cv2.findHomography(P, Q, cv2.RANSAC, thresh)
    keep = inl.ravel().astype(bool)
    P, Q = P[keep], Q[keep]
    for _ in range(3):
        if len(P) < 12:
            break
        f = RBFInterpolator(P, Q, kernel="thin_plate_spline", smoothing=len(P) * 2.0, neighbors=min(150, len(P)))
        r = np.linalg.norm(f(P) - Q, axis=1)
        keep = r < max(2.0, 3 * np.median(r))
        if keep.all():
            break
        P, Q = P[keep], Q[keep]
    return P, Q


def calibrate_panel(template: Template, panel, pairs, log=print):
    """pairs: list of (mockup_bgr, panel_bgr_fullsize). Writes templates/<name>/map_<panel>.npz."""
    s = _work_scale(panel.size)
    ww, wh = round(panel.size[0] * s), round(panel.size[1] * s)
    allP, allQ = [], []
    for mockup, full in pairs:
        work = cv2.resize(full, (ww, wh), interpolation=cv2.INTER_AREA)
        P, Q = _match(work, mockup, panel.region)
        thresh = 0.02 * max(mockup.shape[:2])
        P, Q = _robust_filter(P, Q, thresh)
        log(f"  {panel.name}: {len(P)} matches kept")
        allP.append(P)
        allQ.append(Q)
    P0, Q0 = np.concatenate(allP), np.concatenate(allQ)
    if len(P0) < 30:
        raise RuntimeError(f"{panel.name}: only {len(P0)} matches, cannot calibrate")
    works = [cv2.resize(full, (ww, wh), interpolation=cv2.INTER_AREA) for _, full in pairs]
    mockups = [m for m, _ in pairs]
    P, Q = P0, Q0
    grid = _fit_grid(P, Q, ww, wh)
    for it in range(FLOW_ITERS):
        fP, fQ = _flow_points(grid, mockups, works)
        P, Q = np.concatenate([P0, fP]), np.concatenate([Q0, fQ])
        grid = _fit_grid(P, Q, ww, wh)
        log(f"  {panel.name}: refine {it + 1}/{FLOW_ITERS}, {len(fP)} flow points")
    ys, xs = np.mgrid[0:wh:GRID_STEP, 0:ww:GRID_STEP]
    dist = cKDTree(P).query(np.stack([xs.ravel(), ys.ravel()], 1))[0].reshape(ys.shape)
    cov = np.clip(1 - (dist - COVER_FULL) / COVER_FADE, 0, 1)
    cov = np.minimum(cov, _agreement(grid, mockups, works))
    cov = np.minimum(cov, _not_foreshortened(grid))
    cov = _main_region(cov)
    log(f"  {panel.name}: trusted area {100 * (cov > 0.5).mean():.0f}%")
    # Mockup pixels per work pixel, used to pick the unwrap resolution.
    jac = np.median(np.linalg.norm(np.diff(grid, axis=1), axis=-1)[cov[:, 1:] > 0.5]) / GRID_STEP
    np.savez_compressed(
        template.map_path(panel.name),
        grid=grid.astype(np.float32), cov=cov.astype(np.float32),
        work_size=np.array([ww, wh]), step=GRID_STEP, density=jac,
        mockup_size=np.array(pairs[0][0].shape[1::-1]),
    )
    log(f"  {panel.name}: saved map, {jac:.2f} mockup px per work px")


def _agreement(grid, mockups, works):
    """Trust only where every calibration mockup, unwrapped, looks like its real panel.
    One design alone can agree by chance (a black background matches anything black)."""
    from .unwrap import remap_grid
    gh, gw = grid.shape[:2]
    worst = None
    for mockup, work in zip(mockups, works):
        wh, ww = work.shape[:2]
        un = remap_grid(grid, mockup, (ww, wh))
        a = cv2.cvtColor(cv2.GaussianBlur(work, (0, 0), 2), cv2.COLOR_BGR2LAB).astype(np.float32)
        b = cv2.cvtColor(cv2.GaussianBlur(un, (0, 0), 2), cv2.COLOR_BGR2LAB).astype(np.float32)
        # compare structure, not absolute color: the mockup is shaded
        a -= cv2.GaussianBlur(a, (0, 0), 25)
        b -= cv2.GaussianBlur(b, (0, 0), 25)
        err = cv2.GaussianBlur(np.linalg.norm(a - b, axis=-1), (0, 0), 6)
        err = cv2.resize(err, (gw, gh), interpolation=cv2.INTER_AREA)
        worst = err if worst is None else np.maximum(worst, err)
    return np.clip(1 - (worst - AGREE_OK) / AGREE_FADE, 0, 1)


def _not_foreshortened(grid):
    """Where the cloth turns away from the camera the map squeezes many panel pixels into a
    few mockup pixels; the unwrap there is a smear, so it is not trusted."""
    jx = np.linalg.norm(np.gradient(grid, axis=1), axis=-1)
    jy = np.linalg.norm(np.gradient(grid, axis=0), axis=-1)
    j = np.minimum(jx, jy)
    ref = np.median(j)
    return np.clip((j / ref - 0.55) / 0.2, 0, 1)


def _main_region(cov):
    """Keep the largest trusted region; islands are usually another part of the garment."""
    m = (cov > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
    if n <= 2:
        return cov
    keep = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = cv2.dilate((lab == keep).astype(np.uint8), np.ones((5, 5), np.uint8))
    return cov * mask


def _thin(P, Q, cell=12):
    """Keep at most one correspondence per cell so the fit stays fast and evenly weighted."""
    key = (P[:, 1] // cell).astype(np.int64) * 100000 + (P[:, 0] // cell).astype(np.int64)
    _, idx = np.unique(key, return_index=True)
    return P[idx], Q[idx]


def _fit_grid(P, Q, ww, wh):
    P, Q = _thin(P, Q)
    f = RBFInterpolator(P.astype(np.float64), Q.astype(np.float64), kernel="thin_plate_spline",
                        smoothing=SMOOTHING, neighbors=min(80, len(P)))
    ys, xs = np.mgrid[0:wh:GRID_STEP, 0:ww:GRID_STEP]
    return f(np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float64)).reshape(ys.shape + (2,))


def _flow_points(grid, mockups, works, stride=6):
    """Correspondences from dense optical flow, only where the panel has texture and the
    flow is consistent forwards and backwards."""
    from .unwrap import remap_grid
    gh, gw = grid.shape[:2]
    outP, outQ = [], []
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    for mockup, work in zip(mockups, works):
        wh, ww = work.shape[:2]
        un = remap_grid(grid, mockup, (ww, wh))
        a = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(un, cv2.COLOR_BGR2GRAY)
        b = cv2.normalize(b, None, 0, 255, cv2.NORM_MINMAX)
        a = cv2.normalize(a, None, 0, 255, cv2.NORM_MINMAX)
        fwd = dis.calc(a, b, None)
        bwd = dis.calc(b, a, None)
        ys, xs = np.mgrid[0:wh:stride, 0:ww:stride].astype(np.float32)
        f = fwd[ys.astype(int), xs.astype(int)]
        tx, ty = xs + f[..., 0], ys + f[..., 1]
        ok = (tx >= 0) & (ty >= 0) & (tx < ww - 1) & (ty < wh - 1)
        bb = bwd[np.clip(ty, 0, wh - 1).astype(int), np.clip(tx, 0, ww - 1).astype(int)]
        fb = np.hypot(f[..., 0] + bb[..., 0], f[..., 1] + bb[..., 1])
        gmag = cv2.GaussianBlur(np.hypot(cv2.Sobel(a, cv2.CV_32F, 1, 0), cv2.Sobel(a, cv2.CV_32F, 0, 1)), (0, 0), 3)
        tex = gmag[ys.astype(int), xs.astype(int)] > TEXTURE_MIN
        ok &= (fb < 1.0) & tex & (np.hypot(f[..., 0], f[..., 1]) < 40)
        px, py = xs[ok], ys[ok]
        qx = _interp2_pts(grid[..., 0], tx[ok] / GRID_STEP, ty[ok] / GRID_STEP)
        qy = _interp2_pts(grid[..., 1], tx[ok] / GRID_STEP, ty[ok] / GRID_STEP)
        outP.append(np.stack([px, py], 1))
        outQ.append(np.stack([qx, qy], 1))
    return np.concatenate(outP).astype(np.float32), np.concatenate(outQ).astype(np.float32)


def _interp2_pts(a, x, y):
    gh, gw = a.shape
    x = np.clip(x, 0, gw - 1.001); y = np.clip(y, 0, gh - 1.001)
    x0 = np.floor(x).astype(int); y0 = np.floor(y).astype(int); fx = x - x0; fy = y - y0
    return (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x0 + 1] * fx * (1 - fy)
            + a[y0 + 1, x0] * (1 - fx) * fy + a[y0 + 1, x0 + 1] * fx * fy)


def calibrate_color(template: Template, pairs_by_panel, log=print):
    """Fit an affine color transform mockup -> print from unwrapped calibration panels."""
    from .unwrap import unwrap
    src, dst = [], []
    for panel, mockup, full in pairs_by_panel:
        out, cov = unwrap(template, panel, mockup, color=False)
        ref = cv2.resize(full, out.shape[1::-1], interpolation=cv2.INTER_AREA)
        k = 9
        a = cv2.GaussianBlur(out, (k, k), 0)[cov > 0.8]
        b = cv2.GaussianBlur(ref, (k, k), 0)[cov > 0.8]
        idx = np.random.default_rng(0).choice(len(a), min(len(a), 200000), replace=False)
        src.append(a[idx])
        dst.append(b[idx])
    X = np.concatenate(src).astype(np.float64)
    Y = np.concatenate(dst).astype(np.float64)
    X1 = np.hstack([X, np.ones((len(X), 1))])
    M, *_ = np.linalg.lstsq(X1, Y, rcond=None)  # 4x3
    err = np.abs(X1 @ M - Y).mean()
    np.savez_compressed(template.color_path, M=M)
    log(f"  color transform saved, mean error {err:.1f}/255")


def run(template: Template, sets, log=print):
    """sets: list of dicts {"mockup": path, "<Panel>": path, ...} from the same mockup."""
    loaded = [{k: io.read(v) for k, v in s.items()} for s in sets]
    color_pairs = []
    for panel in template.panels:
        if panel.source != "unwrap":
            continue
        pairs = [(s["mockup"], s[panel.name]) for s in loaded if panel.name in s]
        if not pairs:
            log(f"  {panel.name}: no sample file, skipped")
            continue
        try:
            if panel.method == "strip":
                calibrate_strip(template, panel, pairs, log)
            else:
                calibrate_panel(template, panel, pairs, log)
                color_pairs += [(panel, m, f) for m, f in pairs]
        except RuntimeError as e:
            log(f"  {panel.name}: NOT calibrated ({e})")
            template.map_path(panel.name).unlink(missing_ok=True)
    calibrate_color(template, color_pairs, log)


def _profile_norm(x):
    x = x.astype(np.float32)
    return (x - x.mean(0)) / (x.std(0) + 1e-3)


def calibrate_strip(template: Template, panel, pairs, log=print):
    """Sleeves: rows of the panel are bands. Align the panel's center column with a curve
    drawn along the sleeve in the mockup, then map each panel row across the sleeve."""
    s = _work_scale(panel.size)
    ww, wh = round(panel.size[0] * s), round(panel.size[1] * s)
    mh, mw = pairs[0][0].shape[:2]
    curve = np.array(panel.curve, np.float64) * [mw, mh]
    seg = np.linalg.norm(np.diff(curve, axis=0), axis=1)
    L = seg.sum()
    n_s = int(L)
    t = np.linspace(0, L, n_s)
    cum = np.concatenate([[0], np.cumsum(seg)])
    cx = np.interp(t, cum, curve[:, 0]); cy = np.interp(t, cum, curve[:, 1])
    tx, ty = np.gradient(cx), np.gradient(cy)
    tn = np.hypot(tx, ty); tx, ty = tx / tn, ty / tn
    nx, ny = ty, -tx  # normal across the sleeve
    half = panel.halfwidth * mw
    rows = 400
    costs = None
    for mockup, full in pairs:
        work = cv2.resize(full, (ww, wh), interpolation=cv2.INTER_AREA)
        lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB).astype(np.float32)
        prof_p = lab[:, int(ww * 0.4):int(ww * 0.6)].mean(1)
        prof_p = cv2.resize(prof_p[:, None, :], (1, rows), interpolation=cv2.INTER_AREA)[:, 0]
        mlab = cv2.cvtColor(mockup, cv2.COLOR_BGR2LAB).astype(np.float32)
        offs = np.linspace(-half * 0.6, half * 0.6, 9)
        sx = (cx[:, None] + offs[None] * nx[:, None]).astype(np.float32)
        sy = (cy[:, None] + offs[None] * ny[:, None]).astype(np.float32)
        prof_m = cv2.remap(mlab, sx, sy, cv2.INTER_LINEAR).mean(1)
        a, b = _profile_norm(prof_p), _profile_norm(prof_m)
        c = ((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)
        costs = c if costs is None else costs + c
    path = _dtw_path_cost(costs)
    # panel row (0..rows) -> arc length sample index; make it a smooth monotonic function
    sm = np.array([path[path[:, 0] == r, 1].mean() for r in range(rows)])
    sm = np.maximum.accumulate(cv2.GaussianBlur(sm.astype(np.float32)[:, None], (1, 0), 3)[:, 0])
    row_s = np.interp(np.arange(wh), (np.arange(rows) + 0.5) * wh / rows, sm)
    # Mockup px per work px. One value for the whole sleeve: the local DTW slope is too noisy.
    k = np.full(wh, np.clip(np.median(np.gradient(row_s)[np.gradient(row_s) > 0]), 0.05, 5))
    gys, gxs = np.mgrid[0:wh:GRID_STEP, 0:ww:GRID_STEP]
    si = np.clip(row_s[gys], 0, n_s - 1)
    px = np.interp(si, np.arange(n_s), cx); py = np.interp(si, np.arange(n_s), cy)
    pnx = np.interp(si, np.arange(n_s), nx); pny = np.interp(si, np.arange(n_s), ny)
    off = (gxs - ww / 2) * k[gys]
    grid = np.dstack([px + off * pnx, py + off * pny])
    cov = np.clip(1 - (np.abs(off) - half * 0.7) / (half * 0.3), 0, 1)
    np.savez_compressed(
        template.map_path(panel.name),
        grid=grid.astype(np.float32), cov=cov.astype(np.float32),
        work_size=np.array([ww, wh]), step=GRID_STEP, density=float(np.median(k)),
        mockup_size=np.array([mw, mh]),
    )
    log(f"  {panel.name}: strip map saved, {np.median(k):.2f} mockup px per work px")


def _dtw_path_cost(cost):
    n, m = cost.shape
    D = np.full((n + 1, m + 1), np.inf, np.float64)
    D[0, :] = 0  # the panel may start anywhere along the curve (top of sleeve can be hidden)
    for i in range(1, n + 1):
        prev = D[i - 1]
        best = np.minimum(prev[1:], prev[:-1])
        cur = D[i]
        row = cost[i - 1]
        for j in range(1, m + 1):
            v = best[j - 1]
            if cur[j - 1] < v:
                v = cur[j - 1]
            cur[j] = row[j - 1] + v
    j = int(np.argmin(D[n, 1:])) + 1
    i, path = n, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        cand = [D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]]
        k = int(np.argmin(cand))
        i, j = (i - 1, j - 1) if k == 0 else (i - 1, j) if k == 1 else (i, j - 1)
    return np.array(path[::-1])
