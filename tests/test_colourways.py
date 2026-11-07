import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from colour_shifter.catalogue import export_catalogue
from colour_shifter.material import select_material
from colour_shifter.recolour import apply_colour
from colour_shifter.reflections import reflection_cast


class MaterialTests(unittest.TestCase):
    def test_large_ceramic_region_wins_when_ai_only_detects_taps(self):
        image = Image.new("RGBA", (220, 180), "#F5F5F5")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 75, 219, 179), fill="#DAA88F")
        draw.rectangle((153, 115, 219, 179), fill="#F1D5C6")
        draw.rounded_rectangle((48, 32, 70, 104), radius=5, fill="#B9B9B9")
        draw.rectangle((56, 40, 57, 70), fill="#DAA88F")
        foreground = Image.new("L", image.size)
        ImageDraw.Draw(foreground).rectangle((48, 32, 70, 104), fill=255)

        source, mask = select_material(image, foreground)
        target = (180, 199, 214)
        result = apply_colour(image, mask, target)
        self.assertEqual(source, (218, 168, 143))
        self.assertEqual(mask.getpixel((100, 140)), 255)
        self.assertEqual(result.getpixel((100, 140)), (*target, 255))
        for point in ((56, 55), (60, 55), (130, 40)):
            self.assertEqual(result.getpixel(point), image.getpixel(point))

    def test_ai_contour_protects_a_backdrop_of_the_same_hue(self):
        image = Image.new("RGBA", (150, 120), "#AE3035")
        ImageDraw.Draw(image).rectangle((40, 30, 110, 95), fill="#D74A4D")
        foreground = Image.new("L", image.size)
        ImageDraw.Draw(foreground).rectangle((40, 30, 110, 95), fill=255)
        _, mask = select_material(image, foreground)
        self.assertEqual(mask.getpixel((70, 60)), 255)
        self.assertEqual(mask.getpixel((20, 20)), 0)

    def test_manual_starting_colour_selects_a_different_region(self):
        image = Image.new("RGBA", (160, 120), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 10, 100, 110), fill="#DAA88F")
        draw.rectangle((110, 30, 150, 90), fill="#6391C2")
        foreground = Image.new("L", image.size)
        source, mask = select_material(image, foreground, (99, 145, 194))
        self.assertEqual(source, (99, 145, 194))
        self.assertEqual(mask.getpixel((130, 60)), 255)
        self.assertEqual(mask.getpixel((70, 60)), 0)

    def test_neutral_product_uses_ai_contour_when_wall_shadow_has_a_colour_cast(self):
        image = Image.new("RGBA", (220, 180), "#CAC8C7")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 50, 130, 179), fill="#A29792")
        draw.rectangle((77, 57, 166, 118), fill="#DEDAD5")
        foreground = Image.new("L", image.size)
        ImageDraw.Draw(foreground).rectangle((77, 57, 166, 118), fill=255)
        source, mask = select_material(image, foreground)
        self.assertEqual(source, (222, 218, 213))
        self.assertEqual(mask.getpixel((120, 90)), 255)
        self.assertEqual(mask.getpixel((35, 115)), 0)

    def test_manual_colour_can_select_a_frame_filling_product_without_ai_contour(self):
        image = Image.new("RGBA", (160, 120), "#DFB8A7")
        draw = ImageDraw.Draw(image)
        draw.rectangle((18, 36, 52, 80), fill="#7F5547")
        draw.rectangle((55, 40, 120, 90), fill="#201613")
        source = (223, 184, 167)
        _, mask = select_material(image, Image.new("L", image.size), source)
        self.assertEqual(mask.getpixel((35, 45)), 255)
        self.assertEqual(mask.getpixel((70, 60)), 0)

    def test_glaze_rim_recolours_mixed_pixels_and_fills_a_bright_gap(self):
        source = (214, 163, 146)
        target = (186, 197, 215)
        wall = (230, 224, 222)
        image = Image.new("RGBA", (280, 220), wall)
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 40, 185, 180), fill=source)
        draw.rectangle((40, 110, 175, 112), fill=(227, 218, 216))
        mixed_colours = [(220, 178, 165), (223, 195, 185), (226, 207, 201), (229, 219, 215)]
        for x, pixel in enumerate(mixed_colours, start=186):
            draw.line((x, 40, x, 180), fill=pixel)
        foreground = Image.new("L", image.size)
        ImageDraw.Draw(foreground).rectangle((30, 40, 185, 180), fill=255)

        detected, mask = select_material(image, foreground)
        result = apply_colour(image, mask, target)
        self.assertEqual(detected, source)
        self.assertEqual(result.getpixel((100, 80)), (*target, 255))
        for point in ((100, 111), (186, 80)):
            red, _, blue, _ = result.getpixel(point)
            self.assertLess(red, blue)
        self.assertEqual(result.getpixel((190, 80)), (*wall, 255))
        self.assertEqual(result.getpixel((250, 80)), (*wall, 255))


