"""Discover a reference palette and export a colourway tree for a catalogue."""

import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

import numpy as np
from PIL import Image, ImageCms

from .images import IMAGE_EXTENSIONS, folder_images, open_image
from .recolour import Segmenter, colour_variants, prepare_recolour, reference_colour, save_png
from .swatches import RGB, hex_colour


PALETTE_SIZE = 5
MAX_CATALOGUE_PIXELS = 60_000_000


@dataclass(frozen=True)
class Colourway:
    name: str
    rgb: RGB
    reference: Path


@dataclass(frozen=True)
class CatalogueResult:
    folder: Path
    palette: tuple[Colourway, ...]
    photos: int
    completed: int
    variants: int
    failed: int


def catalogue_images(root: Path) -> list[Path]:
    paths = []
    for directory, children, files in os.walk(root):
        parent = Path(directory)
        children[:] = [
            name for name in children if not (parent / name).is_symlink() and not (parent / name).is_junction()
        ]
        paths.extend(
            parent / name for name in files
            if Path(name).suffix.lower() in IMAGE_EXTENSIONS and not (parent / name).is_symlink()
        )
    return sorted(paths, key=lambda path: str(path.relative_to(root)).casefold())


def _series(paths: Sequence[Path]):
    # Reference variants usually share the product/view name and differ in
    # the colour words. Use the names to identify a comparable set of views.
    groups = defaultdict(list)
    for path in paths:
        words = path.stem.split()
        for prefix in range(2, len(words)):
            for suffix in range(min(2, len(words) - prefix - 1) + 1):
                stop = len(words) - suffix
                key = (tuple(words[:prefix]), tuple(words[stop:]))
                groups[key].append((path, " ".join(words[prefix:stop])))
    groups = sorted(
        ((key, items) for key, items in groups.items() if len(items) == PALETTE_SIZE),
        key=lambda group: (len(group[0][1]) > 0, sum(map(len, group[0]))),
        reverse=True,
    )
    for _, items in groups:
        if len({label.casefold() for _, label in items}) == PALETTE_SIZE:
            yield items


def _distances(colours: Sequence[RGB]) -> np.ndarray:
    strip = Image.new("RGB", (len(colours), 1))
    strip.putdata(colours)
    lab = ImageCms.profileToProfile(strip, ImageCms.createProfile("sRGB"), ImageCms.createProfile("LAB"), outputMode="LAB")
    coordinates = np.asarray(lab, dtype=np.float32)[0] * np.array([.24, 1., 1.])
    return np.linalg.norm(coordinates[:, None] - coordinates[None, :], axis=2)


def discover_palette(
    segmenter: Segmenter, paths: Sequence[Path], progress: Callable[[str], None] = lambda _: None,
) -> tuple[Colourway, ...]:
    if len(paths) < PALETTE_SIZE:
        raise ValueError("Choose a palette folder with at least five reference photos.")
    samples = {}

    def sample(path):
        if path not in samples:
            progress(f"Discovering colour · {path.name}")
            try:
                image = open_image(path)
                image.thumbnail((800, 800))
                samples[path] = reference_colour(image, segmenter.foreground(image))
            except (OSError, ValueError, Image.DecompressionBombError):
                samples[path] = None
        return samples[path]

    for series in _series(paths):
        values = [sample(path) for path, _ in series]
        if all(value is not None for value in values):
            distances = _distances(values)
            if distances[np.triu_indices(PALETTE_SIZE, 1)].min() >= 6:
                return tuple(Colourway(name, value, path) for (path, name), value in zip(series, values))

    # Generic reference folders may have multiple views of each colour.
    # Spread five seeds through colour space, then take a representative
    # sample from each group of nearby colours.
    observed = [(path, colour) for path in paths if (colour := sample(path)) is not None]
    if len(observed) < PALETTE_SIZE:
        raise ValueError("Could not sample five reference colours. Choose clearer palette photos.")
    distances = _distances([colour for _, colour in observed])
    seeds = [int(distances.sum(axis=1).argmin())]
    while len(seeds) < PALETTE_SIZE:
        nearest = distances[:, seeds].min(axis=1)
        if nearest.max() < 6:
            raise ValueError("Could not distinguish five reference colours. Choose five photos of the same view in different colours.")
        seeds.append(int(nearest.argmax()))
    assignments = distances[:, seeds].argmin(axis=1)
    groups = [np.flatnonzero(assignments == index) for index in range(PALETTE_SIZE)]
    medoids = [int(group[distances[np.ix_(group, group)].sum(axis=1).argmin()]) for group in groups]
    if any(distances[left, right] < 6 for i, left in enumerate(medoids) for right in medoids[i + 1:]):
        raise ValueError("Could not distinguish five reference colours. Choose five photos of the same view in different colours.")
    return tuple(
        Colourway(
            observed[index][0].stem if len(paths) == PALETTE_SIZE else hex_colour(observed[index][1]),
            observed[index][1], observed[index][0],
        )
        for index in medoids
    )


