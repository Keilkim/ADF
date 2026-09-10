"""Read-only PDF comparison, run in a separate process from the Qt renderer.

Page alignment uses unchanged anchors and sequence similarity in the gaps.
Text changes and rendered artwork changes are both retained. Coordinates in
the result are in displayed (rotated) page points, not raster pixels.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import sys

import pymupdf
from PIL import Image, ImageChops

from .document import _open_pdf, _require_permission


def _raster(page, scale):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def _fingerprint(page):
    text = ' '.join(page.get_text(sort=True).split())
    thumbnail = _raster(page, 128 / max(page.rect.width, page.rect.height)).convert('L')
    thumbnail = thumbnail.resize((64, 64))
    pixels = thumbnail.tobytes()
    size = (round(page.rect.width, 2), round(page.rect.height, 2))
    key = hashlib.sha256(repr(size).encode() + text.encode() + pixels).digest()
    return {'key': key, 'text': text[:20000], 'tokens': set(text.split()), 'pixels': pixels}


def _similarity(left, right):
    if left['key'] == right['key']:
        return 1.0
    pixel_distance = sum(abs(a-b) for a, b in zip(left['pixels'], right['pixels'])) / (255 * 4096)
    visual = max(0., 1 - pixel_distance * 8)
    if left['text'] or right['text']:
        union = left['tokens'] | right['tokens']
        overlap = len(left['tokens'] & right['tokens']) / max(1, len(union))
        text = SequenceMatcher(None, left['text'][:2000], right['text'][:2000]).ratio()
        return .45 * overlap + .45 * text + .1 * visual
    return visual


def _align_gap(left, right, li, lj, ri, rj):
    n, m = lj-li, rj-ri
    if not n:
        return [(None, index) for index in range(ri, rj)]
    if not m:
        return [(index, None) for index in range(li, lj)]
    # Large unanchored rewrites have no reliable inferred correspondence.
    # Keep work bounded and expose the resulting page pairs in the UI.
    if n*m > 10000:
        return [(li+i if i<n else None, ri+i if i<m else None) for i in range(max(n, m))]
    costs = [[0.] * (m+1) for _ in range(n+1)]
    moves = [[0] * (m+1) for _ in range(n+1)]
    for i in range(1, n+1):
        costs[i][0], moves[i][0] = i*.6, 1
    for j in range(1, m+1):
        costs[0][j], moves[0][j] = j*.6, 2
    for i in range(1, n+1):
        for j in range(1, m+1):
            similarity = _similarity(left[li+i-1], right[ri+j-1])
            choices = (costs[i-1][j-1] + 1-similarity,
                       costs[i-1][j]+.6, costs[i][j-1]+.6)
            move = min(range(3), key=lambda k: choices[k])
            costs[i][j], moves[i][j] = choices[move], move
    pairs, i, j = [], n, m
    while i or j:
        move = moves[i][j]
        pairs.append((li+i-1 if move != 2 else None, ri+j-1 if move != 1 else None))
        i -= move != 2
        j -= move != 1
    return pairs[::-1]


def align_pages(left, right):
    pairs = []
    matcher = SequenceMatcher(None, [p['key'] for p in left], [p['key'] for p in right], autojunk=False)
    for tag, li, lj, ri, rj in matcher.get_opcodes():
        if tag == 'equal':
            pairs.extend(zip(range(li, lj), range(ri, rj)))
        else:
            pairs.extend(_align_gap(left, right, li, lj, ri, rj))
    return list(pairs)


def _word_rects(page, words):
    rects = []
    for word in words:
        rect = pymupdf.Rect(word[:4]) * page.rotation_matrix
        if rects and abs(rects[-1][1]-rect.y0) < 2 and abs(rects[-1][3]-rect.y1) < 2 and rect.x0-rects[-1][2] < 15:
            rects[-1] = list(pymupdf.Rect(rects[-1]) | rect)
        else:
            rects.append(list(rect))
    return rects


def _text_changes(left, right):
    old, new = left.get_text('words', sort=True), right.get_text('words', sort=True)
    matcher = SequenceMatcher(None, [w[4] for w in old], [w[4] for w in new], autojunk=False)
    changes = []
    for tag, li, lj, ri, rj in matcher.get_opcodes():
        if tag == 'equal':
            continue
        before, after = ' '.join(w[4] for w in old[li:lj]), ' '.join(w[4] for w in new[ri:rj])
        changes.append({'kind': tag, 'before': before[:240], 'after': after[:240],
                        'left_rects': _word_rects(left, old[li:lj]),
                        'right_rects': _word_rects(right, new[ri:rj])})
    return changes


def _visual_regions(left, right):
    scale = min(2., 1500 / max(*left.rect[2:], *right.rect[2:]))
    old, new = _raster(left, scale), _raster(right, scale)
    size = (max(old.width, new.width), max(old.height, new.height))
    canvases = [Image.new('RGB', size, 'white'), Image.new('RGB', size, 'white')]
    canvases[0].paste(old, (0, 0))
    canvases[1].paste(new, (0, 0))
    channels = ImageChops.difference(*canvases).split()
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]).point(lambda v: 255 if v > 24 else 0)
    bounds = mask.getbbox()
    if not bounds:
        return []
    # Connected 12-pixel tiles give readable regions rather than one box per pixel.
    tile, occupied = 12, {}
    for y in range(bounds[1]//tile, (bounds[3]+tile-1)//tile):
        for x in range(bounds[0]//tile, (bounds[2]+tile-1)//tile):
            crop = mask.crop((x*tile, y*tile, min((x+1)*tile, size[0]), min((y+1)*tile, size[1])))
            box = crop.getbbox()
            if box:
                occupied[(x, y)] = (x*tile+box[0], y*tile+box[1], x*tile+box[2], y*tile+box[3])
    regions = []
    while occupied:
        point, box = occupied.popitem()
        pending, region = [point], pymupdf.Rect(box)
        while pending:
            x, y = pending.pop()
            for dx, dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                near = (x+dx, y+dy)
                if near in occupied:
                    region |= pymupdf.Rect(occupied.pop(near))
                    pending.append(near)
        regions.append(list(pymupdf.Rect(*(v/scale for v in region))))
    return sorted(regions, key=lambda r: (r[1], r[0]))


def _clip(rect, page):
    result = pymupdf.Rect(rect) & page.rect
    return [list(result)] if not result.is_empty else []


def compare_documents(left, right, progress=None):
    for document in (left, right):
        _require_permission(document, pymupdf.PDF_PERM_COPY)
    fingerprints = [[], []]
    total = len(left) + len(right)
    done = 0
    for side, document in enumerate((left, right)):
        for page in document:
            fingerprints[side].append(_fingerprint(page))
            done += 1
            if progress:
                progress(f'페이지 맞추기 · {done} / {total}')
    aligned = align_pages(*fingerprints)
    pairs = []
    for index, (li, ri) in enumerate(aligned):
        if progress:
            progress(f'변경 내용 비교 · {index+1} / {len(aligned)}')
        if li is None or ri is None:
            changes = [{'kind': 'page_insert' if li is None else 'page_delete', 'before': '', 'after': '',
                        'left_rects': [] if li is None else [list(left[li].rect)],
                        'right_rects': [] if ri is None else [list(right[ri].rect)]}]
        else:
            old, new = left[li], right[ri]
            changes = _text_changes(old, new)
            # Text boxes explain the corresponding raster changes already.
            covered = [pymupdf.Rect(r) + (-2, -2, 2, 2) for change in changes
                       for r in change['left_rects'] + change['right_rects']]
            for region in _visual_regions(old, new):
                rect = pymupdf.Rect(region)
                if any(box.contains(rect) for box in covered):
                    continue
                changes.append({'kind': 'visual', 'before': '', 'after': '',
                                'left_rects': _clip(region, old), 'right_rects': _clip(region, new)})
            if abs(old.rect.width-new.rect.width) > .1 or abs(old.rect.height-new.rect.height) > .1:
                changes.append({'kind': 'size', 'before': f'{old.rect.width*25.4/72:.1f} × {old.rect.height*25.4/72:.1f} mm',
                                'after': f'{new.rect.width*25.4/72:.1f} × {new.rect.height*25.4/72:.1f} mm',
                                'left_rects': [list(old.rect)], 'right_rects': [list(new.rect)]})
        pairs.append({'left': li, 'right': ri, 'changes': changes})
    return {'pairs': pairs, 'changed_pages': sum(bool(p['changes']) for p in pairs),
            'change_count': sum(len(p['changes']) for p in pairs)}


def comparison_worker(request_path):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    request = Path(request_path)
    task = json.loads(request.read_text(encoding='utf-8'))
    try:
        with _open_pdf(task['left'], task.get('left_password')) as left, _open_pdf(task['right'], task.get('right_password')) as right:
            result = compare_documents(left, right, lambda message: print(message, flush=True))
        payload, code = {'result': result}, 0
    except Exception as error:
        payload, code = {'error': str(error)}, 1
    (request.parent/'result.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return code
