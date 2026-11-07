"""Local Colour Shifter API over MCP stdio."""

import os
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

from anyio import Lock, from_thread, get_cancelled_exc_class, to_thread
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .catalogue import export_catalogue
from .images import open_image
from .mcp_paths import MAX_BATCH_IMAGES, MAX_CATALOGUE_IMAGES, McpWorkspace
from .recolour import Segmenter, export_batch, reference_colour, sample_colour
from .swatches import Swatch, SwatchStore, hex_colour, parse_hex, remember_batch_colour


Backend = Literal["gpu", "cpu"]
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
mcp = FastMCP(
    "Colour Shifter",
    instructions=(
        "Recolour local product photos using an existing swatch, hex colour or reference photo. "
        "Use absolute paths inside the configured Colour Shifter MCP workspace. "
        "Exports go into a new folder and the originals are preserved."
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


@dataclass
class Catalogue:
    output_folder: str
    manifest: str
    palette: list[Swatch]
    photos: int
    completed: int
    variants: int
    failed: int


def batch_callbacks(ctx: Context):
    cancelled_error = get_cancelled_exc_class()
    reported = -1

    def cancelled():
        try:
            from_thread.check_cancelled()
        except cancelled_error:
            return True
        return False

    def progress(count, total, message):
        nonlocal reported
        if count > reported and not cancelled():
            try:
                from_thread.run(ctx.report_progress, count, total, message)
            except cancelled_error:
                return
            reported = count

    return progress, cancelled


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
    """Read a hex colour from a reference photo. Omit coordinates to discover the main material colour."""
    def sample():
        image = open_image(McpWorkspace().image(image_path))
        if x is None and y is None:
            mask = segmenter(backend).foreground(image)
            return Sample(hex_colour(reference_colour(image, mask)))
        if x is None or y is None or x >= image.width or y >= image.height:
            raise ValueError("Supply both pixel coordinates inside the image, or omit both.")
        return Sample(hex_colour(sample_colour(image, x, y)))

    async with inference_lock:
        return await to_thread.run_sync(sample)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
async def recolour_batch(
    output_folder: Annotated[str, Field(description="Absolute path to an existing folder for the new export folder.")],
    ctx: Context,
    image_paths: Annotated[list[str] | None, Field(
        max_length=MAX_BATCH_IMAGES, description="Absolute paths of images inside the configured workspace."
    )] = None,
    input_folder: Annotated[str | None, Field(description="Top-level images in this folder. Alternative to image_paths.")] = None,
    swatch_name: Annotated[str | None, Field(description="Saved swatch to use as the target.")] = None,
    target_hex: Annotated[str | None, Field(description="Target as a six-digit hex colour.")] = None,
    reference_image: Annotated[str | None, Field(description="Absolute reference image path for automatic colour sampling.")] = None,
    original_hex: Annotated[str | None, Field(description="Optional starting colour override. Otherwise each photo is analysed automatically.")] = None,
    backend: Backend = "gpu",
) -> Batch:
    """Recolour photos and export full-resolution PNGs. Supply one input option and exactly one target option."""
    report, cancelled = batch_callbacks(ctx)

    def run():
        workspace = McpWorkspace()
        if (image_paths is None) == (input_folder is None):
            raise ValueError("Provide either image_paths or input_folder.")
        paths = (
            workspace.images_in(input_folder) if input_folder is not None
            else list(dict.fromkeys(workspace.image(path, allow_missing=True) for path in image_paths))
        )
        if not paths:
            raise ValueError("Provide at least one supported input image.")
        if sum(value is not None for value in (swatch_name, target_hex, reference_image)) != 1:
            raise ValueError("Provide exactly one of swatch_name, target_hex or reference_image.")
        destination = workspace.directory(output_folder)
        source = parse_hex(original_hex) if original_hex is not None else None
        if swatch_name is not None:
            target = parse_hex(SwatchStore().get(swatch_name).hex)
        elif target_hex is not None:
            target = parse_hex(target_hex)
        else:
            reference = open_image(workspace.image(reference_image))
            target = reference_colour(reference, segmenter(backend).foreground(reference))

        def progress(count, message):
            report(count, len(paths), message)

        folder, count, errors = export_batch(segmenter(backend), paths, destination, target, source, progress, cancelled)
        return Batch(
            str(folder), hex_colour(target), backend, count, len(errors),
            str(folder / "errors.txt") if errors else None,
            remember_batch_colour(target) if count else None,
        )

    async with inference_lock:
        return await to_thread.run_sync(run)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
async def export_colourways(
    input_folder: Annotated[str, Field(description="Absolute path to the catalogue root. Includes subfolders.")],
    reference_folder: Annotated[str, Field(description="Absolute path to the folder containing the five reference colours.")],
    output_folder: Annotated[str, Field(description="Existing output folder outside the input tree.")],
    ctx: Context,
    suffix: bool = True,
    backend: Backend = "gpu",
    only_photos: Annotated[list[str] | None, Field(
        max_length=MAX_CATALOGUE_IMAGES, description="Optional absolute paths of specific photos to export or retry."
    )] = None,
    starting_colours: Annotated[dict[str, str] | None, Field(
        max_length=MAX_CATALOGUE_IMAGES, description="Optional map from absolute photo paths to starting hex colours."
    )] = None,
) -> Catalogue:
    """Discover five colours, then export each photo once per colour with a mirrored folder tree."""
    progress, cancelled = batch_callbacks(ctx)

    def run():
        workspace = McpWorkspace()
        root = workspace.directory(input_folder)
        references = workspace.directory(reference_folder)
        destination = workspace.directory(output_folder)
        selected = {workspace.image(path) for path in only_photos} if only_photos is not None else None
        overrides = {workspace.image(path): parse_hex(colour) for path, colour in (starting_colours or {}).items()}
        result = export_catalogue(
            segmenter(backend), root, references, destination, suffix=suffix,
            progress=progress, cancelled=cancelled,
            photos=workspace.catalogue_images(input_folder),
            reference_paths=workspace.images_in(reference_folder),
            only_photos=selected, overrides=overrides,
        )
        return Catalogue(
            str(result.folder), str(result.folder / "manifest.json"),
            [Swatch(swatch.name, hex_colour(swatch.rgb)) for swatch in result.palette],
            result.photos, result.completed, result.variants, result.failed,
        )

    async with inference_lock:
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