def _safe_label(label: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", label).strip(" .")[:64]


def _output_stems(paths: Sequence[Path], palette: Sequence[Colourway], suffix: bool):
    names = [re.escape(swatch.name) for swatch in palette if not swatch.name.startswith("#")]
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(names) + r")(?!\w)", re.IGNORECASE) if names and suffix else None
    stems = {}
    for path in paths:
        stem = pattern.sub(" ", path.stem) if pattern else path.stem
        stems[path] = " ".join(stem.split()).strip(" _-")[:120] or "photo"
    duplicates = Counter((str(path.parent).casefold(), stem.casefold()) for path, stem in stems.items())
    used = set()
    for path, stem in stems.items():
        key = str(path.parent).casefold(), stem.casefold()
        label = stem
        index = 1
        while (str(path.parent).casefold(), label.casefold()) in used or (duplicates[key] > 1 and label == stem):
            label = f"{stem} [{index:02d}]"
            index += 1
        used.add((str(path.parent).casefold(), label.casefold()))
        stems[path] = label
    return stems


def export_catalogue(
    segmenter: Segmenter,
    root: Path,
    references: Path,
    destination: Path,
    *,
    suffix: bool,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
    overrides: Mapping[Path, RGB] | None = None,
    photos: Sequence[Path] | None = None,
    reference_paths: Sequence[Path] | None = None,
    only_photos: set[Path] | None = None,
) -> CatalogueResult:
    root = root.resolve(strict=True)
    destination = destination.resolve(strict=True)
    if destination.is_relative_to(root):
        raise ValueError("Choose an export folder outside the source tree to keep future batches separate.")
    if photos is None:
        photos = catalogue_images(root)
    all_photos = photos
    if only_photos is not None:
        if not only_photos <= set(all_photos):
            raise ValueError("Every selected photo must be inside the catalogue's source tree.")
        photos = [path for path in all_photos if path in only_photos]
    if not photos:
        raise ValueError("The source folder contains no supported images.")
    if cancelled():
        raise ValueError("Catalogue export stopped before palette discovery.")

    def discovering(message):
        if cancelled():
            raise ValueError("Catalogue export stopped during palette discovery.")
        progress(0, len(photos), message)

    if reference_paths is None:
        reference_paths = folder_images(references)
    palette = discover_palette(segmenter, reference_paths, discovering)
    prefix = datetime.now().strftime("colourways-%Y%m%d-%H%M%S-")
    run = Path(mkdtemp(prefix=prefix, dir=destination))
    stems = _output_stems(all_photos, palette, suffix)
    labels = [_safe_label(swatch.name) for swatch in palette]
    folders = [f"{index:02d} {label}" for index, label in enumerate(labels, start=1)]
    manifest = {
        "source_folder": str(root),
        "palette": [
            {"name": swatch.name, "hex": hex_colour(swatch.rgb), "reference": str(swatch.reference), "folder": folder}
            for swatch, folder in zip(palette, folders)
        ],
        "photos": [],
    }
    completed = variants = failed = 0
    for index, path in enumerate(photos):
        if cancelled():
            break
        relative = path.relative_to(root)
        progress(index, len(photos), f"Recolouring {index + 1} of {len(photos)} · {path.name}")
        record = {"source": relative.as_posix(), "outputs": []}
        try:
            image = open_image(path, max_pixels=MAX_CATALOGUE_PIXELS)
            source, mask, reflections = prepare_recolour(segmenter, image, (overrides or {}).get(path))
            record["starting_colour"] = hex_colour(source)
            versions = colour_variants(image, mask, (swatch.rgb for swatch in palette), reflections=reflections)
            for folder, label, result in zip(folders, labels, versions):
                if cancelled():
                    break
                name = f"{stems[path]} - {label}" if suffix else stems[path]
                target = Path(folder) / relative.parent / f"{name}.png"
                output = run / target
                output.parent.mkdir(parents=True, exist_ok=True)
                save_png(result, output)
                record["outputs"].append(target.as_posix())
                variants += 1
            completed += len(record["outputs"]) == PALETTE_SIZE
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            record["error"] = str(error)
            failed += 1
        manifest["photos"].append(record)
        progress(index + 1, len(photos), f"Processed {index + 1} of {len(photos)} photos")
    manifest["stopped"] = cancelled()
    (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CatalogueResult(run, palette, len(photos), completed, variants, failed)
