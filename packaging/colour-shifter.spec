# -*- mode: python ; coding: utf-8 -*-
from importlib.metadata import distributions
from pathlib import Path
import sys

from PyInstaller.building.utils import format_binaries_and_datas
from PyInstaller.utils.hooks import collect_dynamic_libs

root = Path(SPECPATH).parent
runtime = Path(sys.base_prefix)
notices = [
    (str(root / "packaging" / "BiRefNet-LICENSE.txt"), "licenses/BiRefNet"),
    (str(runtime / "LICENSE.txt"), "licenses/Python"),
]
for distribution in distributions():
    for relative in distribution.files or ():
        # Preserve the wheel's copyright and third-party notices, including
        # notices for native libraries bundled inside a wheel.
        parts = [part.lower() for part in relative.parts]
        if relative.name.lower().startswith(("license", "copying", "notice", "thirdpartynotice")) or "licenses" in parts:
            path = distribution.locate_file(relative)
            if path.is_file():
                destination = Path("licenses", distribution.metadata["Name"])
                if "licenses" in parts:
                    destination /= Path(*relative.parts[parts.index("licenses") + 1:-1])
                notices.append((str(path), str(destination)))

onnx_dlls = collect_dynamic_libs("onnxruntime")
data_files = notices + [(str(root / "models" / "birefnet-lite-512.onnx"), "models")]
desktop = Analysis(
    [str(root / "packaging" / "desktop.py")],
    pathex=[str(root)],
    binaries=onnx_dlls,
)
server = Analysis(
    [str(root / "packaging" / "mcp_entry.py")],
    pathex=[str(root)],
    binaries=onnx_dlls,
)

gui_exe = EXE(
    PYZ(desktop.pure), desktop.scripts, [],
    exclude_binaries=True, name="Colour Shifter", console=False, upx=False,
)
mcp_exe = EXE(
    PYZ(server.pure), server.scripts, [],
    exclude_binaries=True, name="Colour Shifter MCP", console=True, upx=False,
)

bundle = COLLECT(
    gui_exe, mcp_exe,
    desktop.binaries, server.binaries,
    desktop.datas, server.datas,
    [(destination, path, "DATA") for destination, path in sorted(format_binaries_and_datas(data_files))],
    name="Colour Shifter", upx=False,
)
