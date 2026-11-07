"""Transfer the ceramic's colour cast on nearby reflective fittings."""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from .swatches import RGB


LEVELS = np.arange(256)
LUMINANCE = np.array([.299, .587, .114])


@dataclass(frozen=True)
class Reflections:
    source: RGB
    box: tuple[int, int, int, int]
    strength: Image.Image

    def apply(self, result: Image.Image, target: RGB) -> None:
        original = np.array(self.source, dtype=float)
        replacement = np.array(target, dtype=float)
        shift = (
            (replacement - replacement @ LUMINANCE) - (original - original @ LUMINANCE)
        ) / (max(self.source) - min(self.source))
        if not shift.any():
            return

        channels = result.crop(self.box).split()
        adjusted = []
        for channel, change in zip(channels[:3], shift):
            amount = self.strength.point(np.rint(np.clip(LEVELS * abs(change), 0, 255)).tolist())
            adjusted.append(ImageChops.add(channel, amount) if change >= 0 else ImageChops.subtract(channel, amount))
        result.paste(Image.merge("RGBA", (*adjusted, channels[3])), self.box)


def reflection_cast(image: Image.Image, material: Image.Image, source: RGB) -> Reflections | None:
    if max(source) - min(source) < 22:
        return None
    scale = min(1, 384 / max(image.size))
    size = tuple(max(1, round(length * scale)) for length in image.size)
    rgb = np.asarray(image.resize(size, Image.Resampling.BOX).convert("RGB"))
    outside = np.asarray(material.resize(size, Image.Resampling.BILINEAR)) < 160
    chroma = np.ptp(rgb, axis=2)
    lightness = rgb @ LUMINANCE

    # A polished fitting brings light and dark neutral pixels close together.
    # Require both near a tint so similar hues on the wall carry less weight.
    highlight = Image.fromarray((outside & (chroma < 23) & (lightness > 175)).astype(np.uint8) * 255)
    shadow = Image.fromarray((outside & (chroma < 26) & (lightness < 140)).astype(np.uint8) * 255)
    metal = ImageChops.multiply(highlight.filter(ImageFilter.MaxFilter(11)), shadow.filter(ImageFilter.MaxFilter(11)))
    metal = metal.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(1.2))
    near_size = tuple(max(1, round(length / 3)) for length in size)
    near = material.resize(near_size, Image.Resampling.BOX).point([0] * 100 + [255] * 156)
    near = near.filter(ImageFilter.MaxFilter(39)).resize(size, Image.Resampling.BILINEAR)
    region = ImageChops.multiply(metal, near)
    # Crop to the relevant part of the full-resolution image.
    bounds = region.getbbox()
    if not bounds:
        return None
    left, top, right, bottom = bounds
    w, h = image.size
    box = (
        max(0, (left - 1) * w // size[0]), max(0, (top - 1) * h // size[1]),
        min(w, (right + 1) * w // size[0] + 1), min(h, (bottom + 1) * h // size[1] + 1),
    )
    area = image.crop(box)
    tint = area.convert("RGB")
    source_hue = Image.new("RGB", (1, 1), source).convert("HSV").getpixel((0, 0))[0]
    distance = np.abs((LEVELS - source_hue + 128) % 256 - 128)
    hue = tint.convert("HSV").getchannel("H")
    match = hue.point(np.rint(np.clip((30 - distance) / 16, 0, 1) * 255).tolist())
    red, green, blue = tint.split()
    high = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    amount = ImageChops.subtract(high, low).point(np.rint(LEVELS * np.clip((LEVELS - 4) / 7, 0, 1)).tolist())
    amount = ImageChops.multiply(amount, match)
    sx, sy = size[0] / w, size[1] / h
    projected_region = region.resize(
        area.size, Image.Resampling.BILINEAR,
        box=(box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy),
    )
    amount = ImageChops.multiply(amount, projected_region)
    amount = ImageChops.multiply(amount, ImageChops.invert(material.crop(box)))
    amount = ImageChops.multiply(amount, area.getchannel("A"))
    return Reflections(source, box, amount) if amount.getbbox() else None
