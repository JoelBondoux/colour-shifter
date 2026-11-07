import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, UnidentifiedImageError

from colour_shifter import images, model
from colour_shifter.mcp_paths import McpWorkspace


class ImageSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_rejects_unapproved_decoder_even_with_approved_extension(self):
        path = self.root / "disguised.png"
        Image.new("RGB", (8, 8)).save(path, format="GIF")
        with self.assertRaises(UnidentifiedImageError):
            images.open_image(path)

    def test_dimension_and_file_size_limits(self):
        path = self.root / "product.png"
        Image.new("RGBA", (8, 8)).save(path)
        with patch.object(images, "MAX_IMAGE_PIXELS", 63):
            with self.assertRaisesRegex(ValueError, "pixels"):
                images.open_image(path)
        with patch.object(images, "MAX_IMAGE_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "MiB"):
                images.open_image(path)
        self.assertEqual(images.open_image(path).size, (8, 8))

    def test_rejects_multiframe_tiff(self):
        path = self.root / "animated.tiff"
        first = Image.new("RGB", (8, 8), "red")
        second = Image.new("RGB", (8, 8), "blue")
        first.save(path, save_all=True, append_images=[second])
        with self.assertRaisesRegex(ValueError, "single-frame"):
            images.open_image(path)

    def test_tampered_model_rejected_before_parsing(self):
        path = self.root / "model.onnx"
        path.write_bytes(b"fake")
        with patch.object(model, "MODEL_PATH", path), patch.object(model, "MODEL_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "integrity"):
                model.read_verified_model()


@unittest.skipUnless(os.name == "nt", "MCP workspace uses Windows filesystem rules")
class McpWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = self.parent / "workspace"
        self.root.mkdir()
        self.outside = self.parent / "outside.png"
        self.outside.touch()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, COLOUR_SHIFTER_MCP_ROOT=str(self.root)).start()
        self.workspace = McpWorkspace()

    def test_unsafe_paths_rejected_before_filesystem_access(self):
        for path in (
            str(self.outside),
            str(self.root / ".." / self.outside.name),
            r"\\server\share\photo.png",
            r"\\?\C:\photo.png",
            str(self.root / "photo.png:stream"),
            str(self.root / "NUL.png"),
        ):
            with self.subTest(path=path), patch.object(Path, "lstat", side_effect=AssertionError("filesystem access")):
                with self.assertRaises(ValueError):
                    self.workspace.image(path)

    def test_rejects_hard_links_and_reparse_points(self):
        alias = self.root / "alias.png"
        os.link(self.outside, alias)
        with self.assertRaisesRegex(ValueError, "hard-linked"):
            self.workspace.image(str(alias))

        with patch.object(Path, "lstat") as metadata:
            metadata.return_value.st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
            metadata.return_value.st_mode = stat.S_IFDIR
            with self.assertRaisesRegex(ValueError, "reparse"):
                self.workspace.image(str(self.root / "junction" / "image.png"))

    def test_workspace_must_be_configured_and_regular_images_work(self):
        image = self.root / "product.png"
        image.touch()
        self.assertEqual(self.workspace.images_in(str(self.root)), [image])
        with patch.dict(os.environ, COLOUR_SHIFTER_MCP_ROOT=""):
            with self.assertRaisesRegex(ValueError, "COLOUR_SHIFTER_MCP_ROOT"):
                McpWorkspace()

    def test_recursive_catalogue_checks_nested_inputs(self):
        nested = self.root / "nested"
        nested.mkdir()
        regular = nested / "product.png"
        regular.touch()
        self.assertEqual(self.workspace.catalogue_images(str(self.root)), [regular])
        alias = nested / "alias.png"
        os.link(self.outside, alias)
        with self.assertRaisesRegex(ValueError, "hard-linked"):
            self.workspace.catalogue_images(str(self.root))


if __name__ == "__main__":
    unittest.main()