class ReflectionTests(unittest.TestCase):
    def test_shifts_a_glaze_reflection_and_preserves_neutral_chrome(self):
        source = (218, 168, 143)
        target = (180, 199, 214)
        image = Image.new("RGBA", (240, 180), "#F5F5F5")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 95, 239, 179), fill=source)
        draw.rectangle((48, 61, 73, 94), fill="#E8E8E8")
        draw.rectangle((49, 68, 55, 88), fill="#363636")
        draw.rectangle((56, 68, 63, 88), fill="#C8A694")
        draw.rectangle((114, 68, 134, 88), fill="#C8A694")
        surface = Image.new("L", image.size)
        ImageDraw.Draw(surface).rectangle((0, 95, 239, 179), fill=255)

        reflections = reflection_cast(image, surface, source)
        self.assertIsNotNone(reflections)
        result = apply_colour(image, surface, target, reflections=reflections)
        red, green, blue, _ = result.getpixel((59, 79))
        self.assertLess(red, green)
        self.assertLess(green, blue)
        self.assertLessEqual(abs(result.convert("L").getpixel((59, 79)) - image.convert("L").getpixel((59, 79))), 1)
        for point in ((69, 79), (51, 79), (122, 79), (0, 0)):
            self.assertEqual(result.getpixel(point), image.getpixel(point))
        self.assertEqual(result.getpixel((100, 120)), (*target, 255))


class CatalogueTests(unittest.TestCase):
    def test_discovers_named_palette_and_masks_each_nested_photo_once(self):
        class CountingSegmenter:
            calls = 0

            def foreground(self, image):
                self.calls += 1
                return Image.new("L", image.size, 255)

        colours = {
            "Coral Pink": "#DAB19F",
            "Flamingo Pink": "#C78E9D",
            "Lemon Sorbet": "#D8D7A8",
            "Mint Green": "#A4BF91",
            "Powder Blue": "#A4BAD2",
        }
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root, references, destination = (base / name for name in ("source", "references", "exports"))
            nested = root / "Deco" / "Close-ups"
            for path in (nested, references, destination):
                path.mkdir(parents=True)
            for name, colour in colours.items():
                image = Image.new("RGBA", (160, 120), "white")
                ImageDraw.Draw(image).rectangle((20, 30, 135, 105), fill=colour)
                image.save(references / f"Deco toilet basin set {name} new.png")
                if name in {"Coral Pink", "Mint Green"}:
                    image.save(nested / f"Basin {name} front.png")

            segmenter = CountingSegmenter()
            result = export_catalogue(
                segmenter, root, references, destination, suffix=True,
                progress=lambda *_: None, cancelled=lambda: False,
            )
            self.assertEqual(segmenter.calls, 7)
            self.assertEqual((result.completed, result.variants, result.failed), (2, 10, 0))
            self.assertEqual([swatch.name for swatch in result.palette], list(colours))
            manifest = json.loads((result.folder / "manifest.json").read_text(encoding="utf-8"))
            for index, name in enumerate(colours, start=1):
                target = result.folder / f"{index:02d} {name}" / "Deco" / "Close-ups"
                self.assertEqual(
                    sorted(path.name for path in target.glob("*.png")),
                    [f"Basin front [01] - {name}.png", f"Basin front [02] - {name}.png"],
                )
            self.assertEqual(len(manifest["photos"]), 2)
            self.assertTrue(all(len(photo["outputs"]) == 5 for photo in manifest["photos"]))

            retry = export_catalogue(
                segmenter, root, references, destination, suffix=True,
                progress=lambda *_: None, cancelled=lambda: False,
                only_photos={nested / "Basin Mint Green front.png"},
            )
            self.assertEqual((retry.completed, retry.variants), (1, 5))
            self.assertEqual(segmenter.calls, 13)
            self.assertTrue(
                (retry.folder / "01 Coral Pink" / "Deco" / "Close-ups" / "Basin front [02] - Coral Pink.png").is_file()
            )


if __name__ == "__main__":
    unittest.main()
