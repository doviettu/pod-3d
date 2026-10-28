"""Local panel/contact-edge evidence, not physical assembly or seam validation."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

from PIL import Image, ImageDraw

from garment_preview import HEX_ID, PANEL_NAME, _inside, _recorded


MAX_PIXELS = 50_000_000
MAX_BYTES = 256 * 1024 * 1024
MAX_PANELS = 64
PER_PAGE = 6
TILE = (420, 500)
LABEL = "canvas edges, not verified sew lines"
MANIFEST = re.compile(r"previews/assembly-[0-9a-f]{32}\.json")


def _required(engine, job_id, job):
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid job ID.")
    folder = _inside(Path(engine.root).resolve(), job_id)
    panels = engine.templates.load(job["template"]).panels
    sizes, revisions = {}, {}
    for panel in panels:
        name, size = panel.name, tuple(panel.size)
        if (not isinstance(name, str) or not PANEL_NAME.fullmatch(name)
                or name in sizes or len(size) != 2
                or any(type(n) is not int or n <= 0 for n in size)
                or math.prod(size) > MAX_PIXELS):
            raise ValueError("Invalid template panel.")
        saved = job.get("reviews", {}).get(name)
        revision = saved.get("revision") if isinstance(saved, dict) else None
        if saved is not None and (not isinstance(revision, str) or not HEX_ID.fullmatch(revision)):
            raise ValueError("Invalid saved review revision.")
        sizes[name] = size
        revisions[name] = revision
    if not sizes or len(sizes) > MAX_PANELS:
        raise ValueError("Invalid template panel count.")
    return folder, sizes, revisions


def _allowed(engine, job_id, job, folder, relative):
    path = _inside(folder, relative)
    urls = {a.get("url") for a in job.get("artifacts", []) if isinstance(a, dict)}
    if engine.url(job_id, path) not in urls:
        return None
    return _recorded(engine, job_id, folder, relative)


def _select(engine, job_id, job, folder, name, revision):
    if revision is not None:
        spec_path = _inside(folder, f"reviews/{revision}/spec.json")
        try:
            with spec_path.open("rb") as stream:
                raw = stream.read(65_537)
            spec = json.loads(raw) if len(raw) <= 65_536 else None
        except (OSError, ValueError):
            spec = None
        if isinstance(spec, dict) and type(spec.get("approved")) is bool:
            suffix = "approved" if spec["approved"] else "draft"
            relative = f"reviews/{revision}/{name}-{suffix}.png"
            path = _allowed(engine, job_id, job, folder, relative)
            if path is not None:
                approved = spec["approved"] and job["reviews"][name].get("approved") is True
                return path, relative, approved
    relative = f"local/{name}.png"
    path = _allowed(engine, job_id, job, folder, relative)
    if path is None:
        raise ValueError(f"Missing allowlisted panel: {name}")
    return path, relative, False


def _hash(stream):
    digest, count = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        count += len(block)
        if count > MAX_BYTES:
            raise ValueError("Panel exceeds file-size limit.")
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def _panel(path, expected, draw=None):
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        digest = _hash(stream)
        with Image.open(stream) as image:
            if (image.format not in ("JPEG", "PNG", "WEBP")
                    or getattr(image, "n_frames", 1) != 1
                    or image.size != expected or math.prod(image.size) > MAX_PIXELS):
                raise ValueError("Panel format or dimensions do not match the template.")
            if draw is not None:
                image.load()
                draw(image)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Panel changed while building proof.")
    return {"sha256": digest, "size": list(expected)}


def _tile(page, origin, name, size, approved, image):
    x, y = origin
    draw = ImageDraw.Draw(page)
    draw.text((x + 12, y + 10), name, fill="black")
    draw.text((x + 12, y + 28), f"{size[0]} x {size[1]} px | "
              + ("saved approval" if approved else "review required"), fill="black")
    width, height = image.size
    dx, dy = max(1, round(width * .03)), max(1, round(height * .03))
    strips = [
        ("Left", (0, 0, dx, height), (x + 12, y + 74), (24, 280), (x + 10, y + 54)),
        ("Right", (width - dx, 0, width, height), (x + 384, y + 74), (24, 280), (x + 376, y + 54)),
        ("Top", (0, 0, width, dy), (x + 12, y + 386), (396, 24), (x + 12, y + 366)),
        ("Bottom", (0, height - dy, width, height), (x + 12, y + 444), (396, 24), (x + 12, y + 424)),
    ]
    for label, box, position, target, label_position in strips:
        draw.text(label_position, label, fill="black")
        with image.crop(box) as strip:
            with strip.resize(target, Image.Resampling.LANCZOS) as resized:
                page.paste(resized, position)
    # Keep only one decoded full-resolution panel; reduce it before RGB conversion.
    image.thumbnail((324, 280), Image.Resampling.LANCZOS)
    with image.convert("RGB") as thumbnail:
        page.paste(thumbnail, (x + 48 + (324 - thumbnail.width) // 2,
                               y + 74 + (280 - thumbnail.height) // 2))


def create_assembly_proof(engine, job_id) -> dict:
    """Caller holds the serial postprocess lock; create evidence without changing reviews.

    Return JSON-safe metadata and an allowlisted manifest. Required names without
    a saved review have explicit None revisions. No job fields are persisted here.
    """
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid job ID.")
    job = engine.get_job(job_id)
    folder, sizes, revisions = _required(engine, job_id, job)
    selected = {name: _select(engine, job_id, job, folder, name, revisions[name])
                for name in sizes}
    names = list(sizes)
    token = uuid.uuid4().hex
    manifest_relative = f"previews/assembly-{token}.json"
    result = {"kind": "six-panel-proof", "job_id": job_id, "template": job["template"],
              "panels": names, "missing_approvals": [n for n in names if not selected[n][2]],
              "dimensions": {n: list(s) for n, s in sizes.items()},
              "review_revisions": revisions, "sources": {}, "images": [],
              "manifest": manifest_relative, "label": LABEL}
    previews = _inside(folder, "previews")
    previews.mkdir(exist_ok=True)
    published = []
    try:
        with tempfile.TemporaryDirectory(prefix=".assembly-", dir=previews) as temporary:
            staging = Path(temporary)
            for offset in range(0, len(names), PER_PAGE):
                batch = names[offset:offset + PER_PAGE]
                columns = min(3, len(batch))
                page_size = (max(840, columns * TILE[0]), math.ceil(len(batch) / columns) * TILE[1] + 64)
                with Image.new("RGB", page_size, "white") as page:
                    draw = ImageDraw.Draw(page)
                    draw.text((12, 10), "ALL TEMPLATE PANELS | LOCAL ASSEMBLY PROOF", fill="black")
                    draw.text((12, 30), LABEL, fill="black")
                    for index, name in enumerate(batch):
                        path, relative, approved = selected[name]
                        origin = ((index % columns) * TILE[0], 64 + (index // columns) * TILE[1])
                        info = _panel(path, sizes[name],
                                      lambda image: _tile(page, origin, name, sizes[name], approved, image))
                        result["sources"][name] = {"path": relative, **info}
                    relative = f"previews/assembly-{token}-{offset // PER_PAGE + 1}.jpg"
                    output = staging / Path(relative).name
                    with output.open("xb") as stream:
                        page.save(stream, format="JPEG", quality=90, subsampling=0)
                    with output.open("rb") as stream:
                        digest = _hash(stream)
                    result["images"].append({"path": relative, "sha256": digest,
                                             "size": list(page_size),
                                             "url": engine.url(job_id, folder / relative)})
            result["url"] = result["images"][0]["url"]
            manifest = staging / Path(manifest_relative).name
            manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            for relative in [item["path"] for item in result["images"]] + [manifest_relative]:
                target = _inside(folder, relative)
                if target.exists() or target.is_symlink():
                    raise ValueError("Proof destination already exists.")
                (staging / target.name).rename(target)
                published.append(target)
        for path in published:
            engine.add_artifact(job_id, path, "json" if path.suffix == ".json" else "image")
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return result


def proof_is_current(engine, job, proof) -> bool:
    """Check persisted, allowlisted evidence against current required reviews and bytes.

    A current proof may contain drafts; callers must separately enforce approval.
    """
    try:
        if not isinstance(proof, dict) or proof.get("kind") != "six-panel-proof":
            return False
        job_id = job["id"]
        current = engine.get_job(job_id)
        folder, sizes, revisions = _required(engine, job_id, current)
        if (proof.get("job_id") != job_id or proof.get("template") != current["template"]
                or proof.get("panels") != list(sizes)
                or proof.get("review_revisions") != revisions
                or proof.get("dimensions") != {n: list(s) for n, s in sizes.items()}
                or set(proof.get("sources", {})) != set(sizes)):
            return False
        relative = proof.get("manifest")
        if not isinstance(relative, str) or not MANIFEST.fullmatch(relative):
            return False
        manifest = _allowed(engine, job_id, current, folder, relative)
        if manifest is None:
            return False
        with manifest.open("rb") as stream:
            raw = stream.read(262_145)
        if len(raw) > 262_144 or json.loads(raw) != proof:
            return False
        missing = []
        for name, size in sizes.items():
            path, source, approved = _select(engine, job_id, current, folder, name, revisions[name])
            if not approved:
                missing.append(name)
            if proof["sources"][name] != {"path": source, **_panel(path, size)}:
                return False
        if proof.get("missing_approvals") != missing or proof.get("label") != LABEL:
            return False
        images = proof.get("images")
        if not isinstance(images, list) or len(images) != math.ceil(len(sizes) / PER_PAGE):
            return False
        prefix = relative[:-5]
        for index, item in enumerate(images, 1):
            image_relative = f"{prefix}-{index}.jpg"
            path = _allowed(engine, job_id, current, folder, image_relative)
            if (path is None or item["path"] != image_relative
                    or item["url"] != engine.url(job_id, path)
                    or _panel(path, tuple(item["size"])) !=
                    {"size": item["size"], "sha256": item["sha256"]}):
                return False
        return proof.get("url") == images[0]["url"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, Image.DecompressionBombError):
        return False
