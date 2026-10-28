"""Exact-byte lookup of paired sample originals; never infers artwork or approval."""
import hashlib
import itertools
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from PIL import Image


MAX_PIXELS = 50_000_000
MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 256
EXTENSIONS = {".jpg", ".jpeg", ".png"}
NAME = re.compile(r"[A-Za-z0-9_-]{1,50}")


def _template(template):
    panels = {}
    if not isinstance(template.name, str) or not template.name:
        raise ValueError("Invalid template name.")
    for panel in template.panels:
        name, size = panel.name, tuple(panel.size)
        if (not isinstance(name, str) or not NAME.fullmatch(name)
                or name in panels or len(size) != 2
                or any(type(n) is not int or n <= 0 for n in size)
                or math.prod(size) > MAX_PIXELS):
            raise ValueError("Invalid template panel.")
        panels[name] = size
    dpi = template.dpi
    if (not panels or len(panels) > MAX_ENTRIES
            or not isinstance(dpi, (int, float)) or isinstance(dpi, bool)
            or not math.isfinite(dpi) or dpi <= 0):
        raise ValueError("Invalid template panels or DPI.")
    return panels


def _open_file(path, *, dir_fd=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError("Invalid reference file.")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _sha256(handle):
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
        if handle.tell() > MAX_BYTES:
            raise ValueError("Reference file is too large.")
    handle.seek(0)
    return digest.hexdigest()


def _inspect(handle, expected=None, output=None, dpi=None):
    digest = _sha256(handle)
    with Image.open(handle) as image:
        size = image.size
        if (image.format not in ("JPEG", "PNG") or getattr(image, "n_frames", 1) != 1
                or math.prod(size) > MAX_PIXELS
                or (expected is not None and size != expected)):
            raise ValueError("Invalid reference image format or dimensions.")
        image.verify()
    handle.seek(0)
    with Image.open(handle) as image:
        image.load()
        if output is not None:
            # PNG cannot encode CMYK JPEGs; preserve all other supported pixel modes.
            if image.mode == "CMYK":
                image = image.convert("RGB")
            image.save(output, format="PNG", dpi=(dpi, dpi))
    return {"sha256": digest, "size": list(size)}


def _entries(directory):
    # shortcut: no persistent cache; rescan at most 256 entries per directory per lookup.
    with os.scandir(directory) as scan:
        entries = list(itertools.islice(scan, MAX_ENTRIES + 1))
    if len(entries) > MAX_ENTRIES:
        raise ValueError("Reference directory exceeds scan limit.")
    return sorted(entries, key=lambda entry: entry.name)


class ReferenceLibrary:
    """Read immediate sample directories only, with bounded scans and no hash cache."""

    def __init__(self, samples_root):
        root = Path(samples_root).absolute()
        if root.is_symlink():
            raise ValueError("The samples root must not be a symlink.")
        self.samples_root = root.resolve()

    def _directory(self, sample):
        if (not isinstance(sample, str) or sample in ("", ".", "..")
                or Path(sample).name != sample or "/" in sample or "\\" in sample):
            raise ValueError("Invalid sample directory.")
        root = os.open(self.samples_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return os.open(sample, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        finally:
            os.close(root)

    def _match(self, sample, source_name, template, panels, staging=None):
        directory = self._directory(sample)
        try:
            entries = _entries(directory)
            files = {e.name: e for e in entries if e.is_file(follow_symlinks=False)}
            if source_name not in files:
                raise ValueError("Missing mockup.")
            source = files[source_name]
            if (Path(source.name).suffix.lower() not in EXTENSIONS
                    or Path(source.name).stem.casefold() in {n.casefold() for n in panels}):
                raise ValueError("Invalid mockup.")
            with _open_file(source_name, dir_fd=directory) as handle:
                source_info = _inspect(handle)
            result = {"mode": "source-original", "sample": sample,
                      "template": template.name, "dpi": template.dpi,
                      "source": {"path": source_name, **source_info},
                      "panels": {}}
            for name, size in panels.items():
                candidates = [e.name for e in files.values()
                              if Path(e.name).stem == name
                              and Path(e.name).suffix.lower() in EXTENSIONS]
                if len(candidates) != 1:
                    raise ValueError("Missing or ambiguous reference panel.")
                filename = candidates[0]
                output = staging / (name + ".png") if staging is not None else None
                with _open_file(filename, dir_fd=directory) as handle:
                    info = _inspect(handle, size, output, template.dpi)
                result["panels"][name] = {"path": filename, **info}
            return result
        finally:
            os.close(directory)

    def find(self, source_path, template):
        """Return a JSON-safe exact match, or None for unknown/invalid sample images.

        Invalid template definitions raise ValueError. No output files are created.
        """
        panels = _template(template)
        try:
            with _open_file(Path(source_path)) as handle:
                source_info = _inspect(handle)
            for sample in _entries(self.samples_root):
                if not sample.is_dir(follow_symlinks=False):
                    continue
                try:
                    directory = self._directory(sample.name)
                    try:
                        filenames = [e.name for e in _entries(directory)
                                     if e.is_file(follow_symlinks=False)
                                     and Path(e.name).suffix.lower() in EXTENSIONS
                                     and Path(e.name).stem.casefold() not in
                                     {n.casefold() for n in panels}]
                        for filename in filenames:
                            try:
                                with _open_file(filename, dir_fd=directory) as handle:
                                    if _sha256(handle) != source_info["sha256"]:
                                        continue
                                match = self._match(sample.name, filename, template, panels)
                                if match["source"] == {"path": filename, **source_info}:
                                    return match
                            except (OSError, ValueError, Image.DecompressionBombError):
                                continue
                    finally:
                        os.close(directory)
                except (OSError, ValueError):
                    continue
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        return None

    def restore(self, match, destination, template):
        """Revalidate a match and publish a new PNG directory at template.dpi.

        Returns JSON-safe provenance including original and restored SHA256 hashes.
        Raises ValueError/OSError on invalid, changed, or unsafe inputs. Destination
        must not exist; failed validation leaves it and all originals untouched.
        """
        panels = _template(template)
        if (not isinstance(match, dict) or not isinstance(match.get("source"), dict)
                or not isinstance(match["source"].get("path"), str)):
            raise ValueError("Invalid reference match.")
        destination = Path(destination).absolute()
        parent = destination.parent.resolve(strict=True)
        target = parent / destination.name
        if target.exists() or target.is_symlink():
            raise ValueError("Destination must be new.")
        if target.is_relative_to(self.samples_root):
            raise ValueError("Destination must be outside the sample library.")
        with tempfile.TemporaryDirectory(prefix=".reference-", dir=parent) as temporary:
            staging = Path(temporary) / "panels"
            staging.mkdir()
            current = self._match(match.get("sample"), match.get("source", {}).get("path"),
                                  template, panels, staging)
            if current != match:
                raise ValueError("Reference files or template changed since lookup.")
            for name, info in current["panels"].items():
                output = staging / (name + ".png")
                with _open_file(output) as handle:
                    restored = _inspect(handle, panels[name])
                info["output"] = name + ".png"
                info["output_sha256"] = restored["sha256"]
            # A new directory prevents overwriting existing panel files.
            if target.exists() or target.is_symlink():
                raise ValueError("Destination must be new.")
            staging.rename(target)
        return current
