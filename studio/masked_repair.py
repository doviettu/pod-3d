"""One masked image edit with a locally enforced original-resolution pixel boundary."""
import base64
import copy
import hashlib
import io
import math
from pathlib import Path
import time
import warnings

from PIL import Image, ImageChops, ImageDraw

import openai_api as api
from design_spec import GENERATION_PANELS, apply_patch_mask, design_spec_hash, patch_prompt

MAX_PIXELS = 50_000_000


def _box(value, size):
    if (type(value) not in (list, tuple) or len(value) != 4
            or any(type(n) is not int for n in value)):
        raise ValueError("Boxes must contain integer x, y, width, height")
    x, y, width, height = value
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > size[0] or y + height > size[1]:
        raise ValueError("Box lies outside the flat canvas")
    return x, y, x + width, y + height


def _draw(draw, box, fill):
    left, top, right, bottom = box
    if left < right and top < bottom:
        draw.rectangle((left, top, right - 1, bottom - 1), fill=fill)


def _reference(image):
    stream = io.BytesIO()
    image.save(stream, "PNG")
    # JSON image_url references have a 20 MiB string limit.
    encoded = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    if len(encoded) > 20 * 1024 * 1024:
        raise ValueError("Encoded image exceeds request limit")
    return {"image_url": encoded}


def _read(path, *, rgb=False):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if (image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1
                    or (rgb and image.mode != "RGB")):
                raise ValueError("Expected a single flat RGB image within the pixel limit")
            dpi = image.info.get("dpi")
            if dpi is not None and (
                    type(dpi) not in (list, tuple) or len(dpi) != 2
                    or any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in dpi)):
                raise ValueError("Invalid image DPI")
            return image.copy() if rgb else image.convert("RGB"), dpi


