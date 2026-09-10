"""Portable PDF ink objects and their editable geometry."""
import json
import math
import uuid
import pymupdf

KINDS = {'pencil': ('펜슬', 1.2, .95), 'marker': ('마카', 8.0, .28), 'brush': ('브러쉬', 4.0, 1.0)}
PREFIX = 'ADF_INK_1:'


def frame_for(paths, width):
    points = [p for path in paths for p in path]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [(min(xs)+max(xs))/2, (min(ys)+max(ys))/2,
            max(6, max(xs)-min(xs)+width), max(6, max(ys)-min(ys)+width), 0]


def factors(count, pressures):
    if count <= 2:
        return [max(.12, p) for p in pressures]
    return [max(.08, min(1, p) * min(1, .25+i/3, .25+(count-1-i)/3)) for i, p in enumerate(pressures)]


def read_ink(annotation):
    if annotation.type[0] != pymupdf.PDF_ANNOT_INK:
        return None
    paths = [[tuple(p) for p in path] for path in annotation.vertices or [] if path]
    if not paths:
        return None
    metadata = {}
    subject = annotation.info.get('subject', '')
    if subject.startswith(PREFIX):
        try:
            metadata = json.loads(subject[len(PREFIX):])
            if not isinstance(metadata, dict):
                metadata = {}
        except (ValueError, TypeError):
            pass
    kind = metadata.get('kind', 'pencil')
    if kind not in KINDS:
        kind = 'pencil'
    width = max(.2, float(annotation.border.get('width', 1.2)))
    color = annotation.colors.get('stroke') or (0, 0, 0)
    if len(color) != 3:
        color = (0, 0, 0)
    color = '#'+''.join(f'{max(0, min(255, round(c*255))):02x}' for c in color)
    frame = metadata.get('frame')
    if not (isinstance(frame, list) and len(frame) == 5 and
            all(isinstance(v, (float, int)) and math.isfinite(v) for v in frame) and frame[2] > 0 and frame[3] > 0):
        frame = frame_for(paths, width)
    pressures = metadata.get('pressures')
    if not (isinstance(pressures, list) and len(pressures) == len(paths) and
            all(isinstance(values, list) and len(values) == len(path) and
                all(isinstance(p, (float, int)) and math.isfinite(p) and 0 <= p <= 1 for p in values)
                for path, values in zip(paths, pressures))):
        pressures = [[.8]*len(path) for path in paths]
    weights = metadata.get('weights')
    if not (isinstance(weights, list) and len(weights) == len(paths) and
            all(isinstance(values, list) and len(values) == len(path) and
                all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in values)
                for path, values in zip(paths, weights))):
        weights = None
    return dict(paths=paths, color=color, width=width, kind=kind, pressures=pressures, weights=weights,
                frame=frame, uid=str(metadata.get('uid') or uuid.uuid4().hex))


def write_ink(page, paths, color, width, kind='pencil', pressures=None, frame=None, uid=None, weights=None,
              info=None, flags=None, opacity=None):
    if kind not in KINDS:
        raise ValueError('펜 종류가 올바르지 않습니다.')
    if pressures is None:
        pressures = [[.8]*len(path) for path in paths]
    if len(pressures) != len(paths) or any(len(p) != len(s) or
        any(not math.isfinite(v) or not 0 <= v <= 1 for v in p) for s, p in zip(paths, pressures)):
        raise ValueError('필압 정보가 올바르지 않습니다.')
    annotation = page.add_ink_annot(paths)
    annotation.set_border(width=width)
    rgb = tuple(int(color[i:i+2], 16)/255 for i in (1, 3, 5))
    annotation.set_colors(stroke=rgb)
    annotation.set_flags(pymupdf.PDF_ANNOT_IS_PRINT if flags is None else flags)
    if info is not None:
        annotation.set_info({key: info[key] for key in ('title', 'content', 'creationDate', 'modDate') if key in info})
    metadata = dict(kind=kind, pressures=pressures, frame=frame or frame_for(paths, width), uid=uid or uuid.uuid4().hex)
    if weights is not None:
        metadata['weights'] = weights
    annotation.set_info(subject=PREFIX+json.dumps(metadata, separators=(',', ':')))
    actual_opacity = KINDS[kind][2] if opacity is None else (opacity if opacity >= 0 else 1)
    annotation.update(opacity=actual_opacity)
    if kind == 'brush':
        # A standard PDF appearance stream preserves variable brush width in
        # other readers; InkList and metadata retain the editable points.
        rect = annotation.rect
        ap = int(page.parent.xref_get_key(annotation.xref, 'AP/N')[1].split()[0])
        lines = ['q', '1 J 1 j', ' '.join(f'{v:.6f}' for v in rgb)+' RG']
        for index, (path, values) in enumerate(zip(paths, pressures)):
            stroke_weights = weights[index] if weights is not None else factors(len(path), values)
            for i in range(len(path)-1):
                a, b = path[i], path[i+1]
                stroke_width = width*(stroke_weights[i]+stroke_weights[i+1])/2
                lines.append(f'{stroke_width:.5f} w {a[0]-rect.x0:.5f} {rect.y1-a[1]:.5f} m '
                             f'{b[0]-rect.x0:.5f} {rect.y1-b[1]:.5f} l S')
        lines.append('Q')
        page.parent.xref_set_key(ap, 'BBox', f'[0 0 {rect.width:.6f} {rect.height:.6f}]')
        page.parent.xref_set_key(ap, 'Matrix', '[1 0 0 1 0 0]')
        page.parent.update_stream(ap, '\n'.join(lines).encode('ascii'))
    return annotation.xref
