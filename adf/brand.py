"""XDF's public identity; storage identifiers remain stable across the rename."""

NAME = 'XDF'
ACCENT = '#f04436'
ACCENT_HOVER = '#d7372d'
ACCENT_TEXT = '#b92f26'
# Cool neutral interaction fills keep the signature color for focused emphasis.
ACCENT_SOFT = '#f0f3f8'
ACCENT_SELECTED = '#e2e7ee'
ACCENT_PDF = tuple(int(ACCENT[index:index + 2], 16) / 255 for index in (1, 3, 5))
