"""Find the coloured material using spatial support and local AI saliency."""

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .swatches import RGB


DETECTION_SIZE = 384
LEVELS = np.arange(256)


def _rgb_median(pixels) -> RGB:
    return tuple(np.rint(np.median(pixels, axis=0)).astype(int).tolist())


def _hue_distance(hues, centre):
    return np.abs((hues.astype(np.int16) - centre + 128) % 256 - 128)


def _dominant_region(rgb, hsv, sample):
    visible_colour = sample & (np.ptp(rgb, axis=2) >= 15) & (hsv[:, :, 2] >= 45)
    counts = np.bincount(hsv[:, :, 0][visible_colour], minlength=256)
    if not counts.any():
        return visible_colour
    nearby = sum(np.roll(counts, offset) for offset in range(-8, 9))
    hue = int(np.argmax(nearby + counts))
    return visible_colour & (_hue_distance(hsv[:, :, 0], hue) <= 12)


def _lookup(channel, weights):
    return channel.point(np.rint(np.clip(weights, 0, 1) * 255).tolist())


def _affinity(
    rgb: Image.Image, source: RGB, *, edges: bool = False, neutral: bool = False, protect_dark: bool = False,
) -> Image.Image:
    source_hue, source_sat, source_value = Image.new("RGB", (1, 1), source).convert("HSV").getpixel((0, 0))
    hue, saturation, value = rgb.convert("HSV").split()
    chroma = max(source) - min(source)
    if neutral or chroma < 15 or source_value < 35:
        neutral_weight = _lookup(saturation, (108 - LEVELS) / 68)
        if source_value < 75:
            return ImageChops.multiply(neutral_weight, _lookup(value, (source_value + 100 - LEVELS) / 65))
        return ImageChops.multiply(neutral_weight, _lookup(value, (LEVELS - max(28, source_value - 95)) / 45))

    hue_weight = _lookup(hue, (27 - _hue_distance(LEVELS, source_hue)) / 16)
    sat_weight = _lookup(saturation, (LEVELS - max(9, source_sat * .32)) / max(9, source_sat * .28))
    if edges:
        # Glare washes out the glaze near a bright rim. Relax the colour
        # match there while retaining the stricter match in darker shadows.
        pale = _lookup(saturation, (LEVELS - max(9, source_sat * .21)) / max(9, source_sat * .22))
        bright = _lookup(value, (LEVELS - source_value + 3) / 12)
        sat_weight = ImageChops.lighter(sat_weight, ImageChops.multiply(pale, bright))
    if protect_dark:
        brightness = _lookup(value, (LEVELS - max(24, source_value * .38)) / max(12, source_value * .16))
        red, green, blue = rgb.split()
        maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
        chroma_weight = _lookup(ImageChops.subtract(maximum, minimum), (LEVELS - max(10, chroma * .32)) / 8)
        sat_weight = ImageChops.multiply(sat_weight, brightness)
        sat_weight = ImageChops.multiply(sat_weight, chroma_weight)
    return ImageChops.multiply(hue_weight, sat_weight)


def _coherent_regions(affinity: Image.Image, *, neutral: bool = False) -> Image.Image:
    # Erode first so thin reflection streaks and narrow bridges do not join
    # the ceramic body. Retain large pieces, then restore and feather edges.
    core = affinity.point([0] * 170 + [1] * 86).filter(ImageFilter.MinFilter(7 if neutral else 5))
    labels = core.convert("I")
    pixels = labels.load()
    next_label = 2
    rows, columns = np.nonzero(np.asarray(core))
    for x, y in zip(columns.tolist(), rows.tolist()):
        if pixels[x, y] == 1:
            ImageDraw.floodfill(labels, (x, y), next_label)
            next_label += 1
    if next_label == 2:
        return Image.new("L", affinity.size)
    labelled = np.asarray(labels)
    sizes = np.bincount(labelled.ravel(), minlength=next_label)
    threshold = max(12, labelled.size * .0015, sizes[2:].max() * (.12 if neutral else .035))
    keep = sizes >= threshold
    keep[:2] = False
    support = Image.fromarray(keep[labelled].astype(np.uint8) * 255)
    support = support.filter(ImageFilter.MaxFilter(13 if neutral else 15))
    if not neutral:
        # Bridge short breaks in the same glaze, for example the strip
        # between the top of a tap and the back rim of a basin.
        support = support.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
    return support.filter(ImageFilter.GaussianBlur(.8))


