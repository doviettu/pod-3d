"""Local inverse projection of calibrated UV maps; never a 3D/quality approval."""

import json
from pathlib import Path
import re
import uuid

import cv2
import numpy as np
from PIL import Image, ImageDraw

MAX_EDGE = 1600
MAX_PIXELS = 50_000_000
LABEL_HEIGHT = 64
HEX_ID = re.compile(r"[0-9a-f]{32}")
PANEL_NAME = re.compile(r"[A-Za-z0-9_-]{1,50}")


def _inside(folder, relative):
    path = folder / relative
    resolved = path.resolve()
    if resolved != path or not resolved.is_relative_to(folder):
        raise ValueError("Đường dẫn xem trên áo không hợp lệ.")
    return path


def _recorded(engine, job_id, folder, relative):
    expected = _inside(folder, relative)
    try:
        actual = Path(engine.file_path(job_id, relative)).resolve()
    except FileNotFoundError:
        return None
    if actual != expected or not actual.is_file():
        raise ValueError("Tệp xem trên áo không hợp lệ.")
    return actual


def _panel_source(engine, job_id, job, folder, name):
    saved = job.get("reviews", {}).get(name)
    if saved is not None:
        revision = saved.get("revision") if isinstance(saved, dict) else None
        if not isinstance(revision, str) or not HEX_ID.fullmatch(revision):
            raise ValueError("Phiên bản duyệt không hợp lệ; hãy lưu lại bản duyệt.")
        spec_path = _inside(folder, f"reviews/{revision}/spec.json")
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Không đọc được bản duyệt; hãy lưu lại bản duyệt.") from None
        if not isinstance(spec, dict) or type(spec.get("approved")) is not bool:
            raise ValueError("Trạng thái bản duyệt không hợp lệ.")
        approved = spec["approved"]
        relative = f"reviews/{revision}/{name}-{'approved' if approved else 'draft'}.png"
        path = _recorded(engine, job_id, folder, relative)
        if path is not None:
            return path, approved, relative
    for source in ("openai", "local"):
        relative = f"{source}/{name}.png"
        path = _recorded(engine, job_id, folder, relative)
        if path is not None:
            return path, False, relative
    return None, False, None


def _bounded_size(size, edge=MAX_EDGE):
    scale = min(1.0, edge / max(size))
    return tuple(max(1, round(v * scale)) for v in size)


def _image(path, size=None, expected=None):
    try:
        with Image.open(path) as image:
            if (image.format not in ("JPEG", "PNG", "WEBP")
                    or image.width * image.height > MAX_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                    or (expected is not None and image.size != tuple(expected))):
                raise ValueError("Ảnh xem trên áo không hợp lệ (tối đa 50 triệu pixel).")
            target = size or _bounded_size(image.size, MAX_EDGE - LABEL_HEIGHT)
            image.draft("RGB", target)
            image = image.resize(target, Image.Resampling.LANCZOS)
            return np.array(image.convert("RGB"))
    except (OSError, Image.DecompressionBombError):
        raise ValueError("Không đọc được ảnh xem trên áo.") from None


def _load_map(path):
    try:
        with np.load(path, allow_pickle=False) as data:
            grid, cov = data["grid"], data["cov"]
            work = np.asarray(data["work_size"], dtype=float)
            mockup = np.asarray(data["mockup_size"], dtype=float)
            step = float(data["step"].item())
        if (grid.ndim != 3 or grid.shape[2] != 2 or min(grid.shape[:2]) < 2
                or grid.shape[:2] != cov.shape or grid.shape[0] * grid.shape[1] > 1_000_000
                or work.shape != (2,) or mockup.shape != (2,)
                or not np.isfinite(work).all() or not np.isfinite(mockup).all()
                or (work < 1).any() or (mockup < 1).any()
                or (work != np.floor(work)).any() or (mockup != np.floor(mockup)).any()
                or not np.isfinite(step) or step <= 0
                or work.prod() > MAX_PIXELS or mockup.prod() > MAX_PIXELS
                or not np.issubdtype(grid.dtype, np.number)
                or not np.issubdtype(cov.dtype, np.number)
                or np.iscomplexobj(grid) or np.iscomplexobj(cov)):
            raise ValueError()
        return grid.astype(np.float32), cov.astype(np.float32), work, step, mockup
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError("Bản đồ phủ UV không hợp lệ; hãy kiểm tra dữ liệu khuôn.") from None


