import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from colour_shifter.swatches import Swatch, SwatchStore


class SwatchStoreTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "swatches.sqlite3"
        self.store = SwatchStore(self.path)

    def test_named_swatch_is_visible_to_other_instances(self):
        self.store.add(" Dark Rose ", "c54860")
        other = SwatchStore(self.path)
        self.assertEqual(other.get("dark rose"), Swatch("Dark Rose", "#C54860"))
        self.assertEqual(other.rename("Dark Rose", "Dusty Rose"), Swatch("Dusty Rose", "#C54860"))
        self.assertEqual(self.store.delete("DUSTY ROSE").hex, "#C54860")
        self.assertEqual(other.snapshot(), ([], []))

    def test_invalid_colour_and_duplicate_names_leave_existing_swatches_intact(self):
        self.store.add("Rose", "#C54860")
        self.store.add("Plum", "#5A2F68")
        with self.assertRaisesRegex(ValueError, "six-digit"):
            self.store.add("Invalid", "#123")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.add("ROSE", "#FFFFFF")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.rename("Plum", "rose")
        self.assertEqual(self.store.get("Rose").hex, "#C54860")
        self.assertEqual(self.store.get("Plum").hex, "#5A2F68")

    def test_recent_colours_are_unique_ordered_and_bounded(self):
        for value in range(22):
            self.store.record_batch((value, 0, 0))
        self.store.record_batch((2, 0, 0))
        _, recent = SwatchStore(self.path).snapshot()
        self.assertEqual(recent[:2], ["#020000", "#150000"])
        self.assertEqual(len(recent), 20)
        self.assertEqual(len(set(recent)), 20)
        self.assertNotIn("#010000", recent)

    def test_concurrent_writers_keep_each_swatch(self):
        def add(number):
            SwatchStore(self.path).add(f"Swatch {number}", "#123456")

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(add, range(16)))
        saved, _ = self.store.snapshot()
        self.assertEqual(len(saved), 16)


if __name__ == "__main__":
    unittest.main()
