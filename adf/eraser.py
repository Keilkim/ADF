"""Subtract a swept circular eraser from ink polylines, retaining editable ink."""
import math

from .ink import factors, frame_for


def _band(value, delta, low, high):
    if abs(delta) < 1e-12:
        return (0., 1.) if low <= value <= high else None
    a, b = sorted(((low-value)/delta, (high-value)/delta))
    return max(0., a), min(1., b)


def _covered(a, b, start, end, radius):
    """Parameter intervals of AB inside the capsule around start/end."""
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx+dy*dy
    intervals = []
    for cx, cy in (start, end):
        ox, oy = a[0]-cx, a[1]-cy
        if length2 < 1e-16:
            if ox*ox+oy*oy <= radius*radius:
                return [(0., 1.)]
            continue
        dot = ox*dx+oy*dy
        discriminant = dot*dot-length2*(ox*ox+oy*oy-radius*radius)
        if discriminant >= 0:
            root = math.sqrt(discriminant)
            intervals.append((max(0., (-dot-root)/length2), min(1., (-dot+root)/length2)))
    ex, ey = end[0]-start[0], end[1]-start[1]
    length = math.hypot(ex, ey)
    if length > 1e-12:
        ux, uy = ex/length, ey/length
        ox, oy = a[0]-start[0], a[1]-start[1]
        along = _band(ox*ux+oy*uy, dx*ux+dy*uy, 0, length)
        across = _band(-ox*uy+oy*ux, -dx*uy+dy*ux, -radius, radius)
        if along and across:
            intervals.append((max(along[0], across[0]), min(along[1], across[1])))
    merged = []
    for lo, hi in sorted((lo, hi) for lo, hi in intervals if hi-lo > 1e-10):
        if merged and lo <= merged[-1][1]+1e-10:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def erase_sweep(data, start, end, radius):
    """Return replacement data, or None when this sweep misses the ink.

    Exact capsule intersections prevent gaps even with sparse/fast pointer
    input. Stored brush weights keep surviving ends from being tapered again.
    """
    paths, pressures, weights = [], [], []
    changed = False
    for index, (stroke, values) in enumerate(zip(data['paths'], data['pressures'])):
        brush = data['kind'] == 'brush'
        original_weights = data.get('weights')
        widths = (original_weights[index] if original_weights is not None else factors(len(stroke), values)) if brush else [1.]*len(stroke)
        if len(stroke) == 1:
            if _covered(stroke[0], stroke[0], start, end, radius+data['width']*widths[0]/2):
                changed = True
            else:
                paths.append(stroke[:]); pressures.append(values[:]); weights.append(widths[:])
            continue
        current, current_pressure, current_weight = [], [], []

        def flush():
            nonlocal current, current_pressure, current_weight
            if current:
                paths.append(current); pressures.append(current_pressure); weights.append(current_weight)
            current, current_pressure, current_weight = [], [], []

        for i, (a, b) in enumerate(zip(stroke, stroke[1:])):
            covered = _covered(a, b, start, end, radius+data['width']*(widths[i]+widths[i+1])/4)
            changed |= bool(covered)
            kept, last = [], 0.
            for lo, hi in covered:
                if lo > last+1e-10:
                    kept.append((last, lo))
                last = hi
            if last < 1.-1e-10:
                kept.append((last, 1.))
            if not kept:
                flush()
            for lo, hi in kept:
                if lo > 1e-10 or brush:
                    flush()
                for t in (lo, hi):
                    point = (a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t)
                    if not current or math.dist(point, current[-1]) > 1e-9:
                        current.append(point)
                        current_pressure.append(values[i]+(values[i+1]-values[i])*t)
                        # Each rendered brush segment has a constant width.
                        current_weight.append((widths[i]+widths[i+1])/2 if brush else 1.)
                if hi < 1.-1e-10 or brush:
                    flush()
        flush()
    if not changed:
        return None
    return dict(data, paths=paths, pressures=pressures,
                weights=weights if data['kind'] == 'brush' else None,
                frame=frame_for(paths, data['width']) if paths else data['frame'])


def erasable(annotation):
    import pymupdf
    return (annotation.type[0] == pymupdf.PDF_ANNOT_INK and
            not annotation.flags & (pymupdf.PDF_ANNOT_IS_LOCKED | pymupdf.PDF_ANNOT_IS_READ_ONLY |
                                    pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_INVISIBLE |
                                    pymupdf.PDF_ANNOT_IS_NO_VIEW))
