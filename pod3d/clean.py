"""Remove the knit texture and shading the mockup adds, and snap flat areas to a clean palette."""
import cv2
import numpy as np

SNAP_DIST = 14.0  # Lab distance under which a pixel is snapped to its palette color


def denoise(img):
    """Edge-preserving smoothing: removes knit texture, keeps motif edges."""
    out = cv2.bilateralFilter(img, 7, 30, 5)
    return cv2.bilateralFilter(out, 7, 20, 3)


def palette(img, k=12, sample=60000):
    """Main colors of the design (Lab), by k-means on a sample of pixels."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    rng = np.random.default_rng(0)
    pts = lab[rng.choice(len(lab), min(sample, len(lab)), replace=False)]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(pts, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    return centers[counts > 0.002 * len(pts)]


def snap(img, pal, dist=SNAP_DIST):
    """Replace pixels close to a palette color with that exact color; leave the rest (photos, faces)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    flat = lab.reshape(-1, 3)
    near = np.zeros(len(flat), bool)
    for a in range(0, len(flat), 1 << 20):  # chunks keep memory low on 40-megapixel panels
        c = flat[a:a + (1 << 20)]
        d = np.linalg.norm(c[:, None, :] - pal[None, :, :], axis=-1)
        i = d.argmin(1)
        n = d[np.arange(len(c)), i] < dist
        c[n] = pal[i[n]]
        near[a:a + len(c)] = n
    near = near.reshape(img.shape[:2])
    # Snap only inside flat areas: in a drawing or photo, single pixels may sit near a palette
    # color by chance and snapping them leaves a posterized, speckled look.
    dense = cv2.blur(near.astype(np.float32), (9, 9)) > 0.9
    keep = near & dense
    snapped = flat.reshape(lab.shape)
    out_lab = np.where(keep[..., None], snapped, cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32))
    out = cv2.cvtColor(np.clip(out_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    return out, keep.mean()


def clean(img, k=12):
    """Returns (cleaned image, palette). The palette is reused after upscaling."""
    den = denoise(img)
    pal = palette(den, k)
    out, frac = snap(den, pal)
    return out, pal, frac