def repair_image(source, destination, settings, *, panel, spec, editable_box,
                 protected_boxes=(), mockup):
    """Repair a manually selected unknown rectangle; never retry a paid request.

    Boxes are original flat-canvas integer [x,y,width,height], half-open.
    The selection records a bounded unknown with manual-selection provenance in
    a private spec copy. Text/logo bounds and protected_boxes always win.
    Input spec is never modified. Pixel locks refer to decoded RGB samples,
    not PNG file bytes. Output is atomic PNG, at original dimensions and DPI.
    """
    started = time.monotonic()
    try:
        api._validate(settings.model, settings.image_size, settings.quality, settings.api_key)
        if not settings.api_key:
            raise ValueError("Missing API key")
        source, destination, mockup = Path(source), Path(destination), Path(mockup)
        if destination.suffix.lower() != ".png" or not destination.parent.is_dir() or destination.is_dir():
            raise ValueError("Invalid output path")
        for input_path in (source, mockup):
            if (destination.resolve() == input_path.resolve()
                    or (destination.exists() and input_path.samefile(destination))):
                raise ValueError("Cannot overwrite an input image")
        input_spec_hash = design_spec_hash(spec)
        # Validate generation panel and spec before opening large images.
        if type(panel) is not str or panel not in GENERATION_PANELS or panel not in spec["panels"]:
            raise ValueError("Unsupported repair panel")
        original, dpi = _read(source, rgb=True)
        size = original.size
        if list(size) != spec["panels"][panel]:
            raise ValueError("Source dimensions must match the spec panel")
        selected = _box(editable_box, size)
        if type(protected_boxes) not in (list, tuple) or len(protected_boxes) > 64:
            raise ValueError("Invalid protected boxes")
        protected = [_box(value, size) for value in protected_boxes]
        for row in spec["text_layers"]:
            if row["panel"] == panel:
                l, t, r, b = row["bounds"]
                protected.append((math.floor(l * size[0]), math.floor(t * size[1]),
                                  math.ceil(r * size[0]), math.ceil(b * size[1])))
        local_mask = Image.new("RGBA", size, (0, 0, 0, 255))
        alpha = local_mask.getchannel("A")
        draw = ImageDraw.Draw(alpha)
        _draw(draw, selected, 0)
        for box in protected:
            _draw(draw, box, 255)
        local_mask.putalpha(alpha)
        if alpha.getextrema()[0] == 255:
            raise ValueError("Selection is fully protected")

        repair_spec = copy.deepcopy(spec)
        l, t, r, b = selected
        # Round normalized bounds outward by one ULP so the shared compositor's
        # inward pixel rounding does not lose integer selection edges.
        bounds = [max(0, math.nextafter(l / size[0], -math.inf)),
                  max(0, math.nextafter(t / size[1], -math.inf)),
                  min(1, math.nextafter(r / size[0], math.inf)),
                  min(1, math.nextafter(b / size[1], math.inf))]
        repair_spec["unknowns"] = [row for row in repair_spec["unknowns"] if row["panel"] != panel] + [{
            "panel": panel, "kind": "missing", "bounds": bounds,
            "description": "Only unknown details within the manually selected repair rectangle.",
            "provenance": "manual: explicit editable_box in original flat-canvas pixels",
        }]
        # Reuse the single binary-mask compositor, including its text-layer checks.
        apply_patch_mask(repair_spec, panel, original, original, local_mask)
        prompt = patch_prompt(repair_spec, panel)

        edge = api.EDGES[settings.image_size]
        width, height = size
        inner_width = max(1, round(width * edge / max(size)))
        inner_height = max(1, round(height * edge / max(size)))
        left, top = (edge - inner_width) // 2, (edge - inner_height) // 2
        inner_box = (left, top, left + inner_width, top + inner_height)
        canvas = Image.new("RGB", (edge, edge), "white")
        canvas.paste(original.resize((inner_width, inner_height), Image.Resampling.LANCZOS), (left, top))

        def scaled(box, inward):
            l, t, r, b = box
            # Integer arithmetic avoids rounding an editable boundary outward.
            if inward:
                return (left + (l * inner_width + width - 1) // width,
                        top + (t * inner_height + height - 1) // height,
                        left + r * inner_width // width, top + b * inner_height // height)
            return (left + l * inner_width // width, top + t * inner_height // height,
                    left + (r * inner_width + width - 1) // width,
                    top + (b * inner_height + height - 1) // height)

        working_mask = Image.new("RGBA", (edge, edge), (0, 0, 0, 255))
        working_alpha = working_mask.getchannel("A")
        draw = ImageDraw.Draw(working_alpha)
        _draw(draw, scaled(selected, True), 0)
        for box in protected:
            _draw(draw, scaled(box, False), 255)
        if working_alpha.getextrema()[0] == 255:
            raise ValueError("Selection is empty at working resolution")
        working_mask.putalpha(working_alpha)
        prompt = (f"Request coordinates: image 1 is a {edge}x{edge} padded working canvas. "
                  f"The original {width}x{height} flat artwork occupies the half-open rectangle "
                  f"{list(inner_box)}. Brief bounds, period and phase refer to the original flat "
                  f"artwork; map x by {inner_width}/{width} plus {left}, and y by "
                  f"{inner_height}/{height} plus {top}. Return the complete working square; "
                  "keep its white exterior padding. The mask already uses working coordinates.\n"
                  + prompt)
        context, _ = _read(mockup)
        context.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        # The mask attaches to the first image; image 2 is evidence only.
        # https://developers.openai.com/api/reference/resources/images/methods/edit
        payload = {
            "model": settings.model, "prompt": prompt, "n": 1, "size": f"{edge}x{edge}",
            "quality": settings.quality, "output_format": "png",
            "images": [_reference(canvas), _reference(context)], "mask": _reference(working_mask),
        }
        if len(prompt) > 32000:
            raise ValueError("Repair prompt exceeds API limit")
        spec_hash = design_spec_hash(repair_spec)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    except (OSError, TypeError, ValueError, KeyError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise api.OpenAIError("Invalid repair inputs, selection, mask or output path; no request sent.") from None

    # Exactly one call. The existing request helper sanitizes provider failures.
    result = api._request(settings, payload)
    try:
        if type(result) is not dict or type(result.get("data")) is not list or len(result["data"]) != 1:
            raise ValueError
        encoded = result["data"][0]["b64_json"]
        if type(encoded) is not str or len(encoded) > api.MAX_RESPONSE_BYTES:
            raise ValueError
        raw = base64.b64decode(encoded, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as generated:
                if (generated.format != "PNG" or generated.size != (edge, edge)
                        or getattr(generated, "n_frames", 1) != 1):
                    raise ValueError
                generated.load()
                edited = generated.convert("RGB").crop(inner_box).resize(size, Image.Resampling.LANCZOS)
        output = apply_patch_mask(repair_spec, panel, original, edited, local_mask)
        # An actual diff, before PNG encoding, proves the local pixel boundary.
        channels = ImageChops.difference(original, output).split()
        changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        protected_diff = ImageChops.multiply(changed, alpha)
        locked_changed_pixels = size[0] * size[1] - protected_diff.histogram()[0]
        if locked_changed_pixels != 0:
            raise ValueError("Pixel lock verification failed")
        changed_pixels = size[0] * size[1] - changed.histogram()[0]
    except (OSError, TypeError, ValueError, KeyError, IndexError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise api.OpenAIError("Invalid repair response or pixel-lock failure; destination preserved; no retry.") from None
    try:
        api._atomic_write(destination, lambda stream: output.save(
            stream, "PNG", **({"dpi": dpi} if dpi is not None else {})))
    except (OSError, ValueError, TypeError):
        raise api.OpenAIError("Could not save repaired artwork; no retry.") from None
    return {
        "provider": "openai", "model": settings.model, "panel": panel,
        "workflow": "masked-repair-v1", "image_size": settings.image_size, "quality": settings.quality,
        "artwork_dimensions": list(size), "raw_dimensions": [edge, edge],
        "editable_box": list(editable_box), "protected_boxes": [list(box) for box in protected_boxes],
        "prompt_hash": prompt_hash, "spec_hash": spec_hash, "input_spec_hash": input_spec_hash,
        "pixel_lock_verified": True, "locked_changed_pixels": locked_changed_pixels,
        "changed_pixels": changed_pixels, "paid_calls": 1, "manual_review_required": True,
        "quality_status": "awaiting_review", "estimated_image_cost_usd": None,
        "seconds": round(time.monotonic() - started, 3),
    }
