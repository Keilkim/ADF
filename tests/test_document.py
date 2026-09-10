"""Integration checks use real PDFs, including encryption and shared images."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import pymupdf

from adf.document import PdfDocument, PasswordRequired, compress_pdf, merge_pdfs, parse_ranges, replace_page_text, split_pdf
from adf.text_groups import text_group_at


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="adf-test-")
        self.root = Path(self.folder.name)
        self.source = self.make_pdf("source.pdf", ["First page", "Second page", "Third page"])
        self.original = self.source.read_bytes()
        self.model = PdfDocument()
        self.model.open(self.source)

    def tearDown(self):
        self.model.close()
        self.folder.cleanup()

    def make_pdf(self, name, labels):
        path = self.root / name
        with pymupdf.open() as doc:
            for label in labels:
                doc.new_page(width=300, height=400).insert_text((30, 50), label)
            doc.save(path)
        return path

    def labels(self):
        return [page.get_text().strip() for page in self.model.doc]

    def test_rotate_reorder_delete_and_undo_preserve_original(self):
        self.model.rotate([0, 2], 90)
        self.model.reorder([2, 0, 1])
        self.model.delete([1])
        self.assertEqual(self.labels(), ["Third page", "Second page"])
        self.assertEqual(self.model.doc[0].rotation, 90)
        self.assertTrue(self.model.dirty)
        for _ in range(3):
            self.assertTrue(self.model.undo())
        self.assertEqual(self.labels(), ["First page", "Second page", "Third page"])
        self.assertFalse(self.model.dirty)
        self.assertTrue(self.model.redo())
        self.assertEqual(self.model.doc[2].rotation, 90)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_save_records_state_and_undo_redo_tracks_saved_state(self):
        self.model.rotate([0], 90)
        output = self.root / "saved.pdf"
        self.model.save(output)
        self.assertFalse(self.model.dirty)
        self.model.undo()
        self.assertTrue(self.model.dirty)
        self.model.redo()
        self.assertFalse(self.model.dirty)
        with pymupdf.open(output) as saved:
            self.assertEqual(saved[0].rotation, 90)
            self.assertEqual(saved.page_count, 3)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_confirmed_save_to_source_works_on_windows(self):
        self.model.rotate([1], 180)
        self.model.save(self.source)
        with pymupdf.open(self.source) as saved:
            self.assertEqual(saved[1].rotation, 180)
        self.model.undo()
        self.assertEqual(self.model.doc[1].rotation, 0)

    def test_atomic_save_failure_keeps_existing_output_and_dirty_state(self):
        output = self.make_pdf("existing.pdf", ["Keep me"])
        before = output.read_bytes()
        self.model.rotate([0], 90)
        with patch("adf.document.os.replace", side_effect=OSError("simulated filesystem error")):
            with self.assertRaises(OSError):
                self.model.save(output)
        self.assertEqual(output.read_bytes(), before)
        self.assertTrue(self.model.dirty)
        self.assertEqual(list(self.root.glob(".adf-*")), [])

    def test_failed_multi_page_edit_rolls_back_and_keeps_history(self):
        self.model.rotate([0], 90)
        before_revision = self.model.revision
        from adf.document import number_page
        calls = 0
        def fail_second(page, text, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated write failure")
            return number_page(page, text, **kwargs)
        with patch("adf.document.number_page", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.model.number_pages([0, 1])
        self.assertEqual(self.labels(), ["First page", "Second page", "Third page"])
        self.assertEqual(self.model.revision, before_revision)
        self.assertTrue(self.model.undo())
        self.assertFalse(self.model.dirty)
        self.assertEqual(self.model.doc[0].rotation, 0)

    def test_new_edit_after_undo_discards_redo(self):
        self.model.rotate([0], 90)
        self.model.undo()
        self.model.delete([2])
        self.assertFalse(self.model.can_redo)
        self.assertEqual(self.model.page_count, 2)

    def test_history_is_disk_backed_bounded_and_removed_on_close(self):
        with patch("adf.document.HISTORY_STEPS", 2):
            for _ in range(4):
                self.model.rotate([0], 90)
        self.assertEqual(len(self.model._undo), 2)
        history = Path(self.model._history_dir.name)
        self.assertTrue(history.is_dir())
        self.model.close()
        self.assertFalse(history.exists())

    def test_invalid_operations_do_not_modify_document(self):
        operations = [lambda: self.model.delete([0, 1, 2]), lambda: self.model.rotate([-1], 90),
                      lambda: self.model.rotate([0], 45), lambda: self.model.reorder([0, 0, 1]),
                      lambda: self.model.reorder([2, 0]), lambda: self.model.insert_blank(-1),
                      lambda: self.model.insert_blank(1, width=-5), lambda: self.model.delete([])]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()
                self.assertFalse(self.model.dirty)
                self.assertFalse(self.model.can_undo)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_blank_insertion_and_replacement_with_multiple_pages(self):
        self.model.insert_blank(1)
        self.assertEqual(self.model.page_count, 4)
        self.assertEqual(tuple(self.model.doc[1].rect), (0, 0, 300, 400))
        replacement = self.make_pdf("replacement.pdf", ["A", "B"])
        self.model.replace(1, replacement, [1, 0])
        self.assertEqual(self.labels(), ["First page", "B", "A", "Second page", "Third page"])
        self.model.undo()
        self.assertEqual(self.labels(), ["First page", "", "Second page", "Third page"])
        self.model.insert_pdf(replacement, 0)
        self.assertEqual(self.labels()[:2], ["A", "B"])

    def test_multiple_pdf_addition_is_ordered_selected_and_one_undo_step(self):
        first = self.make_pdf("add-first.pdf", ["A1", "A2"])
        second = self.make_pdf("add-second.pdf", ["B1", "B2"])
        self.model.insert_pdfs([first, second], 1, selected_ranges=[[1], [0]])
        self.assertEqual(self.labels(), ["First page", "A2", "B1", "Second page", "Third page"])
        self.assertTrue(self.model.dirty)
        self.assertTrue(self.model.undo())
        self.assertEqual(self.labels(), ["First page", "Second page", "Third page"])
        self.assertFalse(self.model.dirty)
        self.assertFalse(self.model.can_undo)

    def test_replace_only_page_never_drops_all_pages(self):
        single = self.make_pdf("single.pdf", ["Old"])
        self.model.open(single)
        self.model.replace(0, self.source, [1, 2])
        self.assertEqual(self.labels(), ["Second page", "Third page"])

    def test_mixed_page_import_validates_all_inputs_before_changing_document(self):
        image = self.root / 'image.png'
        Image.new('RGB', (100, 80), 'red').save(image)
        with self.assertRaises(FileNotFoundError):
            self.model.insert_files([image, self.root / 'missing.pdf'], 1)
        self.assertEqual(self.labels(), ['First page', 'Second page', 'Third page'])
        self.assertFalse(self.model.dirty)
        self.assertFalse(self.model.can_undo)
        for index in (-1, 4, True):
            with self.assertRaises(ValueError):
                self.model.insert_files([image], index)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_imported_image_page_respects_exif_orientation_and_saved_dimensions(self):
        image = self.root / 'rotated.jpg'
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (120, 80), 'blue').save(image, exif=exif)
        self.model.insert_files([image], 1)
        page = self.model.doc[1]
        self.assertEqual(tuple(page.rect), (0, 0, 60, 90))
        self.assertEqual(len(page.get_images()), 1)
        output = self.root / 'image-page.pdf'
        self.model.save(output)
        with pymupdf.open(output) as saved:
            self.assertEqual(saved.page_count, 4)
            self.assertEqual(tuple(saved[1].rect), (0, 0, 60, 90))
            self.assertGreater(saved[1].get_pixmap().pixel(30,45)[2], 240)
        self.model.undo()
        self.assertEqual(self.labels(), ['First page', 'Second page', 'Third page'])

    def test_cmyk_jpeg_can_be_inserted_as_a_page(self):
        path = self.root / 'print-image.jpg'
        Image.new('CMYK', (80, 60), (0, 255, 255, 0)).save(path)
        self.model.insert_files([path], 1)
        self.assertEqual(self.model.page_count, 4)
        pixel = self.model.doc[1].get_pixmap().pixel(20, 20)
        self.assertGreater(pixel[0], 240)
        self.assertLess(pixel[1], 10)

    def test_failed_open_retains_previous_document(self):
        with self.assertRaises(FileNotFoundError):
            self.model.open(self.root / "missing.pdf")
        self.assertEqual(self.model.page_count, 3)
        self.assertEqual(self.model.path, str(self.source.resolve()))

    def test_korean_numbering_round_trips_in_standard_pdf(self):
        self.model.rotate([1], 90)
        self.model.number_pages([0, 1, 2], start=7, digits=3, prefix="쪽 ", suffix=" / 끝")
        output = self.root / "numbered.pdf"
        self.model.save(output)
        with pymupdf.open(output) as doc:
            for index, page in enumerate(doc):
                self.assertIn(f"쪽 {7 + index:03d} / 끝", page.get_text())
                match = page.search_for(f"{7 + index:03d}")[0] * page.rotation_matrix
                self.assertGreater(match.y0, page.rect.height / 2)
                self.assertTrue(page.rect.contains(match))

    def test_range_parser_supports_groups_and_korean_end(self):
        self.assertEqual(parse_ranges("1-3, 7, 12-끝", 14), [[0, 1, 2], [6], [11, 12, 13]])
        self.assertEqual(parse_ranges("2 ~ 4; 끝", 5), [[1, 2, 3], [4]])
        self.assertEqual(parse_ranges("전체", 2), [[0, 1]])
        for invalid in ("", "0", "4", "3-2", "1,", "1-99", "a", "-1", "1--2"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_ranges(invalid, 3)

    def test_split_extracts_groups_and_remaining_without_overwrite(self):
        outputs = split_pdf(self.model, [[2], [0]], self.root, "분리", include_remaining=True)
        self.assertEqual([Path(path).name for path in outputs], ["분리_p003.pdf", "분리_p001.pdf", "분리_나머지.pdf"])
        for path, expected in zip(outputs, ["Third page", "First page", "Second page"]):
            with pymupdf.open(path) as doc:
                self.assertEqual(doc.page_count, 1)
                self.assertEqual(doc[0].get_text().strip(), expected)
        before = Path(outputs[0]).read_bytes()
        with self.assertRaises(FileExistsError):
            split_pdf(self.model, [[2]], self.root, "분리")
        self.assertEqual(Path(outputs[0]).read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_split_failure_rolls_back_all_created_files(self):
        from adf.document import _atomic_save
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated export failure")
            return _atomic_save(*args, **kwargs)
        with patch("adf.document._atomic_save", side_effect=fail_second):
            with self.assertRaises(OSError):
                split_pdf(self.model, [[0], [1]], self.root, "failed")
        self.assertEqual(list(self.root.glob("failed*.pdf")), [])

    def test_merge_preserves_order_and_does_not_overwrite_sources(self):
        other = self.make_pdf("other.pdf", ["End"])
        output = self.root / "merged.pdf"
        merge_pdfs([other, self.source], output)
        with pymupdf.open(output) as doc:
            self.assertEqual([page.get_text().strip() for page in doc],
                             ["End", "First page", "Second page", "Third page"])
            self.assertEqual([item[2] for item in doc.get_toc()], [1, 2])
        with self.assertRaises(ValueError):
            merge_pdfs([self.source, other], self.source)
        with self.assertRaises(FileExistsError):
            merge_pdfs([self.source], output)
        self.assertEqual(self.source.read_bytes(), self.original)

    def encrypted(self, *, user="user", permissions=pymupdf.PDF_PERM_PRINT):
        path = self.root / "locked.pdf"
        with pymupdf.open(self.source) as doc:
            doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user,
                     owner_pw="owner", permissions=permissions)
        return path

    def test_password_permissions_and_owner_authentication(self):
        locked = self.encrypted()
        with self.assertRaises(PasswordRequired):
            self.model.open(locked)
        with self.assertRaises(PasswordRequired) as error:
            self.model.open(locked, "wrong")
        self.assertTrue(error.exception.incorrect)
        self.model.open(locked, "user")
        self.assertFalse(self.model.editable)
        with self.assertRaises(PermissionError):
            self.model.rotate([0], 90)
        with self.assertRaises(PermissionError):
            split_pdf(self.model, [[0]], self.root, "restricted")
        with self.assertRaises(PermissionError):
            merge_pdfs([locked], self.root / "restricted.pdf", passwords={str(locked): "user"})
        self.model.open(locked, "owner")
        self.assertTrue(self.model.editable)
        self.model.rotate([0], 90)
        self.model.undo()
        self.model.redo()
        saved = self.root / "protected-output.pdf"
        self.model.save(saved)
        with pymupdf.open(saved) as doc:
            self.assertTrue(doc.needs_pass)
            self.assertTrue(doc.authenticate("user"))
            self.assertFalse(doc.permissions & pymupdf.PDF_PERM_MODIFY)
            self.assertEqual(doc[0].rotation, 90)

    def test_owner_restrictions_without_open_password_are_respected(self):
        locked = self.encrypted(user="")
        self.model.open(locked)
        self.assertFalse(self.model.editable)
        with self.assertRaises(PermissionError):
            self.model.delete([0])
        self.model.open(locked, "owner")
        self.assertTrue(self.model.editable)

    def test_split_and_compression_preserve_password_security(self):
        locked = self.encrypted()
        self.model.open(locked, "owner")
        paths = split_pdf(self.model, [[0]], self.root, "protected-split")
        for mode in ("images", "raster"):
            output = self.root / f"protected-{mode}.pdf"
            compress_pdf(self.model, output, mode=mode, dpi=72)
            paths.append(str(output))
        for output in paths:
            with pymupdf.open(output) as doc:
                self.assertTrue(doc.needs_pass)
                self.assertTrue(doc.authenticate("user"))
                self.assertFalse(doc.permissions & pymupdf.PDF_PERM_MODIFY)

    def image_bytes(self, color="red", size=(80, 80)):
        stream = io.BytesIO()
        Image.new("RGBA", size, color).save(stream, format="PNG")
        return stream.getvalue()

    def test_image_delete_is_local_even_when_image_is_shared_across_pages(self):
        path = self.root / "shared.pdf"
        with pymupdf.open(self.source) as doc:
            xref = doc[0].insert_image((80, 80, 180, 180), stream=self.image_bytes())
            doc[1].insert_image((80, 80, 180, 180), xref=xref)
            doc.save(path)
        self.model.open(path)
        before_second = self.model.doc[1].get_pixmap().samples
        xref = self.model.doc[0].get_images()[0][0]
        self.model.remove_image(0, xref)
        self.assertEqual(self.model.doc[1].get_pixmap().samples, before_second)
        self.assertEqual(self.model.doc[0].get_pixmap().pixel(120, 120), (255, 255, 255))
        self.assertEqual(self.model.doc[0].get_text().strip(), "First page")
        self.model.undo()
        self.assertEqual(self.model.doc[0].get_pixmap().pixel(120, 120), (255, 0, 0))

    def test_transparent_image_insertion_and_undo(self):
        xref = self.model.add_image(0, (100, 100, 150, 150), self.image_bytes((255, 0, 0, 128)))
        self.assertGreater(xref, 0)
        pixel = self.model.doc[0].get_pixmap().pixel(120, 120)
        self.assertEqual(pixel[0], 255)
        self.assertTrue(110 < pixel[1] < 145)
        self.model.undo()
        self.assertEqual(self.model.doc[0].get_pixmap().pixel(120, 120), (255, 255, 255))

    def test_deleting_image_does_not_remove_another_with_same_colour_and_different_mask(self):
        first = self.model.add_image(0, (30, 100, 80, 150), self.image_bytes((255, 0, 0, 128)))
        self.model.add_image(0, (130, 100, 180, 150), self.image_bytes((255, 0, 0, 64)))
        before_second = self.model.doc[0].get_pixmap().pixel(150, 120)
        self.model.remove_image(0, first)
        self.assertEqual(self.model.doc[0].get_pixmap().pixel(50, 120), (255, 255, 255))
        after_second = self.model.doc[0].get_pixmap().pixel(150, 120)
        # MuPDF can round alpha compositing by one channel value after grafting
        # page resources; the second stamp must remain visually unchanged.
        self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(after_second, before_second)))

    def test_upright_image_placement_compensates_rotated_page(self):
        image = Image.new('RGB',(100,50),'red')
        image.paste('blue',(50,0,100,50))
        stream = io.BytesIO()
        image.save(stream,format='PNG')
        for rotation in (90,180,270):
            self.model.rotate([0],rotation-self.model.doc[0].rotation)
            page = self.model.doc[0]
            rect = pymupdf.Rect(100,100,200,150)*page.derotation_matrix
            self.model.add_image(0,rect,stream.getvalue(),rotate=rotation)
            pixmap = self.model.doc[0].get_pixmap()
            self.assertEqual(pixmap.pixel(110,125),(255,0,0))
            self.assertEqual(pixmap.pixel(190,125),(0,0,255))
            self.model.undo()

    def test_text_replacement_removes_old_text_and_preserves_background_artwork(self):
        path = self.root / "art.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=400)
            page.draw_rect((20, 20, 220, 90), color=(0, 0, 1), fill=(.8, .8, 1))
            page.insert_image((30, 25, 55, 70), stream=self.image_bytes("green"))
            page.insert_text((60, 50), "Old text", fontsize=14)
            doc.save(path)
        self.model.open(path)
        rect = self.model.doc[0].search_for("Old text")[0]
        before = self.model.doc[0].get_pixmap().pixel(40, 40)
        drawings = len(self.model.doc[0].get_drawings())
        self.model.replace_text(0, rect, "New text", font_size=14)
        page = self.model.doc[0]
        self.assertNotIn("Old text", page.get_text())
        self.assertIn("New text", page.get_text())
        self.assertEqual(page.get_pixmap().pixel(40, 40), before)
        self.assertEqual(len(page.get_drawings()), drawings)
        self.model.undo()
        self.assertIn("Old text", self.model.doc[0].get_text())

    def test_text_replacement_can_move_to_a_resized_target_before_final_save(self):
        source = self.model.doc[0].search_for("First page")[0]
        target = pymupdf.Rect(120, 180, 260, 215)
        self.model.replace_text(0, source, "Moved text", font_size=18, target_rect=target)
        page = self.model.doc[0]
        self.assertNotIn("First page", page.get_text())
        found = page.search_for("Moved text")
        self.assertEqual(len(found), 1)
        self.assertTrue(target.contains(found[0]))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_paragraph_replacement_preserves_neighbor_inside_enclosing_box_and_artwork(self):
        path = self.root / "paragraph-art.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=400)
            page.draw_rect((20, 20, 370, 140), color=(0, 0, 1), fill=(.8, .8, 1))
            page.insert_image((270, 25, 310, 60), stream=self.image_bytes("green"))
            page.insert_text((40, 75), "This first line stretches across the page\nShort ending.", fontsize=12)
            page.insert_text((190, 91.5), "KEEP THIS", fontsize=12)
            doc.save(path)
        self.model.open(path)
        before_disk = path.read_bytes()
        page = self.model.doc[0]
        hit = page.search_for("first")[0]
        group = text_group_at(page, (hit.tl + hit.br) / 2)
        self.assertEqual(group["text"], "This first line stretches across the page\nShort ending.")
        self.assertTrue(pymupdf.Rect(group["bbox"]).intersects(page.search_for("KEEP THIS")[0]))
        before_pixel = page.get_pixmap().pixel(280, 40)
        before_drawings = page.get_drawings()
        target = (40, 170, 330, 250)
        self.model.replace_text(0, group["bbox"], "Replacement first line\nReplacement second line.",
                                font_size=12, target_rect=target, source_rects=group["source_rects"],
                                lineheight=group["lineheight"])
        page = self.model.doc[0]
        self.assertNotIn("stretches", page.get_text())
        self.assertNotIn("Short ending", page.get_text())
        self.assertIn("KEEP THIS", page.get_text())
        self.assertIn("Replacement first line\nReplacement second line.", page.get_text())
        self.assertEqual(page.get_pixmap().pixel(280, 40), before_pixel)
        self.assertEqual(len(page.get_drawings()), len(before_drawings))
        self.assertEqual(path.read_bytes(), before_disk)
        self.assertTrue(self.model.undo())
        self.assertIn("stretches", self.model.doc[0].get_text())
        self.assertNotIn("Replacement", self.model.doc[0].get_text())
        self.assertFalse(self.model.can_undo)
        self.assertFalse(self.model.dirty)

    def test_multiline_text_wraps_fits_inside_target_and_round_trips_korean(self):
        source = self.model.doc[0].search_for("First page")[0]
        target = pymupdf.Rect(30, 100, 270, 200)
        self.model.replace_text(0, source, "줄바꿈을 포함한 문장입니다.\n둘째 줄도 함께 수정합니다.",
                                font_size=14, target_rect=target)
        page = self.model.doc[0]
        self.assertIn("줄바꿈을 포함한 문장입니다.", page.get_text())
        self.assertIn("둘째 줄도 함께 수정합니다.", page.get_text())
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                self.assertTrue((target + (-.01, -.01, .01, .01)).contains(pymupdf.Rect(line["bbox"])), str(line["bbox"]))
        output = self.root / "paragraph-save.pdf"
        self.model.save(output)
        with pymupdf.open(output) as saved:
            self.assertIn("둘째 줄도 함께 수정합니다.", saved[0].get_text())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_long_paragraph_auto_wraps_and_fits_without_clipping(self):
        source = self.model.doc[0].search_for("First page")[0]
        target = pymupdf.Rect(30, 100, 160, 160)
        text = "A long paragraph should wrap to the selected width and remain entirely visible."
        self.model.replace_text(0, source, text, font_size=20, target_rect=target)
        page = self.model.doc[0]
        self.assertEqual(" ".join(page.get_text().split()), text)
        lines = [line for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])]
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertTrue((target + (-.01, -.01, .01, .01)).contains(pymupdf.Rect(line["bbox"])))

    def test_unfittable_paragraph_keeps_document_and_undo_history(self):
        self.model.rotate([1], 90)
        source = self.model.doc[0].search_for("First page")[0]
        before_revision = self.model.revision
        with self.assertRaises(ValueError):
            self.model.replace_text(0, source, "Cannot fit\nEven at one point", target_rect=(30, 100, 31, 101))
        self.assertEqual(self.labels(), ["First page", "Second page", "Third page"])
        self.assertEqual(self.model.revision, before_revision)
        self.assertTrue(self.model.undo())
        self.assertFalse(self.model.dirty)
        self.assertFalse(self.model.can_undo)

    def test_multiline_insertion_failure_rolls_back_all_removed_and_inserted_lines(self):
        source = self.model.doc[0].search_for("First page")[0]
        original_insert = pymupdf.Page.insert_text
        calls = 0

        def fail_second(page, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated second line insertion failure")
            return original_insert(page, *args, **kwargs)

        with patch.object(pymupdf.Page, "insert_text", fail_second), self.assertRaises(RuntimeError):
            self.model.replace_text(0, source, "Line one\nLine two", target_rect=(30, 100, 230, 200))
        self.assertEqual(self.labels(), ["First page", "Second page", "Third page"])
        self.assertFalse(self.model.dirty)
        self.assertFalse(self.model.can_undo)

    def test_disposable_preview_matches_committed_multiline_page_without_changing_model(self):
        source = self.model.doc[0].search_for("First page")[0]
        options = {"font_size": 17, "target_rect": (30, 100, 170, 185), "color": (.2, .3, .8)}
        text = "Preview and final page\nshare the same wrapping and placement."
        with pymupdf.open() as preview:
            preview.insert_pdf(self.model.doc, from_page=0, to_page=0)
            replace_page_text(preview[0], source, text, **options)
            preview_pixels = preview[0].get_pixmap().samples
            self.assertEqual(self.labels()[0], "First page")
            self.assertFalse(self.model.dirty)
            self.assertFalse(self.model.can_undo)
        self.model.replace_text(0, source, text, **options)
        self.assertEqual(self.model.doc[0].get_pixmap().samples, preview_pixels)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_korean_and_latin_fallback_font_wraps_to_actual_pdf_advances(self):
        source = self.model.doc[0].search_for("First page")[0]
        target = pymupdf.Rect(30, 100, 150, 180)
        self.model.replace_text(0, source, "한글과 English 단어를 함께 입력합니다.",
                                font_size=16, target_rect=target)
        lines = [line for block in self.model.doc[0].get_text("dict")["blocks"]
                 for line in block.get("lines", [])]
        self.assertGreater(len(lines), 1)
        self.assertIn("English", self.model.doc[0].get_text().replace("\n", ""))
        for line in lines:
            self.assertTrue((target + (-.01, -.01, .01, .01)).contains(pymupdf.Rect(line["bbox"])))

    def test_non_fitting_fixed_size_and_outside_target_never_remove_original_text(self):
        source = self.model.doc[0].search_for("First page")[0]
        for kwargs in ({"fit": False, "target_rect": (30, 100, 50, 110)},
                       {"target_rect": (-30, 100, 160, 170)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.model.replace_text(0, source, "Paragraph\nwith two lines", font_size=20, **kwargs)
        self.assertEqual(self.labels(), ["First page", "Second page", "Third page"])
        self.assertFalse(self.model.dirty)

    def test_compression_retains_search_in_images_mode_and_rasterizes_explicitly(self):
        for mode in ("images", "raster"):
            output = self.root / f"{mode}.pdf"
            stats = compress_pdf(self.model, output, mode=mode, dpi=72, target_mb=.0001)
            self.assertEqual(stats["output_size"], output.stat().st_size)
            self.assertFalse(stats["target_met"])
            with pymupdf.open(output) as doc:
                self.assertEqual(doc.page_count, 3)
                self.assertEqual(bool(doc[0].search_for("First page")), mode == "images")
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_search_returns_page_and_rectangle(self):
        matches = self.model.search("Second")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], 1)
        self.assertIsInstance(matches[0][1], pymupdf.Rect)
        self.assertEqual(self.model.search("  "), [])


if __name__ == "__main__":
    unittest.main()
