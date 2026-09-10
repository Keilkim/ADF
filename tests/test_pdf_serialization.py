"""Real malformed PDFs exercise serialization before edits and exports."""

import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import pymupdf

from adf.document import PdfDocument, compress_pdf, merge_pdfs, split_pdf


LABELS = ["First page", "Second page", "Third page"]


def image_bytes(color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (80, 40), color).save(stream, format="PNG")
    return stream.getvalue()


def malformed_pdf_bytes():
    """Keep valid xrefs while introducing an invalid, unused dictionary key.

    The unused object precedes live pages/images so compacting xrefs would
    also invalidate object selections held by the editor.
    """
    with pymupdf.open() as doc:
        unused = doc.get_new_xref()
        doc.update_object(unused, "<< /UnusedMarker 1 >>")
        for label in LABELS:
            doc.new_page(width=300, height=400).insert_text((30, 50), label)
        doc[0].insert_image((30, 80, 110, 120), stream=image_bytes("red"))
        data = doc.tobytes(garbage=0, pretty=True)
    assert data.count(b"/UnusedMarker") == 1
    return data.replace(b"/UnusedMarker", b" UnusedMarker", 1)


class PdfSerializationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="adf-serialization-")
        self.root = Path(self.folder.name)
        self.original = malformed_pdf_bytes()
        self.source = self.root / "malformed-unused-object.pdf"
        self.source.write_bytes(self.original)
        self.model = PdfDocument()
        self.model.open(self.source)

    def tearDown(self):
        try:
            self.assertEqual(self.source.read_bytes(), self.original)
        finally:
            self.model.close()
            self.folder.cleanup()

    def labels(self, doc=None):
        return [page.get_text().strip() for page in (self.model.doc if doc is None else doc)]

    def test_fixture_reproduces_original_save_and_tobytes_error(self):
        for operation in ("save", "tobytes"):
            with self.subTest(operation=operation):
                with pymupdf.open(stream=self.original, filetype="pdf") as doc:
                    self.assertFalse(doc.is_repaired)
                    self.assertEqual(self.labels(doc), LABELS)
                    with self.assertRaisesRegex(Exception, "code=8: invalid key in dict"):
                        if operation == "save":
                            doc.save(self.root / "uncollected.pdf", deflate=True)
                        else:
                            doc.tobytes()

    def test_clipboard_image_page_insert_undo_redo_and_save(self):
        self.model.insert_image_page(image_bytes(), 1)
        self.assertEqual(self.labels(), [LABELS[0], "", *LABELS[1:]])
        self.assertEqual(len(self.model.doc[1].get_images()), 1)
        self.assertTrue(self.model.dirty)
        self.assertTrue(self.model.undo())
        self.assertEqual(self.labels(), LABELS)
        self.assertFalse(self.model.dirty)
        self.assertTrue(self.model.redo())
        self.assertEqual(self.model.page_count, 4)

        output = self.root / "image-page.pdf"
        self.model.save(output)
        self.assertFalse(self.model.dirty)
        with pymupdf.open(output) as saved:
            self.assertEqual(self.labels(saved), [LABELS[0], "", *LABELS[1:]])
            self.assertEqual(len(saved[1].get_images()), 1)
        self.assertTrue(self.model.undo())
        self.assertTrue(self.model.dirty)
        self.assertTrue(self.model.redo())
        self.assertFalse(self.model.dirty)

    def test_image_overlay_preserves_selected_xrefs_across_history(self):
        page_xrefs = [page.xref for page in self.model.doc]
        selected_image = self.model.doc[0].get_images()[0][0]
        inserted_image = self.model.add_image(0, (150, 80, 230, 120), image_bytes())
        self.assertNotEqual(inserted_image, selected_image)
        self.assertEqual([page.xref for page in self.model.doc], page_xrefs)
        self.assertEqual({image[0] for image in self.model.doc[0].get_images()},
                         {selected_image, inserted_image})
        self.assertTrue(self.model.undo())
        self.assertEqual(self.model.doc[0].get_images()[0][0], selected_image)
        self.assertTrue(self.model.redo())
        self.assertEqual([page.xref for page in self.model.doc], page_xrefs)
        self.assertEqual({image[0] for image in self.model.doc[0].get_images()},
                         {selected_image, inserted_image})
        self.model.remove_image(0, selected_image)
        pixmap = self.model.doc[0].get_pixmap()
        self.assertEqual(pixmap.pixel(70, 100), (255, 255, 255))
        self.assertEqual(pixmap.pixel(190, 100), (0, 0, 255))

    def test_other_page_edits_each_start_with_malformed_source(self):
        cases = [
            ("blank", lambda: self.model.insert_blank(1), [LABELS[0], "", *LABELS[1:]]),
            ("rotate", lambda: self.model.rotate([0], 90), LABELS),
            ("reorder", lambda: self.model.reorder([2, 0, 1]), [LABELS[2], *LABELS[:2]]),
            ("delete", lambda: self.model.delete([1]), [LABELS[0], LABELS[2]]),
        ]
        for name, edit, expected in cases:
            with self.subTest(edit=name):
                self.model.open(self.source)
                edit()
                self.assertEqual(self.labels(), expected)
                if name == "rotate":
                    self.assertEqual(self.model.doc[0].rotation, 90)
                self.assertTrue(self.model.undo())
                self.assertEqual(self.labels(), LABELS)
                self.assertEqual(self.model.doc[0].rotation, 0)
                self.assertFalse(self.model.dirty)
                self.assertTrue(self.model.redo())
                self.assertEqual(self.labels(), expected)

    def test_failed_image_edit_rolls_back_and_next_edit_succeeds(self):
        page_xrefs = [page.xref for page in self.model.doc]
        with self.assertRaises(Exception):
            self.model.add_image(0, (150, 80, 230, 120), b"not an image")
        self.assertEqual(self.labels(), LABELS)
        self.assertEqual([page.xref for page in self.model.doc], page_xrefs)
        self.assertEqual(len(self.model.doc[0].get_images()), 1)
        self.assertFalse(self.model.dirty)
        self.assertFalse(self.model.can_undo)
        self.assertFalse(list(Path(self.model._history_dir.name).glob("*.pdf")))
        self.model.insert_image_page(image_bytes(), 1)
        self.assertEqual(self.model.page_count, 4)

    def test_tobytes_and_unedited_save_preserve_contents_and_source(self):
        page_xrefs = [page.xref for page in self.model.doc]
        encoded = self.model.tobytes()
        with pymupdf.open(stream=encoded, filetype="pdf") as saved:
            self.assertEqual(self.labels(saved), LABELS)
            self.assertEqual([page.xref for page in saved], page_xrefs)
            self.assertEqual(len(saved[0].get_images()), 1)
        self.model.open(self.source)
        output = self.root / "saved.pdf"
        self.model.save(output)
        with pymupdf.open(output) as saved:
            self.assertEqual(self.labels(saved), LABELS)
            self.assertEqual(len(saved[0].get_images()), 1)
        self.assertFalse(self.model.dirty)
        self.assertFalse(self.model.can_undo)

    def test_split_supports_model_document_path_and_bytes(self):
        for kind in ("model", "document", "path", "bytes"):
            with self.subTest(source=kind):
                self.model.open(self.source)
                source = {"model": self.model, "document": self.model.doc,
                          "path": self.source, "bytes": self.original}[kind]
                outputs = split_pdf(source, [[2, 0]], self.root, f"split-{kind}")
                with pymupdf.open(outputs[0]) as saved:
                    self.assertEqual(self.labels(saved), [LABELS[2], LABELS[0]])
                    self.assertEqual(len(saved[1].get_images()), 1)
                self.assertEqual(self.labels(), LABELS)
                self.assertFalse(self.model.dirty)

    def test_compression_supports_model_document_path_and_bytes(self):
        for kind in ("model", "document", "path", "bytes"):
            with self.subTest(source=kind):
                self.model.open(self.source)
                source = {"model": self.model, "document": self.model.doc,
                          "path": self.source, "bytes": self.original}[kind]
                output = self.root / f"compressed-{kind}.pdf"
                result = compress_pdf(source, output, mode="images", dpi=72)
                self.assertEqual(result["output_size"], output.stat().st_size)
                if kind in {"model", "path", "bytes"}:
                    self.assertEqual(result["original_size"], len(self.original))
                with pymupdf.open(output) as saved:
                    self.assertEqual(self.labels(saved), LABELS)
                    self.assertEqual(len(saved[0].get_images()), 1)
                self.assertEqual(self.labels(), LABELS)
                self.assertFalse(self.model.dirty)

    def test_merge_and_file_insert_keep_page_order(self):
        output = self.root / "merged.pdf"
        merge_pdfs([self.source, self.source], output)
        with pymupdf.open(output) as saved:
            self.assertEqual(self.labels(saved), LABELS * 2)
        self.model.insert_files([self.source], 1)
        self.assertEqual(self.labels(), [LABELS[0], *LABELS, *LABELS[1:]])
        self.assertTrue(self.model.undo())
        self.assertEqual(self.labels(), LABELS)


if __name__ == "__main__":
    unittest.main()
