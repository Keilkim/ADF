"""Physical page slots: the examples requested for left/right starts."""
import unittest
from adf.page_layout import spread_groups, page_side


class SpreadLayoutTests(unittest.TestCase):
    def test_six_pages_never_reverse_reading_order(self):
        self.assertEqual(spread_groups(6), [[0, 1], [2, 3], [4, 5]])
        self.assertEqual(spread_groups(6, True), [[None, 0], [1, 2], [3, 4], [5, None]])

    def test_empty_single_and_odd_page_counts_keep_the_physical_slot(self):
        for count, left, right in [
            (0, [], []), (1, [[0, None]], [[None, 0]]),
            (5, [[0, 1], [2, 3], [4, None]], [[None, 0], [1, 2], [3, 4]]),
        ]:
            self.assertEqual(spread_groups(count), left)
            self.assertEqual(spread_groups(count, True), right)

    def test_numbering_uses_the_same_physical_side_as_the_reader(self):
        for start_right in (False, True):
            for row in spread_groups(6, start_right):
                for slot, page in enumerate(row):
                    if page is not None:
                        self.assertEqual(page_side(page, start_right), ('left', 'right')[slot])
