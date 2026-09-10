"""Shared facing-page order for the reader and page-number previews."""


def spread_groups(page_count, start_right=False):
    """Physical left/right slots; None reserves space, never adds a PDF page."""
    if page_count <= 0:
        return []
    slots = ([None] if start_right else []) + list(range(page_count))
    if len(slots) % 2:
        slots.append(None)
    return [slots[i:i + 2] for i in range(0, len(slots), 2)]


def page_side(index, start_right=False):
    left = (index + int(start_right)) % 2 == 0
    return 'left' if left else 'right'


def numbering_position(position, index, *, mirror=False, anchor_page=0, start_right=False):
    vertical, horizontal = position.split('-')
    if mirror and horizontal != 'center' and page_side(index, start_right) != page_side(anchor_page, start_right):
        horizontal = 'right' if horizontal == 'left' else 'left'
    return f'{vertical}-{horizontal}'


def numbering_label(start, offset, count, *, prefix='', suffix='', digits=1, zero_pad=False):
    width = len(str(start + max(0, count - 1))) if zero_pad else digits
    return f'{prefix}{start + offset:0{width}d}{suffix}'
