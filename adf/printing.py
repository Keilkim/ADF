"""Page selection and physical print sizing, independent of printer drivers."""

from PySide6.QtCore import QRectF

from .document import parse_ranges


def print_pages(mode, page_count, current, selected=(), ranges=''):
    if mode == 'all':
        pages = list(range(page_count))
    elif mode == 'current':
        pages = [current]
    elif mode == 'selected':
        pages = sorted(set(selected))
    elif mode == 'range':
        pages = list(dict.fromkeys(page for group in parse_ranges(ranges, page_count) for page in group))
    else:
        raise ValueError('인쇄할 페이지를 선택해 주세요.')
    if not pages or any(page < 0 or page >= page_count for page in pages):
        raise ValueError('인쇄할 페이지를 확인해 주세요.')
    return pages


def print_rect(page_size, printable, resolution, mode='fit', percent=100):
    """Center a page in painter coordinates, preserving its physical aspect ratio."""
    width = page_size.width() * resolution / 72
    height = page_size.height() * resolution / 72
    fit = min(printable.width() / width, printable.height() / height)
    if mode == 'fit':
        factor = fit
    elif mode == 'shrink':
        factor = min(1, fit)
    elif mode == 'actual':
        factor = 1
    elif mode == 'custom' and percent > 0:
        factor = percent / 100
    else:
        raise ValueError('인쇄 배율을 확인해 주세요.')
    width, height = width * factor, height * factor
    return QRectF(printable.center().x() - width / 2, printable.center().y() - height / 2, width, height)
