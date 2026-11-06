"""Limit MCP file operations to an explicitly configured local directory."""

import ctypes
import ntpath
import os
import stat
from pathlib import Path, PureWindowsPath

from .images import IMAGE_EXTENSIONS


MAX_BATCH_IMAGES = 250


def _local_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or len(path.drive) != 2 or not path.drive[0].isascii() or not path.drive[0].isalpha():
        raise ValueError("Use an absolute path on a local Windows drive.")
    for part in path.parts[1:]:
        reserved = ntpath.isreserved(part) if hasattr(ntpath, "isreserved") else PureWindowsPath(part).is_reserved()
        if part == ".." or part.endswith((".", " ")) or ":" in part or "\x00" in part or reserved:
            raise ValueError("The path contains a reserved name, stream or parent-directory component.")
    return path


def _metadata(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("MCP paths cannot pass through links or Windows reparse points.")
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise ValueError("Copy hard-linked images into the MCP workspace as regular files.")
    return info


class McpWorkspace:
    def __init__(self):
        value = os.environ.get("COLOUR_SHIFTER_MCP_ROOT")
        if not value:
            raise ValueError("Set COLOUR_SHIFTER_MCP_ROOT to a dedicated local image and export folder.")
        self.root = _local_path(value)
        # DRIVE_FIXED excludes mapped network drives before any filesystem access.
        if self.root == Path(self.root.anchor) or ctypes.windll.kernel32.GetDriveTypeW(self.root.anchor) != 3:
            raise ValueError("Configure a folder on a fixed local drive as the MCP workspace.")
        path = Path(self.root.anchor)
        for part in self.root.parts[1:]:
            path = path / part
            if not stat.S_ISDIR(_metadata(path).st_mode):
                raise ValueError("The configured MCP workspace must be an existing directory.")

    def _checked(self, value: str, *, directory: bool, allow_missing: bool = False) -> Path:
        try:
            relative = _local_path(value).relative_to(self.root)
        except ValueError as error:
            raise ValueError(f"Use a normal absolute path inside the MCP workspace ({self.root}).") from error

        path = self.root
        for index, part in enumerate(relative.parts, start=1):
            path = path / part
            last = index == len(relative.parts)
            try:
                mode = _metadata(path).st_mode
            except FileNotFoundError:
                if last and allow_missing:
                    return path
                raise ValueError(f"Could not find {path.name!r} in the MCP workspace.") from None
            valid = stat.S_ISDIR(mode) if not last or directory else stat.S_ISREG(mode)
            if not valid:
                raise ValueError(f"Expected {'a folder' if not last or directory else 'an image file'} at {path.name!r}.")
        return path

    def directory(self, value: str) -> Path:
        return self._checked(value, directory=True)

    def image(self, value: str, *, allow_missing: bool = False) -> Path:
        if Path(value).suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Choose a PNG, JPEG, WebP, BMP or TIFF image.")
        return self._checked(value, directory=False, allow_missing=allow_missing)

    def images_in(self, value: str) -> list[Path]:
        paths = []
        for entry in self.directory(value).iterdir():
            if entry.suffix.lower() in IMAGE_EXTENSIONS and not stat.S_ISDIR(_metadata(entry).st_mode):
                paths.append(self.image(str(entry)))
                if len(paths) > MAX_BATCH_IMAGES:
                    raise ValueError(f"Choose at most {MAX_BATCH_IMAGES} images per MCP batch.")
        return sorted(paths, key=lambda path: path.name.casefold())
