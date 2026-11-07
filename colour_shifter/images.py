"""Image I/O shared by preview and export."""

import os
import stat
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageCms, ImageOps


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})
IMAGE_FORMATS = ("PNG", "JPEG", "WEBP", "BMP", "TIFF")
MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_PROFILE_BYTES = 4 * 1024 * 1024


def open_image(path: Path, *, max_pixels: int | None = None) -> Image.Image:
    limit = MAX_IMAGE_PIXELS if max_pixels is None else max_pixels
    with path.open("rb") as stream, Image.open(_checked_stream(stream), formats=IMAGE_FORMATS) as opened:
        if opened.width * opened.height > limit:
            raise ValueError(f"Choose an image with no more than {limit:,} pixels.")
        if getattr(opened, "is_animated", False):
            raise ValueError("Choose a single-frame image.")

        image = ImageOps.exif_transpose(opened)
        rgba = image.convert("RGBA")
        profile = image.info.get("icc_profile")
        if profile:
            if len(profile) > MAX_PROFILE_BYTES:
                raise ValueError("The embedded colour profile exceeds 4 MiB.")
            # Preserve the appearance of tagged images when exporting in sRGB.
            try:
                source = ImageCms.getOpenProfile(BytesIO(profile))
                cms_image = image if image.mode in {"RGB", "CMYK", "L", "LAB"} else rgba.convert("RGB")
                converted = ImageCms.profileToProfile(
                    cms_image, source, ImageCms.createProfile("sRGB"), outputMode="RGB"
                )
                converted.putalpha(rgba.getchannel("A"))
                rgba = converted
            except (ImageCms.PyCMSError, OSError, ValueError) as error:
                raise ValueError("Could not convert the embedded colour profile to sRGB.") from error

        # Do not carry across stale ICC, orientation or other source metadata.
        rgba.info.clear()
        return rgba


def _checked_stream(stream):
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
        raise ValueError("Choose an image file no larger than 100 MiB.")
    return stream


def folder_images(folder: Path) -> list[Path]:
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )
