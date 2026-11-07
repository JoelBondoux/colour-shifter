"""GPU foreground selection and detail-preserving colour transfer."""

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
import sys
from tempfile import mkdtemp

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageChops

from .images import open_image
from .material import detect_colour, select_material
from .model import MODEL_PATH, read_verified_model
from .reflections import Reflections, reflection_cast
from .swatches import RGB


MODEL_SIZE = (512, 512)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MASK_CURVE = np.rint(255 * np.clip((np.arange(256) - 26) / 203, 0, 1)).tolist()


class Segmenter:
    def __init__(self, *, cpu: bool = False) -> None:
        if not MODEL_PATH.is_file():
            if getattr(sys, "frozen", False):
                raise FileNotFoundError("The bundled AI model is missing. Extract the complete release ZIP again.")
            raise FileNotFoundError("The AI model is missing. Run Setup.cmd first.")

        provider = "CPUExecutionProvider" if cpu else "DmlExecutionProvider"
        if provider not in ort.get_available_providers():
            raise RuntimeError("DirectML is unavailable. Select CPU or check the graphics driver and installation.")

        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Parse the bytes that were verified and keep ONNX external data paths disabled.
        self.session = ort.InferenceSession(read_verified_model(), sess_options=options, providers=[provider])
        if self.session.get_providers()[0] != provider:
            raise RuntimeError("Could not start DirectML. Check the graphics driver or select CPU.")
        self.session.disable_fallback()

    def foreground(self, image: Image.Image) -> Image.Image:
        thumbnail = image.resize(MODEL_SIZE, Image.Resampling.BILINEAR)
        alpha = image.getchannel("A")
        rgb = Image.new("RGB", MODEL_SIZE, (235, 235, 235))
        rgb.paste(thumbnail, mask=thumbnail.getchannel("A"))
        pixels = np.asarray(rgb, dtype=np.float32) / 255
        tensor = np.ascontiguousarray(((pixels - MEAN) / STD).transpose(2, 0, 1)[None])
        logits = self.session.run(["output_image"], {"input_image": tensor})[0][0, 0]
        confidence = 1 / (1 + np.exp(-np.clip(logits, -30, 30)))
        mask = Image.fromarray(np.rint(confidence * 255).astype(np.uint8))
        mask = mask.point(MASK_CURVE).resize(image.size, Image.Resampling.BILINEAR)
        if alpha.getextrema()[0] < 255:
            mask = ImageChops.multiply(mask, alpha.point([0] + [255] * 255))
        return mask


def reference_colour(image: Image.Image, mask: Image.Image) -> RGB:
    return detect_colour(image, mask)


def sample_colour(image: Image.Image, x: int, y: int) -> RGB:
    patch = np.asarray(image.crop((max(0, x - 2), max(0, y - 2), min(image.width, x + 3), min(image.height, y + 3))))
    opaque = patch[patch[:, :, 3] >= 128, :3]
    if not opaque.size:
        raise ValueError("Click a solid part of the product.")
    return tuple(np.rint(np.median(opaque, axis=0)).astype(int).tolist())


def apply_colour(
    image: Image.Image, mask: Image.Image, target: RGB, *, reflections: Reflections | None = None,
) -> Image.Image:
    return next(colour_variants(image, mask, (target,), reflections=reflections))


def colour_variants(
    image: Image.Image, mask: Image.Image, targets: Iterable[RGB], *, reflections: Reflections | None = None,
) -> Iterator[Image.Image]:
    rgb = image.convert("RGB")
    luminance = rgb.convert("L")
    alpha = image.getchannel("A")
    solid = mask.point([0] * 128 + [255] * 128)
    histogram = np.cumsum(luminance.histogram(solid))
    if histogram[-1] == 0:
        raise ValueError("No matching product colour found. Clear the original-colour sample or pick another.")

    # Anchor the chosen swatch at the median of the selected material and
    # keep its local light/dark variations on either side of that median.
    middle = int(np.searchsorted(histogram, (histogram[-1] + 1) // 2))
    delta = np.arange(256) - middle
    shadow = np.maximum(0, -delta / max(middle, 48))
    light = np.maximum(0, delta / max(255 - middle, 48))
    for target in targets:
        # Leave some tonal range on both sides of pure black and white.
        if max(target) < 12:
            target = tuple(channel + 12 - max(target) for channel in target)
        if min(target) > 243:
            target = tuple(channel - min(target) + 243 for channel in target)
        channels = [
            luminance.point(np.rint(channel * (1 - shadow) + (255 - channel) * light).tolist())
            for channel in target
        ]
        result = Image.composite(Image.merge("RGB", channels), rgb, mask)
        result.putalpha(alpha)
        if reflections is not None:
            reflections.apply(result, target)
        yield result


def prepare_recolour(
    segmenter: Segmenter, image: Image.Image, source: RGB | None,
) -> tuple[RGB, Image.Image, Reflections | None]:
    colour, mask = select_material(image, segmenter.foreground(image), source)
    return colour, mask, reflection_cast(image, mask, colour)


def recolour(segmenter: Segmenter, image: Image.Image, target: RGB, source: RGB | None) -> Image.Image:
    _, mask, reflections = prepare_recolour(segmenter, image, source)
    return apply_colour(image, mask, target, reflections=reflections)


def save_png(result: Image.Image, output: Path) -> None:
    temporary = output.with_suffix(".part")
    owns_temporary = False
    try:
        with temporary.open("xb") as stream:
            owns_temporary = True
            result.save(stream, format="PNG", compress_level=3)
        temporary.rename(output)
        owns_temporary = False
    finally:
        if owns_temporary:
            temporary.unlink(missing_ok=True)


def export_batch(
    segmenter: Segmenter,
    paths: Sequence[Path],
    destination: Path,
    target: RGB,
    source: RGB | None,
    progress: Callable[[int, str], None],
    cancelled: Callable[[], bool],
    *,
    overrides: Mapping[Path, RGB] | None = None,
) -> tuple[Path, int, list[str]]:
    prefix = datetime.now().strftime("colour-shifter-%Y%m%d-%H%M%S-")
    run_folder = Path(mkdtemp(prefix=prefix, dir=destination))
    successes = 0
    failures = []
    for index, path in enumerate(paths, start=1):
        if cancelled():
            break
        progress(index - 1, f"Processing {index} of {len(paths)} · {path.name}")
        # A fresh run folder and numbered files also separate duplicate stems.
        output = run_folder / f"{index:04d}-{path.stem[:80]}.png"
        try:
            starting_colour = overrides.get(path, source) if overrides is not None else source
            result = recolour(segmenter, open_image(path), target, starting_colour)
            save_png(result, output)
            successes += 1
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            failures.append(f"{path.name} — {error}")
        progress(index, f"Processed {index} of {len(paths)}")

    if failures:
        with (run_folder / "errors.txt").open("x", encoding="utf-8") as report:
            report.write("\n".join(failures) + "\n")
    return run_folder, successes, failures
