"""Select individual PDF paint operations, including shared Form instances.

Edits change path coordinates or one image invocation. They never redact an
area, replace a shared image resource, or rasterize the saved page.
"""
from dataclasses import dataclass
import math
import re
import pymupdf


@dataclass
class Operation:
    name: bytes
    start: int
    operator: int
    end: int
    args: list


def _tokens(data):
    """PDF lexical tokens with offsets; strings and containers are opaque."""
    index, size = 0, len(data)
    whitespace = b'\x00\t\n\x0c\r '
    delimiters = whitespace + b'()<>[]{}/%'
    while index < size:
        if data[index] in whitespace:
            index += 1
            continue
        if data[index] == 37:
            end = data.find(b'\n', index)
            index = size if end < 0 else end + 1
            continue
        start = index
        char = data[index]
        if char == 40:
            depth = 1
            index += 1
            while index < size and depth:
                char = data[index]
                if char == 92:
                    index += 2
                    continue
                depth += (char == 40) - (char == 41)
                index += 1
        elif char == 60 and data[index:index+2] != b'<<':
            end = data.find(b'>', index+1)
            index = size if end < 0 else end+1
        elif data[index:index+2] in (b'<<', b'>>'):
            index += 2
        elif char in b'[]{}':
            index += 1
        else:
            index += 1
            while index < size and data[index] not in delimiters:
                index += 1
        yield data[start:index], start, index


def operations(data):
    args, depth = [], 0
    iterator = iter(_tokens(data))
    for value, start, end in iterator:
        if value in (b'[', b'<<'):
            depth += 1
        elif value in (b']', b'>>'):
            depth -= 1
        operand = depth or value in (b']', b'>>', b'true', b'false', b'null') or value[:1] in b'/([<+-.0123456789'
        if operand:
            args.append((value, start, end))
            continue
        begin = args[0][1] if args else start
        if value == b'BI':
            # Inline image bytes are not PDF tokens. Locate the terminator and
            # leave this operation intact; embedded XObject images are editable.
            match = re.search(rb'\sEI(?=\s|$)', data[end:])
            if match is None:
                raise ValueError('페이지의 인라인 이미지가 손상되었습니다.')
            end += match.end()
            yield Operation(value, begin, start, end, args)
            iterator = iter(_tokens(data[end:]))
            for following in operations(data[end:]):
                following.start += end
                following.operator += end
                following.end += end
                following.args = [(v, a+end, b+end) for v, a, b in following.args]
                yield following
            return
        yield Operation(value, begin, start, end, args)
        args = []


def _reference(doc, owner, key):
    kind, value = doc.xref_get_key(owner, key)
    return int(value.split()[0]) if kind == 'xref' else None


def _resource_owner(doc, owner):
    seen = set()
    while owner and owner not in seen:
        seen.add(owner)
        if doc.xref_get_key(owner, 'Resources')[0] != 'null':
            return owner
        owner = _reference(doc, owner, 'Parent')
    return None


def _matrix(doc, xref):
    kind, value = doc.xref_get_key(xref, 'Matrix')
    if kind == 'array':
        return pymupdf.Matrix(*map(float, value.strip('[]').split()))
    return pymupdf.Matrix(1, 1)


def page_matrix(page):
    """PDF coordinates to unrotated page coordinates, including crop and UserUnit."""
    mupdf = pymupdf.mupdf
    matrix = mupdf.FzMatrix()
    mupdf.pdf_page_transform(page._pdf_page(),mupdf.FzRect(),matrix)
    return pymupdf.Matrix(matrix.a,matrix.b,matrix.c,matrix.d,matrix.e,matrix.f)*page.derotation_matrix


def _name(raw):
    return re.sub(rb'#([0-9A-Fa-f]{2})', lambda m: bytes([int(m[1], 16)]), raw[1:]).decode('latin1')