def _refine_rim(rgb: Image.Image, mask: Image.Image, source: RGB) -> Image.Image:
    # Estimate mixed edge pixels from the local ceramic and surrounding
    # colours. A single hue threshold is unstable on JPEG block boundaries.
    scale = min(1, 1600 / max(rgb.size))
    size = tuple(max(1, round(length * scale)) for length in rgb.size)
    local = mask.resize(size, Image.Resampling.BOX)
    radius = max(2, round(max(size) / 180))
    rgb = rgb.resize(size, Image.Resampling.BOX)

    # Thin specular streaks interrupt the hue match inside the glaze.
    # Only fill small, locally bright gaps surrounded by the material.
    gap = 4 * radius + 1
    closed = local.filter(ImageFilter.MaxFilter(gap)).filter(ImageFilter.MinFilter(gap))
    light = rgb.convert("L")
    glint = ImageChops.subtract(light, light.filter(ImageFilter.GaussianBlur(2 * radius)))
    glint = _lookup(glint, (LEVELS - 4) / 8)
    filled = ImageChops.multiply(ImageChops.subtract(closed, local), glint)
    local = ImageChops.add(local, filled)
    mask = ImageChops.add(mask, filled.resize(mask.size, Image.Resampling.BILINEAR))

    core = local.point([0] * 200 + [255] * 56)
    reach = core.filter(ImageFilter.MaxFilter(2 * radius + 1))
    inside = core.filter(ImageFilter.MinFilter(2 * max(1, radius // 3) + 1))
    rim = ImageChops.subtract(reach, inside)

    high = rgb.getchannel(source.index(max(source)))
    low = rgb.getchannel(source.index(min(source)))
    chroma = ImageChops.subtract(high, low, scale=2, offset=128)
    context = ImageFilter.GaussianBlur(radius)

    def local_colour(sample):
        weight = np.asarray(sample.filter(context), dtype=np.float32)
        weighted = np.asarray(ImageChops.multiply(chroma, sample).filter(context), dtype=np.float32)
        return 255 * weighted / np.maximum(weight, 1), weight

    foreground, foreground_weight = local_colour(core)
    background, background_weight = local_colour(ImageChops.invert(reach))
    signal = np.asarray(chroma.filter(ImageFilter.GaussianBlur(max(.65, radius / 6))), dtype=np.float32)
    span = foreground - background
    fraction = np.clip((signal - background - 1.5) / np.maximum(span - 3, 1), 0, 1)
    # The original pixel already contains glaze colour, so compensate for
    # blending the replacement over it rather than over a clean backdrop.
    corrected = 2 * fraction / (1 + fraction)
    confident = (span >= 4) & (foreground_weight >= 16) & (background_weight >= 16)
    updated = Image.fromarray(np.rint(255 * corrected).astype(np.uint8))
    confidence = Image.fromarray(confident.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(1))
    smooth = local.filter(ImageFilter.GaussianBlur(max(.6, radius / 6)))
    refined = Image.composite(updated, smooth, confidence)
    refined = refined.filter(ImageFilter.GaussianBlur(max(.6, radius / 7)))
    rim = rim.filter(ImageFilter.GaussianBlur(max(.65, radius / 5)))
    return Image.composite(
        refined.resize(mask.size, Image.Resampling.BILINEAR),
        mask,
        rim.resize(mask.size, Image.Resampling.BILINEAR),
    )


def _fills_corners(support):
    mask = np.asarray(support)
    side = max(1, min(mask.shape) // 20)
    corners = (mask[:side, :side], mask[:side, -side:], mask[-side:, :side], mask[-side:, -side:])
    return sum((corner >= 128).mean() > .6 for corner in corners) >= 3


def _analyse(image, foreground, override):
    scale = min(1, DETECTION_SIZE / max(image.size))
    size = tuple(max(1, round(dimension * scale)) for dimension in image.size)
    preview = image.resize(size, Image.Resampling.BOX)
    preview_rgb = preview.convert("RGB")
    rgb = np.asarray(preview_rgb)
    visible = np.asarray(preview.getchannel("A")) >= 128
    saliency = np.asarray(foreground.resize(size, Image.Resampling.BILINEAR))
    inside = (saliency >= 160) & visible

    if override is None:
        hsv = np.asarray(preview_rgb.convert("HSV"))
        global_colour = _dominant_region(rgb, hsv, visible)
        ai_colour = _dominant_region(rgb, hsv, inside)
        selection = global_colour
        if ai_colour.sum() >= max(24, inside.sum() * .60):
            # Let the AI disambiguate competing colours, but sample the
            # whole colour region when both methods found the same material.
            if not np.any(ai_colour & global_colour):
                selection = ai_colour
        colour = _rgb_median(rgb[selection]) if selection.sum() >= max(24, visible.sum() * .006) else None
    else:
        colour = override

    if colour is not None and max(colour) - min(colour) >= 15:
        affinity = _affinity(preview_rgb, colour, edges=True, protect_dark=override is not None)
        affinity = ImageChops.multiply(affinity, Image.fromarray(visible.astype(np.uint8) * 255))
        support = _coherent_regions(affinity)
        region = np.asarray(support) >= 128
        if region.any():
            overlap = (region & inside).sum() / region.sum()
            weak_background_colour = override is None and max(colour) - min(colour) < 24 and inside.any() and overlap < .50
            if not weak_background_colour and not _fills_corners(support):
                return colour, support, overlap >= .65, False
            if not weak_background_colour and np.count_nonzero(region & inside) >= max(12, inside.sum() * .30):
                # When the same hue surrounds a product, use the AI contour
                # to keep the surrounding backdrop out of the mask.
                return colour, support, True, False
            if override is not None:
                return colour, support, False, False
        if override is not None:
            raise ValueError("No coherent region matches the starting colour. Sample a solid area of the product.")

    if not inside.any():
        raise ValueError("No recolourable product was detected. Click a solid area to choose the starting colour.")
    colour = override or _rgb_median(rgb[inside])
    neutral = max(colour) - min(colour) < 24
    if not neutral:
        return colour, None, True, False
    # A neutral glazed surface can look like the wall by colour alone. Use
    # the AI contour and favour broad, smooth patches over chrome and pipes.
    lightness = preview_rgb.convert("L")
    contrast = ImageChops.subtract(lightness.filter(ImageFilter.MaxFilter(5)), lightness.filter(ImageFilter.MinFilter(5)))
    smooth = _lookup(contrast, (65 - LEVELS) / 35)
    affinity = ImageChops.multiply(_affinity(preview_rgb, colour, neutral=True), Image.fromarray(saliency))
    support = _coherent_regions(ImageChops.multiply(affinity, smooth), neutral=True)
    if support.getextrema()[1] < 128:
        raise ValueError("Could not separate a neutral material from the surrounding objects.")
    return colour, support, True, True


def detect_colour(image: Image.Image, foreground: Image.Image) -> RGB:
    return _analyse(image, foreground, None)[0]


def select_material(
    image: Image.Image, foreground: Image.Image, source: RGB | None = None,
) -> tuple[RGB, Image.Image]:
    colour, support, use_foreground, neutral = _analyse(image, foreground, source)
    rgb = image.convert("RGB")
    affinity = _affinity(
        rgb, colour, edges=support is not None, neutral=neutral,
        protect_dark=source is not None and not use_foreground,
    )
    if support is not None:
        affinity = ImageChops.multiply(affinity, support.resize(image.size, Image.Resampling.BILINEAR))
    if use_foreground:
        affinity = ImageChops.multiply(affinity, foreground)
    if not neutral and max(colour) - min(colour) >= 24:
        affinity = _refine_rim(rgb, affinity, colour)
    alpha = image.getchannel("A").point([0] + [255] * 255)
    affinity = ImageChops.multiply(affinity, alpha)
    if affinity.getextrema()[1] < 128:
        raise ValueError("No matching product colour found. Click a solid area to override the starting colour.")
    return colour, affinity
