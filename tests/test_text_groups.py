"""Paragraph hit-testing against real extracted PDF text and drawing rules."""

import unittest

import pymupdf

from adf.text_groups import text_group_at


class TextGroupTests(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=640, height=700)

    def tearDown(self):
        self.doc.close()

    def group(self, needle):
        rect = self.page.search_for(needle)[0]
        return text_group_at(self.page, (rect.tl + rect.br) / 2)

    def test_click_any_wrapped_line_selects_entire_paragraph(self):
        text = "A sentence that wraps onto\nmore than one visual line\nends in this third line."
        self.page.insert_text((40, 70), text, fontsize=12)
        for word in ("sentence", "visual", "third"):
            with self.subTest(word=word):
                group = self.group(word)
                self.assertEqual(group["text"], text)
                self.assertGreater(group["lineheight"], 1)
                self.assertTrue(group["source_rects"])

    def test_mixed_styles_and_separate_drawing_operations_form_one_paragraph(self):
        self.page.insert_text((40, 70), "Normal ", fontsize=12)
        offset = pymupdf.get_text_length("Normal ", fontsize=12)
        self.page.insert_text((40 + offset, 70), "bold phrase", fontsize=12, fontname="hebo", color=(1, 0, 0))
        self.page.insert_text((40, 87), "and a wrapped continuation.", fontsize=12)
        self.assertEqual(self.group("bold")["text"], "Normal bold phrase\nand a wrapped continuation.")

    def test_adjacent_columns_remain_independent(self):
        left = "Left column sentence begins here\nand carries on in its own column."
        right = "Right column is a separate paragraph\nand must never join the left column."
        self.page.insert_text((40, 70), left, fontsize=12)
        self.page.insert_text((335, 70), right, fontsize=12)
        self.assertEqual(self.group("Left")["text"], left)
        self.assertEqual(self.group("Right")["text"], right)

    def test_blank_line_separates_paragraphs(self):
        self.page.insert_text((40, 70), "First paragraph\nwraps once.", fontsize=12)
        self.page.insert_text((40, 118), "Second paragraph\nis independent.", fontsize=12)
        self.assertEqual(self.group("First")["text"], "First paragraph\nwraps once.")
        self.assertEqual(self.group("Second")["text"], "Second paragraph\nis independent.")

    def test_heading_and_new_indentation_are_boundaries(self):
        self.page.insert_text((40, 50), "Heading", fontsize=20)
        self.page.insert_text((40, 72), "Body starts here\nand wraps here.", fontsize=12)
        self.page.insert_text((64, 105), "Indented new paragraph", fontsize=12)
        self.assertEqual(self.group("Body")["text"], "Body starts here\nand wraps here.")
        self.assertEqual(self.group("Heading")["text"], "Heading")

    def test_table_rules_separate_rows_and_columns(self):
        for y in (48, 78, 108):
            self.page.draw_line((30, y), (400, y))
        self.page.draw_line((210, 48), (210, 108))
        for point, text in [((40, 69), "First row left"), ((222, 69), "First row right"),
                            ((40, 95), "Second row left"), ((222, 95), "Second row right")]:
            self.page.insert_text(point, text, fontsize=16)
        self.assertEqual(self.group("First row left")["text"], "First row left")
        self.assertEqual(self.group("Second row right")["text"], "Second row right")

    def test_borderless_short_table_cells_are_separate(self):
        for point, text in [((40, 70), "Apples"), ((200, 70), "10"),
                            ((40, 87), "Oranges"), ((200, 87), "20")]:
            self.page.insert_text(point, text, fontsize=12)
        self.assertEqual(self.group("Apples")["text"], "Apples")
        self.assertEqual(self.group("Oranges")["text"], "Oranges")

    def test_single_span_with_a_column_sized_space_is_split(self):
        self.page.insert_text((40, 70), "Left          Right", fontsize=12)
        self.assertEqual(self.group("Left")["text"], "Left")
        self.assertEqual(self.group("Right")["text"], "Right")

    def test_new_bullet_starts_an_independent_group(self):
        self.page.insert_text((40, 70), "1. First list item\ncontinues here\n2. Second item", fontsize=12)
        self.assertEqual(self.group("First")["text"], "1. First list item\ncontinues here")
        self.assertEqual(self.group("Second")["text"], "2. Second item")

    def test_hanging_list_indent_keeps_wrapped_continuation(self):
        self.page.insert_text((40, 70), "1. This list item wraps", fontsize=12)
        self.page.insert_text((55, 87), "under its text.", fontsize=12)
        self.page.insert_text((40, 104), "2. The next list item", fontsize=12)
        self.assertEqual(self.group("wraps")["text"], "1. This list item wraps\nunder its text.")
        self.assertEqual(self.group("next")["text"], "2. The next list item")

    def test_rotated_page_uses_unrotated_text_coordinates(self):
        self.page.insert_text((40, 70), "Two wrapped\nlines here", fontsize=12)
        self.page.set_rotation(90)
        self.assertEqual(self.group("here")["text"], "Two wrapped\nlines here")

    def test_empty_space_and_image_only_page_have_no_text_selection(self):
        self.assertIsNone(text_group_at(self.page, (50, 50)))
        self.page.draw_rect((20, 20, 80, 80), fill=(0, 0, 1))
        self.assertIsNone(text_group_at(self.page, (50, 50)))
        self.page.insert_text((40, 170), "Text lower down")
        self.assertIsNone(text_group_at(self.page, (50, 50)))


if __name__ == "__main__":
    unittest.main()