def _foreground(canvas):
    """Clip white-background mockups without removing enclosed white print areas."""
    white = canvas.min(axis=2) >= 245
    border = np.concatenate((white[0], white[-1], white[:, 0], white[:, -1]))
    if border.mean() < .9:
        return np.ones(white.shape, dtype=bool)
    contours, _ = cv2.findContours((~white).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask = np.zeros(white.shape, dtype=np.uint8)
    contours = [contour for contour in contours if cv2.contourArea(contour) >= 9]
    cv2.drawContours(mask, contours, -1, 1, cv2.FILLED)
    return mask.astype(bool)


def _project(canvas, texture, mapping, clip=None, coverage=None):
    """Map flat grid triangles back to mockup pixels using cropped source/dest ROIs."""
    grid, cov, work, step, mockup = mapping
    height, width = canvas.shape[:2]
    th, tw = texture.shape[:2]
    gh, gw = cov.shape
    yy, xx = np.mgrid[:gh, :gw]
    flat = np.stack((xx * step, yy * step), axis=-1)
    visible = ((cov >= 0.5) & np.isfinite(cov) & np.isfinite(grid).all(axis=-1)
               & (flat[..., 0] < work[0]) & (flat[..., 1] < work[1])
               & (grid[..., 0] >= 0) & (grid[..., 0] <= mockup[0] - 1)
               & (grid[..., 1] >= 0) & (grid[..., 1] <= mockup[1] - 1))
    src = (flat * (np.array([tw, th]) / work)).astype(np.float32)
    dst = (grid * (np.array([width, height]) / mockup)).astype(np.float32)
    painted = 0
    for corners in (((0, 0), (0, 1), (1, 1)), ((0, 0), (1, 1), (1, 0))):
        slices = tuple((slice(dy, gh - 1 + dy), slice(dx, gw - 1 + dx)) for dy, dx in corners)
        valid = np.logical_and.reduce([visible[s] for s in slices])
        a, b, c = [dst[s] for s in slices]
        area = (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])
        valid &= np.abs(area) > 1e-4
        for y, x in np.argwhere(valid):
            indices = tuple((y + dy, x + dx) for dy, dx in corners)
            p = np.array([src[i] for i in indices], dtype=np.float32)
            q = np.array([dst[i] for i in indices], dtype=np.float32)
            sx, sy = np.floor(p.min(axis=0)).astype(int)
            ex, ey = np.minimum(np.ceil(p.max(axis=0)).astype(int) + 2, [tw, th])
            dx, dy = np.floor(q.min(axis=0)).astype(int)
            rx, ry = np.minimum(np.ceil(q.max(axis=0)).astype(int) + 1, [width, height])
            if sx < 0 or sy < 0 or dx < 0 or dy < 0 or ex <= sx or ey <= sy or rx <= dx or ry <= dy:
                continue
            p -= np.array([sx, sy], dtype=np.float32)
            q -= np.array([dx, dy], dtype=np.float32)
            matrix = cv2.getAffineTransform(p, q)
            warped = cv2.warpAffine(texture[sy:ey, sx:ex], matrix, (rx - dx, ry - dy),
                                    flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            # Pixel-center tests avoid rounded polygon masks spilling into hidden areas.
            py, px = np.mgrid[:ry - dy, :rx - dx]
            edges = [(v[0] - u[0]) * (py - u[1]) - (v[1] - u[1]) * (px - u[0])
                     for u, v in zip(q, np.roll(q, -1, axis=0))]
            mask = np.logical_and.reduce([e >= -1e-5 for e in edges]) | np.logical_and.reduce([e <= 1e-5 for e in edges])
            if clip is not None:
                mask &= clip[dy:ry, dx:rx]
            canvas[dy:ry, dx:rx][mask] = warped[mask]
            if coverage is not None:
                coverage[dy:ry, dx:rx] |= mask
            painted += int(mask.any())
    return painted


def create_garment_preview(engine, job_id) -> dict:
    """Caller supplies the engine's serial postprocess lock; this function is local only."""
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Mã công việc không hợp lệ.")
    job = engine.get_job(job_id)
    root = Path(engine.root).resolve()
    folder = _inside(root, job_id)
    template = engine.templates.load(job["template"])
    names = [p.name for p in template.panels]
    if (not names or len(names) != len(set(names))
            or any(not isinstance(n, str) or not PANEL_NAME.fullmatch(n) for n in names)):
        raise ValueError("Tên mảnh khuôn không hợp lệ.")
    maps = {name: Path(template.map_path(name)) for name in names}
    if not any(path.is_file() for path in maps.values()):
        raise ValueError("Khuôn chưa có bản đồ phủ UV để xem trên áo 2D; hãy hiệu chuẩn khuôn.")
    inputs = [_inside(folder, "input" + suffix) for suffix in (".jpg", ".png", ".webp")]
    inputs = [path for path in inputs if path.is_file()]
    if len(inputs) != 1:
        raise ValueError("Cần đúng một ảnh tải lên gốc input.jpg, input.png hoặc input.webp.")
    canvas = _image(inputs[0])
    foreground = _foreground(canvas)
    coverage = np.zeros(canvas.shape[:2], dtype=bool)
    panels, missing, drafts, unmapped, sources = [], [], [], [], {}
    for name in names:
        source, approved, relative = _panel_source(engine, job_id, job, folder, name)
        if not approved:
            missing.append(name)
        if source is None or not maps[name].is_file():
            unmapped.append(name)
            continue
        mapping = _load_map(maps[name])
        texture = _image(source, _bounded_size(mapping[2]), template.panel(name).size)
        if not _project(canvas, texture, mapping, clip=foreground, coverage=coverage):
            unmapped.append(name)
            continue
        panels.append(name)
        sources[name] = relative
        if not approved:
            drafts.append(name)
    if not panels:
        raise ValueError("Không có vùng phủ UV khả kiến và mảnh đã lưu để xem trên áo 2D.")
    warnings = ["Bản kiểm tra 2D đã hiệu chuẩn, không phải 3D; không xác nhận chất lượng hay duyệt in."]
    if drafts:
        warnings.append("Mảnh đang dùng bản nháp: " + ", ".join(drafts))
    if missing:
        warnings.append("Mảnh chưa có bản duyệt: " + ", ".join(missing))
    if unmapped:
        warnings.append("Mảnh chưa chiếu được; giữ ảnh gốc tại vùng này: " + ", ".join(unmapped))
    # Mark every unmapped garment pixel so the source artwork cannot look like a successful reconstruction.
    # A nonwhite background has no reliable foreground outline; preserve it instead of hatching the whole photo.
    unknown = foreground & ~coverage if not foreground.all() else np.zeros(foreground.shape, dtype=bool)
    if unknown.any():
        gray = cv2.cvtColor(canvas, cv2.COLOR_RGB2GRAY)
        canvas[unknown] = np.repeat(gray[..., None], 3, axis=2)[unknown]
        yy, xx = np.indices(unknown.shape)
        canvas[unknown & ((xx + yy) % 16 < 2)] = [100, 105, 115]
        warnings.append("Vùng áo có vạch chéo xám còn dùng ảnh gốc, chưa được chiếu artwork; không dùng để xác nhận chi tiết in.")
    image = Image.fromarray(canvas)
    labeled = Image.new("RGB", (max(500, image.width), image.height + LABEL_HEIGHT), "#20242b")
    labeled.paste(image, (0, 0))
    draw = ImageDraw.Draw(labeled)
    draw.text((10, image.height + 8), "DRAFT | CALIBRATED 2D | NOT 3D", fill="white")
    draw.text((10, image.height + 30), "Gray hatching = original, unmapped. No quality approval.", fill="white")
    previews = _inside(folder, "previews")
    previews.mkdir(exist_ok=True)
    target = _inside(folder, f"previews/garment-{uuid.uuid4().hex}.jpg")
    with target.open("xb") as stream:
        labeled.save(stream, format="JPEG", quality=92, subsampling=0)
    engine.add_artifact(job_id, target, "image", "Xem trên áo — bản kiểm tra 2D.jpg")
    return {"url": engine.url(job_id, target), "panels": panels, "missing_panels": missing,
            "draft_panels": drafts, "unmapped_panels": unmapped, "sources": sources,
            "kind": "calibrated-2d", "draft": True, "quality_approved": False,
            "warnings": warnings}
