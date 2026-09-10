"""Page-local alignment geometry, independent of mouse and document state."""

from bisect import bisect_left, bisect_right
import math

from PySide6.QtCore import QRectF


class SnapIndex:
    def __init__(self, bounds, objects=()):
        self.bounds = QRectF(bounds)
        anchors = [set(), set()]
        for rect in (self.bounds, *objects):
            values = (rect.left(), rect.top(), rect.right(), rect.bottom())
            if not all(math.isfinite(value) for value in values):
                continue
            if rect.width() < 0 or rect.height() < 0:
                continue
            for axis, coordinates in enumerate(self.coordinates(rect)):
                low, high = ((bounds.left(), bounds.right()), (bounds.top(), bounds.bottom()))[axis]
                anchors[axis].update(value for value in coordinates if low <= value <= high)
        self.anchors = [sorted(values) for values in anchors]

    @staticmethod
    def coordinates(rect):
        return ((rect.left(), rect.center().x(), rect.right()),
                (rect.top(), rect.center().y(), rect.bottom()))

    def candidates(self, axis, values, tolerance):
        anchors = self.anchors[axis]
        matches = []
        for value in values:
            start = bisect_left(anchors, value - tolerance)
            end = bisect_right(anchors, value + tolerance)
            matches.extend((target - value, target) for target in anchors[start:end])
        return sorted(matches, key=lambda match: (abs(match[0]), match[1]))

    def guides(self, rect, resize=False):
        coordinates = ((rect.right(),), (rect.bottom(),)) if resize else self.coordinates(rect)
        return [(axis, target) for axis in (0, 1)
                for _, target in self.candidates(axis, coordinates[axis], 1e-5)]

    def snap(self, rect, tolerance, *, resize=False, ratio=None, minimum=(18., 10.)):
        """Snap within page bounds; ratio is height / width for image resizing.

        The caller supplies an already clamped rectangle. A ratio-constrained
        resize chooses one corner displacement, never distorting the image to
        satisfy two incompatible guides.
        """
        result = QRectF(rect)
        bounds = self.bounds
        if resize and ratio is not None:
            choices = []
            for axis, edge in ((0, rect.right()), (1, rect.bottom())):
                for delta, target in self.candidates(axis, (edge,), tolerance):
                    width = target - rect.left() if axis == 0 else (target - rect.top()) / ratio
                    height = width * ratio
                    distance = math.hypot(width - rect.width(), height - rect.height())
                    if (width >= minimum[0] and height >= minimum[1]
                            and rect.left() + width <= bounds.right() + 1e-7
                            and rect.top() + height <= bounds.bottom() + 1e-7
                            and distance <= tolerance):
                        choices.append((distance, width, height))
            if choices:
                _, width, height = min(choices)
                result.setWidth(width)
                result.setHeight(height)
        else:
            coordinates = ((rect.right(),), (rect.bottom(),)) if resize else self.coordinates(rect)
            for axis in (0, 1):
                for delta, _ in self.candidates(axis, coordinates[axis], tolerance):
                    candidate = QRectF(result)
                    if resize:
                        if axis == 0:
                            candidate.setWidth(result.width() + delta)
                        else:
                            candidate.setHeight(result.height() + delta)
                        if candidate.width() < minimum[0] or candidate.height() < minimum[1]:
                            continue
                    else:
                        candidate.translate(delta if axis == 0 else 0, delta if axis == 1 else 0)
                    if (candidate.left() >= bounds.left() - 1e-7
                            and candidate.top() >= bounds.top() - 1e-7
                            and candidate.right() <= bounds.right() + 1e-7
                            and candidate.bottom() <= bounds.bottom() + 1e-7):
                        result = candidate
                        break
        return result, self.guides(result, resize)