PAINT = (b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*')
TEXT_SHOW = (b'Tj', b'TJ', b"'", b'"')
# A clip larger than this share of the page is an artboard or text-area clip
# that wraps unrelated content, not a clipping mask the user would select.
MASK_AREA = .6


def page_objects(page):
    """Return paint operations in their drawing order, with per-instance routes."""
    return scan_page(page)[0]


def scan_page(page):
    """Paint operations plus clipping groups.

    A clipping group is the stream range from a clip path to the Q that ends
    it: the mask and everything it clips, which move and delete as one unit.
    Each object's 'group' is the outermost clipping mask it belongs to.
    """
    objects, groups = [], []
    doc = page.parent
    transform_page = page_matrix(page)
    active = set()

    def walk(data, owner, resource_owner, matrix, route, inherited=None):
        if len(route) > 32 or owner in active:
            return
        active.add(owner)
        stack, path, clipped, opened = [], [], False, []
        state = dict(inherited) if inherited is not None else dict(width=1., fill=(0.,0.,0.), stroke=(0.,0.,0.),
                                                                fill_space=b'/DeviceGray', stroke_space=b'/DeviceGray',
                                                                clips=())
        try:
            for op in operations(data):
                command = op.name
                values = [token[0] for token in op.args]
                if command == b'q':
                    stack.append((matrix, dict(state)))
                elif command == b'Q' and stack:
                    for group in [g for g in opened if g['depth'] >= len(stack)]:
                        group['end'] = op.start
                        opened.remove(group)
                    matrix, state = stack.pop()
                elif command == b'cm' and len(values) == 6:
                    matrix = pymupdf.Matrix(*map(float, values)) * matrix
                elif command == b'w' and values:
                    state['width'] = float(values[0])
                elif command == b'gs' and values and resource_owner:
                    kind, value = doc.xref_get_key(resource_owner, 'Resources/ExtGState/'+_name(values[0])+'/LW')
                    if kind in ('int', 'float'):
                        state['width'] = float(value)
                elif command in (b'g', b'G', b'rg', b'RG', b'k', b'K'):
                    field = 'stroke' if command.isupper() else 'fill'
                    space = {b'g': b'/DeviceGray', b'rg': b'/DeviceRGB', b'k': b'/DeviceCMYK'}[command.lower()]
                    state[field+'_space'] = space
                    state[field] = _rgb(values, space)
                elif command in (b'cs', b'CS') and values:
                    field = 'stroke' if command == b'CS' else 'fill'
                    space = values[0]
                    if space not in (b'/DeviceGray', b'/DeviceRGB', b'/DeviceCMYK') and resource_owner:
                        kind, name = doc.xref_get_key(resource_owner, 'Resources/ColorSpace/'+_name(space))
                        if kind == 'name':
                            space = name.encode()
                    state[field+'_space'] = space
                    state[field] = _rgb([b'0']*({b'/DeviceRGB': 3, b'/DeviceCMYK': 4}.get(space,1)),space)
                elif command in (b'sc', b'SC', b'scn', b'SCN'):
                    field = 'stroke' if command.isupper() else 'fill'
                    state[field] = _rgb(values,state[field+'_space'])
                elif command in (b'm', b'l', b'c', b'v', b'y', b're', b'h'):
                    path.append((op, pymupdf.Matrix(matrix)))
                elif command in (b'W', b'W*'):
                    clipped = True
                elif command in PAINT or command == b'n':
                    points = _points(path, transform_page)
                    if points and clipped:
                        group = dict(id=len(groups), kind='group', route=route, start=path[0][0].start, end=len(data),
                                     depth=len(stack), matrix=pymupdf.Matrix(matrix), clip=tuple(_bounds(points)))
                        groups.append(group)
                        opened.append(group)
                        state['clips'] = state['clips'] + (group['id'],)
                    if points and command != b'n' and not clipped:
                        bounds = _bounds(points)
                        stroke = command in (b'S',b's',b'B',b'B*',b'b',b'b*')
                        fill = command in (b'f',b'F',b'f*',b'B',b'B*',b'b',b'b*')
                        effective = matrix*transform_page
                        scale = max(math.hypot(effective.a,effective.b),math.hypot(effective.c,effective.d))
                        pad = max(.5, state['width']*scale/2) if stroke else .1
                        objects.append(dict(id=len(objects), kind='path', rect=tuple(bounds + (-pad,-pad,pad,pad)),
                                            bounds=tuple(bounds), route=route, op=op, paths=path, matrix=matrix,
                                            fill=state['fill'] if fill else None,
                                            stroke=state['stroke'] if stroke else None, has_fill=fill, has_stroke=stroke,
                                            width=state['width']*scale, stroke_scale=scale, clips=state['clips']))
                    path, clipped = [], False
                elif command == b'Do' and len(values) == 1 and resource_owner:
                    name = _name(values[0])
                    xref = _reference(doc, resource_owner, 'Resources/XObject/'+name)
                    if not xref:
                        continue
                    subtype = doc.xref_get_key(xref, 'Subtype')[1]
                    if subtype == '/Image':
                        bounds = pymupdf.Rect(0,0,1,1)*matrix*transform_page
                        objects.append(dict(id=len(objects),kind='image',rect=tuple(bounds),bounds=tuple(bounds),
                                            route=route,op=op,paths=[],matrix=matrix,xref=xref,clips=state['clips']))
                    elif subtype == '/Form':
                        child_resources = _resource_owner(doc, xref) or resource_owner
                        walk(doc.xref_stream(xref), xref, child_resources, _matrix(doc,xref)*matrix,
                             route+[(op, xref, resource_owner)],state)
        finally:
            active.remove(owner)

    walk(page.read_contents(), page.xref, _resource_owner(doc,page.xref), pymupdf.Matrix(1,1), [])
    area = abs(page.rect.get_area())
    for group in groups:
        group['members'] = [target['id'] for target in objects if group['id'] in target['clips']]
        group['mask'] = False
        if group['members'] and abs(pymupdf.Rect(group['clip']).get_area()) < MASK_AREA*area:
            content = pymupdf.Rect()
            for member in group['members']:
                content |= pymupdf.Rect(objects[member]['rect'])
            shown = pymupdf.Rect(group['clip']) & content
            if not shown.is_empty:
                group['mask'] = True
                group['rect'] = group['bounds'] = tuple(shown)
    for target in objects:
        target['group'] = next((gid for gid in target['clips'] if groups[gid]['mask']), None)
    return objects, groups


def _points(path, transform_page):
    points = []
    for part, transform in path:
        coords = [float(token[0]) for token in part.args]
        if part.name == b're':
            x, y, w, h = coords
            coords = [x,y,x+w,y,x+w,y+h,x,y+h]
        points += [pymupdf.Point(coords[i:i+2])*transform*transform_page for i in range(0,len(coords)-1,2)]
    return points


def _bounds(points):
    return pymupdf.Rect(min(p.x for p in points), min(p.y for p in points),
                        max(p.x for p in points), max(p.y for p in points))


def resolve(page, ref):
    """Look up an object id, ('object', id) or a clipping ('group', id) of scan_page()."""
    objects, groups = scan_page(page)
    kind, number = ref if isinstance(ref, tuple) and len(ref) == 2 else ('object', ref)
    items = groups if kind == 'group' else objects if kind == 'object' else None
    if items is None or isinstance(number,bool) or not isinstance(number,int) or not 0 <= number < len(items):
        raise ValueError('선택한 객체를 찾지 못했습니다. 다시 선택해 주세요.')
    if kind == 'group' and not items[number]['mask']:
        raise ValueError('선택한 클리핑 그룹을 찾지 못했습니다. 다시 선택해 주세요.')
    return items[number]


def path_parts(page, target):
    """A path's segments in unrotated page coordinates, as ('m'|'l'|'c'|'h', points).

    Rectangles become four lines and a close; v and y curves spell out the
    control point that PDF leaves implicit.
    """
    transform_page = page_matrix(page)
    parts, current, start = [], None, None
    for op, matrix in target['paths']:
        transform = matrix*transform_page
        values = [float(arg[0]) for arg in op.args]
        name = op.name
        if name == b'h':
            parts.append(('h', []))
            current = start
            continue
        if name == b're':
            x, y, w, h = values
            corners = [pymupdf.Point(x,y), pymupdf.Point(x+w,y), pymupdf.Point(x+w,y+h), pymupdf.Point(x,y+h)]
            parts += [('m',[corners[0]*transform])]+[('l',[p*transform]) for p in corners[1:]]+[('h',[])]
            current = start = corners[0]
            continue
        if name == b'v':
            values = [current.x, current.y]+values if current is not None else values[:2]+values
        elif name == b'y':
            values = values+values[2:4]
        points = [pymupdf.Point(values[i],values[i+1]) for i in range(0,len(values)-1,2)]
        parts.append(('c' if name in (b'c',b'v',b'y') else name.decode(), [p*transform for p in points]))
        current = points[-1]
        if name == b'm':
            start = current
    return parts


def _rgb(values, space):
    """Read device colors; leave unsupported color spaces unchanged on edit."""
    try:
        channels = tuple(max(0.,min(1.,float(value))) for value in values)
    except ValueError:
        return None
    if space == b'/DeviceGray' and len(channels) == 1:
        return channels*3
    if space == b'/DeviceRGB' and len(channels) == 3:
        return channels
    if space == b'/DeviceCMYK' and len(channels) == 4:
        c,m,y,k = channels
        return ((1-c)*(1-k),(1-m)*(1-k),(1-y)*(1-k))
    return None


def _write_page_contents(page, data):
    doc = page.parent
    xref = doc.get_new_xref()
    doc.update_object(xref, '<<>>')
    doc.update_stream(xref, data)
    page.set_contents(xref)


def _replace(data, replacements):
    for start, end, replacement in sorted(replacements, reverse=True):
        data = data[:start]+replacement+data[end:]
    return data


def _clone_resources(doc, source_owner, target_owner):
    kind, value = doc.xref_get_key(source_owner, 'Resources')
    if kind == 'xref':
        value = doc.xref_object(int(value.split()[0]))
    if kind not in ('xref', 'dict'):
        raise ValueError('객체 리소스를 찾지 못했습니다.')
    xref = doc.get_new_xref()
    doc.update_object(xref, value)
    doc.xref_set_key(target_owner, 'Resources', f'{xref} 0 R')
    # Updating a name must also leave a shared XObject dictionary intact.
    kind, value = doc.xref_get_key(xref,'XObject')
    if kind == 'xref':
        child = doc.get_new_xref()
        doc.update_object(child, doc.xref_object(int(value.split()[0])))
        doc.xref_set_key(xref,'XObject',f'{child} 0 R')
    return xref


def _stream(page, target):
    return page.parent.xref_stream(target['route'][-1][1]) if target['route'] else page.read_contents()


def _local(page, matrix, transform):
    """The cm matrix that applies page-space `transform` to content drawn under `matrix`."""
    placed = matrix*page_matrix(page)
    if abs(placed.a*placed.d-placed.b*placed.c) < 1e-10:
        raise ValueError('객체의 좌표 변환을 계산할 수 없습니다.')
    inverse = ~placed
    if (transform.a, transform.b, transform.c, transform.d) == (1, 0, 0, 1):
        shift = pymupdf.Point(transform.e, transform.f)*inverse-pymupdf.Point(0, 0)*inverse
        return pymupdf.Matrix(1, 0, 0, 1, shift.x, shift.y)
    return placed*transform*inverse


def _number(value):
    # Matrix products leave float noise, such as 0.9999999999 for 1.
    nearest = round(value)
    return f'{nearest:d}' if abs(value-nearest) < 1e-9 else f'{value:.8g}'


def _matrix_bytes(matrix):
    return (' '.join(_number(v) for v in tuple(matrix))+' cm').encode()


def _checked(transform):
    if isinstance(transform, (tuple, list)) and len(transform) == 2:
        transform = pymupdf.Matrix(1, 0, 0, 1, *map(float, transform))
    transform = pymupdf.Matrix(transform)
    values = tuple(transform)
    if not all(math.isfinite(v) for v in values) or abs(transform.a*transform.d-transform.b*transform.c) < 1e-8:
        raise ValueError('객체 이동 좌표가 올바르지 않습니다.')
    return transform


def edit_object(page, object_id, delta=None):
    """Delete (delta=None) or translate one object or clipping group, preserving its order."""
    if delta is None:
        return delete_object(page, object_id)
    return transform_object(page, object_id, delta)


def delete_object(page, ref):
    target = resolve(page, ref)
    data = _stream(page, target)
    if target['kind'] == 'group':
        changes = [(target['start'], target['end'], b' ')]
    else:
        op = target['op']
        changes = [(op.start, op.end, b'n' if target['kind'] == 'path' else b'')]
    _write_object_stream(page, target, _replace(data, changes))
    return target


def transform_object(page, ref, transform):
    """Apply a page-space move or resize (an unrotated-page Matrix or (dx, dy)).

    Paths get new coordinates so their stroke width is unchanged. Images and
    clipping groups, mask and contents together, are wrapped in a cm.
    """
    target = resolve(page, ref)
    transform = _checked(transform)
    data = _stream(page, target)
    if target['kind'] == 'group':
        local = _local(page, target['matrix'], transform)
        wrapped = b' q '+_matrix_bytes(local)+b'\n'+data[target['start']:target['end']]+b'\nQ\n'
        changes = [(target['start'], target['end'], wrapped)]
    elif target['kind'] == 'image':
        op = target['op']
        local = _local(page, target['matrix'], transform)
        changes = [(op.start, op.end, b' q '+_matrix_bytes(local)+b' '+data[op.start:op.end]+b' Q ')]
    else:
        changes = []
        for part, matrix in target['paths']:
            if part.name == b'h':
                continue
            local = _local(page, matrix, transform)
            values = [float(arg[0]) for arg in part.args]
            if part.name == b're':
                x, y, w, h = values
                corners = [pymupdf.Point(x,y)*local, pymupdf.Point(x+w,y)*local,
                           pymupdf.Point(x+w,y+h)*local, pymupdf.Point(x,y+h)*local]
                if abs(local.b) < 1e-9 and abs(local.c) < 1e-9:
                    x0, y0 = corners[0]
                    replacement = ' '.join(map(_number, (x0, y0, w*local.a, h*local.d)))+' re'
                else:
                    replacement = ' '.join(f'{_number(p.x)} {_number(p.y)} {o}' for p, o in zip(corners, 'mlll'))+' h'
            else:
                points = [pymupdf.Point(values[i], values[i+1])*local for i in range(0, len(values)-1, 2)]
                replacement = ' '.join(f'{_number(p.x)} {_number(p.y)}' for p in points)+' '+part.name.decode()
            changes.append((part.start, part.end, b' '+replacement.encode()+b' '))
    _write_object_stream(page, target, _replace(data, changes))
    return target


def reshape_path(page, ref, parts):
    """Replace a path's anchor and control points, given as path_parts() returned them."""
    target = resolve(page, ref)
    if target['kind'] != 'path':
        raise ValueError('노드는 벡터 도형에서만 편집할 수 있습니다.')
    original = path_parts(page, target)
    if [(name, len(points)) for name, points in original] != [(name, len(points)) for name, points in parts]:
        raise ValueError('도형의 노드 구성이 바뀌었습니다. 다시 선택해 주세요.')
    points = [pymupdf.Point(p) for _, part in parts for p in part]
    if not all(math.isfinite(p.x) and math.isfinite(p.y) for p in points):
        raise ValueError('노드 좌표가 올바르지 않습니다.')
    data = _stream(page, target)
    changes, index = [], 0
    for op, matrix in target['paths']:
        placed = matrix*page_matrix(page)
        if abs(placed.a*placed.d-placed.b*placed.c) < 1e-10:
            raise ValueError('객체의 좌표 변환을 계산할 수 없습니다.')
        inverse = ~placed
        if op.name == b'h':
            index += 1
            continue
        if op.name == b're':
            corners = [parts[index+i][1][0]*inverse for i in range(4)]
            replacement = ' '.join(f'{_number(p.x)} {_number(p.y)} {o}' for p, o in zip(corners, 'mlll'))+' h'
            index += 5
        else:
            name, values = parts[index]
            local = [p*inverse for p in values]
            replacement = ' '.join(f'{_number(p.x)} {_number(p.y)}' for p in local)+' '+name
            index += 1
        changes.append((op.start, op.end, b' '+replacement.encode()+b' '))
    _write_object_stream(page, target, _replace(data, changes))
    return target


def _write_object_stream(page, target, edited, parent_edit=None):
    """Write the edited innermost stream, isolating every Form on the route.

    parent_edit(data, invocation) may return more replacements for each parent
    stream; it must leave the invocation itself alone.
    """
    doc = page.parent
    # Clone every invoked Form on this route, changing only this invocation.
    current_resources = (_resource_owner(doc,target['route'][-1][1]) or target['route'][-1][2]) if target['route'] else None
    for route_index in range(len(target['route'])-1,-1,-1):
        invocation, form_xref, resources = target['route'][route_index]
        child = doc.get_new_xref()
        doc.update_object(child,doc.xref_object(form_xref))
        doc.update_stream(child,edited)
        _clone_resources(doc,current_resources,child)
        parent_xref = target['route'][route_index-1][1] if route_index else page.xref
        parent_data = doc.xref_stream(parent_xref) if route_index else page.read_contents()
        name = f'XdfObj{child}'
        owner = doc.get_new_xref()
        doc.update_object(owner,doc.xref_object(parent_xref))
        resource_xref = _clone_resources(doc,resources,owner)
        xobject_xref = _reference(doc,resource_xref,'XObject')
        doc.xref_set_key(xobject_xref or resource_xref, name if xobject_xref else 'XObject/'+name, f'{child} 0 R')
        changes = [(invocation.start,invocation.end,f'/{name} Do'.encode())]
        if parent_edit is not None:
            changes += parent_edit(parent_data, invocation)
        edited = _replace(parent_data,changes)
        current_resources = owner
    if target['route']:
        doc.xref_set_key(page.xref,'Resources',doc.xref_get_key(current_resources,'Resources')[1])
    _write_page_contents(page,edited)


def style_object(page, object_id, **changes):
    """Change one path's fill, stroke or page-point width in its original order.

    Omitted fields preserve the original graphics state, including spot colors
    and transparency. None removes a fill or stroke. Shared Form routes are
    isolated exactly as they are for moving and deleting an object.
    """
    if isinstance(object_id,tuple) and len(object_id) == 2 and object_id[0] == 'object':
        object_id = object_id[1]
    objects = page_objects(page)
    if isinstance(object_id,bool) or not isinstance(object_id,int) or not 0 <= object_id < len(objects):
        raise ValueError('선택한 도형을 찾지 못했습니다. 다시 선택해 주세요.')
    target = objects[object_id]
    if target['kind'] != 'path' or not changes or set(changes)-{'fill','stroke','width'}:
        raise ValueError('벡터 도형의 채움색, 외곽선색과 두께만 바꿀 수 있습니다.')
    enabled = {'fill': target['has_fill'], 'stroke': target['has_stroke']}
    commands = [b'q']
    for field, operator in (('fill', b'rg'), ('stroke', b'RG')):
        if field not in changes:
            continue
        color = changes[field]
        enabled[field] = color is not None
        if color is not None:
            try:
                rgb = tuple(map(float,color))
            except (TypeError,ValueError):
                raise ValueError('색상 값이 올바르지 않습니다.') from None
            if len(rgb) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in rgb):
                raise ValueError('색상 값이 올바르지 않습니다.')
            commands.append((' '.join(f'{v:.8g}' for v in rgb)+' ').encode()+operator)
    if not any(enabled.values()):
        raise ValueError('채움색 또는 외곽선 중 하나는 남겨 주세요. 도형 삭제는 Delete를 사용하세요.')
    if 'width' in changes:
        width = float(changes['width'])
        scale = target['stroke_scale']
        if not math.isfinite(width) or width < 0 or scale < 1e-10:
            raise ValueError('외곽선 두께가 올바르지 않습니다.')
        commands.append(f'{width/scale:.8g} w'.encode())
    original = target['op'].name
    if original in (b's',b'b',b'b*'):
        commands.append(b'h')
    even_odd = original.endswith(b'*')
    paint = (b'B' if enabled['stroke'] else b'f') + (b'*' if even_odd else b'') if enabled['fill'] else b'S'
    commands.extend((paint,b'Q'))
    op = target['op']
    data = page.parent.xref_stream(target['route'][-1][1]) if target['route'] else page.read_contents()
    _write_object_stream(page,target,_replace(data,[(op.start,op.end,b'\n'.join(commands))]))
    return target


