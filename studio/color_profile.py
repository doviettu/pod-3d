"""The sRGB ICC profile embedded in every print file.

Print shops (Printful, Printify) expect sRGB. A file with no profile is usually read as sRGB too,
but some RIPs and Photoshop fall back to their own working space, which shifts colours; embedding
the profile removes the guess. Pillow builds it with LittleCMS, so no file has to ship.
"""
from functools import lru_cache

from PIL import ImageCms


@lru_cache(maxsize=1)
def srgb():
    """sRGB IEC61966-2.1 profile as bytes, ready for Image.save(..., icc_profile=...)."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def describe(icc_bytes):
    """Profile description of embedded bytes, or None when there is no profile."""
    if not icc_bytes:
        return None
    import io
    try:
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))).strip()
    except (OSError, ImageCms.PyCMSError):
        return 'không đọc được'
