"""Conservative local template matching; scores are heuristics, not probabilities."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import warnings

import numpy as np
from PIL import Image, ImageOps

MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 50_000_000


def _decode(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("Ảnh phải là JPEG/PNG/WebP, tối đa 32 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if (im.format not in {"JPEG", "PNG", "WEBP"}
                        or getattr(im, "n_frames", 1) != 1
                        or im.width * im.height > MAX_PIXELS):
                    raise ValueError("Ảnh tĩnh tối đa 50 triệu pixel.")
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                # Decode only after dimensions pass; JPEG draft avoids a full RGB allocation.
                im.draft("RGB", (384, 384))
                orientation = im.getexif().get(274)
                ratio = im.width / im.height
                if orientation in (5, 6, 7, 8):
                    ratio = 1 / ratio
                im.thumbnail((384, 384), Image.Resampling.LANCZOS)
                im = ImageOps.exif_transpose(im)
                rgba = im.convert("RGBA")
                base = Image.new("RGBA", rgba.size, "white")
                base.alpha_composite(rgba)
                rgb = base.convert("RGB")
                thumb = np.asarray(rgb.resize((96, 96), Image.Resampling.LANCZOS),
                                   dtype=np.float32) / 255
                gray = np.asarray(rgb.convert("L").resize((17, 16),
                                  Image.Resampling.LANCZOS), dtype=np.int16)
                dhash = gray[:, 1:] - gray[:, :-1]
                return ratio, thumb, dhash
    except (OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, ValueError):
        raise ValueError("Ảnh không hợp lệ, bị cắt hoặc vượt giới hạn.") from None


def _shape(thumb):
    mask = np.min(thumb, axis=2) < .90
    border = np.concatenate((mask[0], mask[-1], mask[:, 0], mask[:, -1]))
    if border.mean() > .01 or not .08 < mask.mean() < .8:
        return None
    # Preserve framing: no bounding-box crop or mirror normalization.
    return mask


def _result(selected=None, suggested=None, method="manual", score=0, candidates=None,
            reason="Chưa đủ bằng chứng; vui lòng chọn mẫu thủ công."):
    return dict(selected_template=selected, suggested_template=suggested,
                method=method, reason=reason, score=round(float(score), 6),
                candidates=candidates or [])


def detect_template(image_bytes, references):
    """Match only explicit server-trusted references; invalid input raises ValueError."""
    ratio, thumb, dhash = _decode(image_bytes)
    digest = hashlib.sha256(image_bytes).digest()
    matches, candidates, eligible = set(), {}, set()
    for ref in references:
        if not isinstance(ref, dict) or ref.get("ready") is not True:
            continue
        if (not isinstance(ref.get("id"), str) or not ref["id"]
                or not isinstance(ref.get("label"), str)
                or not isinstance(ref.get("path"), Path)
                or ref.get("background") not in ("white", "complex", "model")):
            continue
        try:
            with ref["path"].open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
            rr, rt, rh = _decode(data)
        except (OSError, ValueError):
            continue
        ident = ref["id"]
        if hashlib.sha256(data).digest() == digest:
            matches.add(ident)
        aspect = abs(ratio / rr - 1)
        error = float(np.mean(np.abs(thumb - rt)))
        mirror_error = float(np.mean(np.abs(thumb - rt[:, ::-1])))
        # Ignore flat-region gradients within JPEG rounding noise.
        stable = (np.abs(dhash) >= 3) | (np.abs(rh) >= 3)
        hamming = int(np.count_nonzero(((dhash > 0) != (rh > 0)) & stable))
        score = max(0., 1 - error * 4)
        method = "reference-match"
        strict = (aspect <= .004 and hamming <= 4 and error <= .012
                  and float(np.quantile(np.abs(thumb - rt), .99)) <= .09)
        mirrored = mirror_error + .003 < error
        if ref["background"] == "white" and ref.get("allow_geometry", True) is True and not strict:
            left, right = _shape(thumb), _shape(rt)
            if left is not None and right is not None:
                union = np.count_nonzero(left | right)
                score = float(np.count_nonzero(left & right) / union)
                method = "silhouette"
                strict = score >= .995 and aspect <= .004 and not mirrored
        if mirrored:
            strict = False
        candidate = dict(id=ident, label=ref["label"], score=round(score, 6),
                         method=method)
        if ident not in candidates or score > candidates[ident]["score"]:
            candidates[ident] = candidate
        if strict:
            eligible.add(ident)
    ranked = sorted(candidates.values(), key=lambda c: (-c["score"], c["id"]))
    if matches:
        exact = [dict(c, score=1., method="exact") for c in ranked if c["id"] in matches]
        rest = [c for c in ranked if c["id"] not in matches]
        if len(matches) == 1:
            ident = next(iter(matches))
            return _result(ident, ident, "exact", 1, exact + rest,
                           "Ảnh trùng khớp duy nhất với mẫu tham chiếu.")
        return _result(score=1, candidates=exact + rest,
                       reason="Ảnh trùng nhiều mẫu; vui lòng chọn thủ công.")
    if not ranked:
        return _result()
    best = ranked[0]
    margin = best["score"] - ranked[1]["score"] if len(ranked) > 1 else 1.
    if best["id"] in eligible and len(eligible) == 1 and margin >= .04:
        return _result(best["id"], best["id"], best["method"], best["score"], ranked,
                       "Ảnh rất gần mẫu tham chiếu. Hãy kiểm tra lại bố cục trước khi xử lý.")
    return _result(suggested=best["id"] if best["score"] >= .75 and margin >= .04 else None,
                   score=best["score"], candidates=ranked,
                   reason="Ảnh khác biệt hoặc nhiều mẫu tương tự; vui lòng chọn thủ công.")


def _catalog(path):
    root = path.resolve().parent
    with path.open("rb") as handle:
        raw = handle.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Danh mục quá lớn.")
    refs = json.loads(raw)
    if not isinstance(refs, list):
        raise ValueError("Danh mục phải là danh sách.")
    result = []
    for ref in refs:
        if (not isinstance(ref, dict) or not {"id", "label", "path", "background", "ready"} <= set(ref)
                or set(ref) - {"id", "label", "path", "background", "ready", "allow_geometry"}
                or type(ref.get("allow_geometry", True)) is not bool
                or not all(isinstance(ref[k], str) and ref[k] for k in ("id", "label", "path"))
                or type(ref["ready"]) is not bool
                or ref["background"] not in ("white", "complex", "model")):
            raise ValueError("Dữ liệu danh mục không hợp lệ.")
        target = (root / ref["path"]).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Đường dẫn phải nằm trong thư mục danh mục.")
        result.append(dict(ref, path=target))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--references", required=True, type=Path)
    args = parser.parse_args()
    try:
        refs = _catalog(args.references)
        with args.image.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        result = detect_template(data, refs)
    except (OSError, ValueError):
        result = _result(reason="Không đọc được ảnh hoặc danh mục hợp lệ.")
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
