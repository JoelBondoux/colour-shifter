"""GPU foreground selection and detail-preserving colour transfer."""

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageChops

from .images import open_image
from .model import MODEL_PATH


RGB = tuple[int, int, int]
MODEL_SIZE = (512, 512)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MASK_CURVE = np.rint(255 * np.clip((np.arange(256) - 26) / 203, 0, 1)).tolist()


class Segmenter:
    def __init__(self, *, cpu: bool = False) -> None:
        if not MODEL_PATH.is_file():
            raise FileNotFoundError("The AI model is missing. Run Setup.cmd first.")

        provider = "CPUExecutionProvider" if cpu else "DmlExecutionProvider"
        if provider not in ort.get_available_providers():
            raise RuntimeError("DirectML is unavailable. Run Setup.cmd to install the GPU runtime.")

        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(MODEL_PATH), sess_options=options, providers=[provider])
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
        if mask.getextrema()[1] < 128:
            raise ValueError("The AI could not find a clear foreground product.")
        return mask


def reference_colour(image: Image.Image, mask: Image.Image) -> RGB:
    # Use the median of confidently selected pixels. A click can override this
    # on a multicoloured reference or when a particular shade is needed.
    size = image.size
    scale = min(1.0, 512 / max(size))
    size = tuple(max(1, round(value * scale)) for value in size)
    preview = np.asarray(image.resize(size, Image.Resampling.BOX))
    selection = np.asarray(mask.resize(size, Image.Resampling.BOX))
    selected = preview[(selection >= 160) & (preview[:, :, 3] >= 128), :3]
    if not selected.size:
        raise ValueError("No solid colour found. Click a colour in the reference image.")
    return tuple(np.rint(np.median(selected, axis=0)).astype(int).tolist())


def sample_colour(image: Image.Image, x: int, y: int) -> RGB:
    patch = np.asarray(image.crop((max(0, x - 2), max(0, y - 2), min(image.width, x + 3), min(image.height, y + 3))))
    opaque = patch[patch[:, :, 3] >= 128, :3]
    if not opaque.size:
        raise ValueError("Click a solid part of the product.")
    return tuple(np.rint(np.median(opaque, axis=0)).astype(int).tolist())


def restrict_colour(image: Image.Image, mask: Image.Image, source: RGB) -> Image.Image:
    source_hue, source_saturation, source_value = Image.new("RGB", (1, 1), source).convert("HSV").getpixel((0, 0))
    hue, saturation, value = image.convert("RGB").convert("HSV").split()
    levels = np.arange(256)
    if source_saturation < 32 or source_value < 32:
        # Hue is unstable near greys and very deep shadows.
        affinity = saturation.point(np.rint(np.clip((92 - levels) / 56, 0, 1) * 255).tolist())
        if source_value < 64:
            darkness = value.point(np.rint(np.clip((128 - levels) / 64, 0, 1) * 255).tolist())
            deep_shadows = value.point(np.rint(np.clip((60 - levels) / 32, 0, 1) * 255).tolist())
            affinity = ImageChops.lighter(ImageChops.multiply(affinity, darkness), deep_shadows)
        return ImageChops.multiply(mask, affinity)

    distance = np.abs((levels - source_hue + 128) % 256 - 128)
    hue_weight = np.rint(np.clip((40 - distance) / 24, 0, 1) * 255).tolist()
    sat_floor = max(16, source_saturation * 0.25)
    sat_weight = np.rint(np.clip((levels - sat_floor) / max(24, source_saturation * 0.35), 0, 1) * 255).tolist()
    affinity = ImageChops.multiply(hue.point(hue_weight), saturation.point(sat_weight))
    return ImageChops.multiply(mask, affinity)


def apply_colour(image: Image.Image, mask: Image.Image, target: RGB) -> Image.Image:
    rgb = image.convert("RGB")
    luminance = rgb.convert("L")
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
    result.putalpha(image.getchannel("A"))
    return result


def recolour(segmenter: Segmenter, image: Image.Image, target: RGB, source: RGB | None) -> Image.Image:
    mask = segmenter.foreground(image)
    if source is not None:
        mask = restrict_colour(image, mask, source)
    return apply_colour(image, mask, target)


def export_batch(
    segmenter: Segmenter,
    paths: Sequence[Path],
    destination: Path,
    target: RGB,
    source: RGB | None,
    progress: Callable[[int, str], None],
    cancelled: Callable[[], bool],
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
        temporary = output.with_suffix(".part")
        try:
            result = recolour(segmenter, open_image(path), target, source)
            result.save(temporary, format="PNG")
            temporary.replace(output)
            successes += 1
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            failures.append(f"{path.name} — {error}")
        finally:
            temporary.unlink(missing_ok=True)
        progress(index, f"Processed {index} of {len(paths)}")

    if failures:
        (run_folder / "errors.txt").write_text("\n".join(failures) + "\n", encoding="utf-8")
    return run_folder, successes, failures
