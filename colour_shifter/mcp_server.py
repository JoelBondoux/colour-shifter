"""Local Colour Shifter API over MCP stdio."""

import os
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from anyio import from_thread, to_thread
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .images import folder_images, open_image
from .recolour import Segmenter, export_batch, reference_colour, sample_colour
from .swatches import Swatch, SwatchStore, hex_colour, parse_hex, remember_batch_colour


Backend = Literal["gpu", "cpu"]
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
mcp = FastMCP(
    "Colour Shifter",
    instructions=(
        "Recolour local product photos using an existing swatch, hex colour or reference photo. "
        "Use absolute Windows paths. Exports go into a new folder and the originals are preserved."
    ),
)
inference_lock = Lock()


@dataclass
class Library:
    saved: list[Swatch]
    recent_batch_colours: list[str]


@dataclass
class Sample:
    hex: str


@dataclass
class Batch:
    output_folder: str
    target_hex: str
    backend: Backend
    exported: int
    failed: int
    error_report: str | None
    warning: str | None


def absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"Use an absolute path for {value!r}.")
    return path


def directory(value: str) -> Path:
    path = absolute_path(value)
    if not path.is_dir():
        raise ValueError(f"Choose an existing folder. Could not open {value!r}.")
    return path


@lru_cache(maxsize=1)
def segmenter(backend: Backend) -> Segmenter:
    # ONNX Runtime may print provider startup diagnostics.
    with redirect_stdout(sys.stderr):
        return Segmenter(cpu=backend == "cpu")


@mcp.tool(annotations=READ_ONLY)
def list_swatches() -> Library:
    """List the saved swatch names and hex values, plus recent successful batch colours."""
    return Library(*SwatchStore().snapshot())


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
def add_swatch(
    name: Annotated[str, Field(description="Unique name, up to 64 characters.")],
    hex_colour: Annotated[str, Field(description="Six-digit hex colour. Accepts a sampled or recent batch colour.")],
) -> Swatch:
    """Save a named swatch for use in the desktop app and future MCP batches."""
    return SwatchStore().add(name, hex_colour)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False))
def rename_swatch(name: str, new_name: str) -> Swatch:
    """Rename a saved swatch while retaining its colour. The new name must be unique."""
    return SwatchStore().rename(name, new_name)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False))
def delete_swatch(name: str) -> Swatch:
    """Delete a named swatch from the shared library. Batch exports remain on disk."""
    return SwatchStore().delete(name)


@mcp.tool(annotations=READ_ONLY)
async def sample_reference(
    image_path: Annotated[str, Field(description="Absolute path to the local reference photo.")],
    x: Annotated[int | None, Field(ge=0, description="Optional pixel column, paired with y.")] = None,
    y: Annotated[int | None, Field(ge=0, description="Optional pixel row, paired with x.")] = None,
    backend: Backend = "gpu",
) -> Sample:
    """Read a hex colour from a reference photo. Omit coordinates to sample the AI-selected foreground."""
    def sample():
        image = open_image(absolute_path(image_path))
        if x is None and y is None:
            with inference_lock:
                mask = segmenter(backend).foreground(image)
            return Sample(hex_colour(reference_colour(image, mask)))
        if x is None or y is None or x >= image.width or y >= image.height:
            raise ValueError("Supply both pixel coordinates inside the image, or omit both.")
        return Sample(hex_colour(sample_colour(image, x, y)))

    return await to_thread.run_sync(sample)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
async def recolour_batch(
    output_folder: Annotated[str, Field(description="Absolute path to an existing folder for the new export folder.")],
    ctx: Context,
    image_paths: Annotated[list[str] | None, Field(description="Absolute paths of the input images.")] = None,
    input_folder: Annotated[str | None, Field(description="Top-level images in this folder. Alternative to image_paths.")] = None,
    swatch_name: Annotated[str | None, Field(description="Saved swatch to use as the target.")] = None,
    target_hex: Annotated[str | None, Field(description="Target as a six-digit hex colour.")] = None,
    reference_image: Annotated[str | None, Field(description="Absolute reference image path for automatic colour sampling.")] = None,
    original_hex: Annotated[str | None, Field(description="Optional starting colour to restrict edits within the foreground.")] = None,
    backend: Backend = "gpu",
) -> Batch:
    """Recolour photos and export full-resolution PNGs. Supply one input option and exactly one target option."""
    def run():
        if (image_paths is None) == (input_folder is None):
            raise ValueError("Provide either image_paths or input_folder.")
        paths = (
            folder_images(directory(input_folder)) if input_folder is not None
            else list(dict.fromkeys(absolute_path(path) for path in image_paths))
        )
        if not paths:
            raise ValueError("Provide at least one supported input image.")
        if sum(value is not None for value in (swatch_name, target_hex, reference_image)) != 1:
            raise ValueError("Provide exactly one of swatch_name, target_hex or reference_image.")
        destination = directory(output_folder)
        source = parse_hex(original_hex) if original_hex is not None else None
        if swatch_name is not None:
            target = parse_hex(SwatchStore().get(swatch_name).hex)
        elif target_hex is not None:
            target = parse_hex(target_hex)
        else:
            reference = open_image(absolute_path(reference_image))
            with inference_lock:
                target = reference_colour(reference, segmenter(backend).foreground(reference))

        reported = -1

        def progress(count, message):
            nonlocal reported
            if count > reported:
                from_thread.run(ctx.report_progress, count, len(paths), message)
                reported = count

        with inference_lock:
            folder, count, errors = export_batch(segmenter(backend), paths, destination, target, source, progress, lambda: False)
        return Batch(
            str(folder), hex_colour(target), backend, count, len(errors),
            str(folder / "errors.txt") if errors else None,
            remember_batch_colour(target) if count else None,
        )

    return await to_thread.run_sync(run)


def main():
    stdin, stdout = sys.stdin, sys.stdout
    # The SDK wraps the underlying pipes and can close its wrappers at exit.
    # Keep the original streams available for the frozen runtime's final flush.
    with open(os.dup(stdin.fileno()), "r", encoding="utf-8") as reader, open(
        os.dup(stdout.fileno()), "w", encoding="utf-8"
    ) as writer:
        sys.stdin, sys.stdout = reader, writer
        try:
            mcp.run(transport="stdio")
        finally:
            sys.stdin, sys.stdout = stdin, stdout


if __name__ == "__main__":
    main()