def _hide(data, keep):
    """Replacements that stop painting every operation for which keep(op) is false.

    Paths keep their construction and clipping. Text objects cannot contain
    paths or XObjects, so a split never falls inside one and its shows can go.
    """
    changes = []
    for op in operations(data):
        if keep(op):
            continue
        if op.name in PAINT:
            changes.append((op.operator, op.end, b'n'))
        elif op.name in (b'Do', b'BI', b'sh') or op.name in TEXT_SHOW:
            changes.append((op.start, op.end, b''))
    return changes


def object_layer(page, ref, part='object'):
    """Copy the page with only one part of its paint order, for hit testing and dragging.

    'object' keeps just the selected object or clipping group, 'below' what is
    painted before it and 'above' what is painted after it, annotations included.
    """
    layer = pymupdf.open()
    try:
        above = part == 'above'
        layer.insert_pdf(page.parent,from_page=page.number,to_page=page.number,annots=above,links=False,widgets=above)
        copied = layer[0]
        target = resolve(copied, ref)
        if target['kind'] == 'group':
            low, high = target['start'], target['end']
        else:
            low, high = target['op'].operator, target['op'].end
        inside = {'object': lambda op: low <= op.operator < high,
                  'below': lambda op: op.operator < low,
                  'above': lambda op: op.operator >= high}[part]
        data = _stream(copied, target)

        def parent_edit(parent_data, invocation):
            keep = {'object': lambda op: op.operator == invocation.operator,
                    'below': lambda op: op.operator <= invocation.operator,
                    'above': lambda op: op.operator >= invocation.operator}[part]
            return _hide(parent_data, keep)
        _write_object_stream(copied, target, _replace(data, _hide(data, inside)), parent_edit)
        return layer
    except BaseException:
        layer.close()
        raise
