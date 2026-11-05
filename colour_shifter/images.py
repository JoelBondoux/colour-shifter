"""Image I/O shared by preview and export."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageCms, ImageOps


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})


def open_image(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        if getattr(opened, "n_frames", 1) != 1:
            raise ValueError("Choose a single-frame image.")

        image = ImageOps.exif_transpose(opened)
        rgba = image.convert("RGBA")
        profile = image.info.get("icc_profile")
        if profile:
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


def folder_images(folder: Path) -> list[Path]:
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )
