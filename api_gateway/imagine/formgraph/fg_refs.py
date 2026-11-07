"""Formgraph references: images, sketches, video frames and meshes the design is checked against.

context/references.json lists them; files live under context/refs/. This module validates the list,
reads image sizes and meshes (STL, OBJ, GLB) with the standard library, reduces large meshes for
display, and packs everything for the page.

Standard library only.
"""
import base64
import json
import math
import os
import re
import struct

KINDS = {'image', 'sketch', 'frame', 'mesh', 'video'}
ROLES = {'look', 'target', 'envelope', 'context'}
CONF = {'measured', 'estimated', 'read'}
VIEWS = {'front', 'back', 'side', 'top', 'bottom', 'iso', 'other'}
UNITS = {'mm': 1.0, 'cm': 10.0, 'm': 1000.0, 'in': 25.4}
IMG_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
MESH_TYPES = {'.stl', '.obj', '.glb'}
VIDEO_TYPES = {'.mp4', '.webm', '.mov', '.m4v'}
MAX_IMAGE = 2 * 1024 * 1024
MAX_TOTAL = 10 * 1024 * 1024
MAX_TRIS = 40000
MAX_FRAMES = 12  # per video, to save compute
FORM_KEYS = {'form.W', 'form.D', 'form.H', 'form.R', 'form.F', 'form.A', 'form.t', 'form.P'}
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}$')


# ---------------------------------------------------------------- images
def image_size(path):
    """(width, height) of a PNG, JPEG, GIF or WebP file, or None."""
    with open(path, 'rb') as f:
        head = f.read(64)
        if head[:8] == b'\x89PNG\r\n\x1a\n':
            return struct.unpack('>II', head[16:24])
        if head[:6] in (b'GIF87a', b'GIF89a'):
            return struct.unpack('<HH', head[6:10])
        if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
            kind = head[12:16]
            if kind == b'VP8 ':
                w, h = struct.unpack('<HH', head[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if kind == b'VP8L':
                b = head[21:25]
                v = int.from_bytes(b, 'little')
                return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
            if kind == b'VP8X':
                return int.from_bytes(head[24:27], 'little') + 1, int.from_bytes(head[27:30], 'little') + 1
        if head[:2] == b'\xff\xd8':
            f.seek(2)
            while True:
                b = f.read(1)
                while b and b != b'\xff':
                    b = f.read(1)
                while b == b'\xff':
                    b = f.read(1)
                if not b:
                    return None
                m = b[0]
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    continue
                ln = struct.unpack('>H', f.read(2))[0]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    d = f.read(5)
                    h, w = struct.unpack('>HH', d[1:5])
                    return w, h
                f.seek(ln - 2, 1)
    return None


# ---------------------------------------------------------------- meshes
def _stl(path):
    data = open(path, 'rb').read()
    tris = []
    if len(data) >= 84:
        n = struct.unpack('<I', data[80:84])[0]
        if 84 + n * 50 == len(data):
            for i in range(n):
                o = 84 + i * 50 + 12
                v = struct.unpack('<9f', data[o:o + 36])
                tris.append((v[0:3], v[3:6], v[6:9]))
            return tris
    txt = data.decode('utf-8', 'replace')
    vs = [tuple(map(float, m.groups())) for m in re.finditer(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', txt)]
    return [tuple(vs[i:i + 3]) for i in range(0, len(vs) - 2, 3)]


def _obj(path):
    vs, tris = [], []
    for line in open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if not p:
            continue
        if p[0] == 'v' and len(p) >= 4:
            vs.append(tuple(map(float, p[1:4])))
        elif p[0] == 'f' and len(p) >= 4:
            idx = []
            for t in p[1:]:
                i = int(t.split('/')[0])
                idx.append(i - 1 if i > 0 else len(vs) + i)
            for k in range(1, len(idx) - 1):
                tris.append((vs[idx[0]], vs[idx[k]], vs[idx[k + 1]]))
    return tris


def _mat_mul(a, b):
    return [sum(a[r + 4 * k] * b[k + 4 * c] for k in range(4)) for c in range(4) for r in range(4)]  # column-major


def _node_matrix(n):
    if 'matrix' in n:
        return list(n['matrix'])
    t = n.get('translation', [0, 0, 0])
    q = n.get('rotation', [0, 0, 0, 1])
    s = n.get('scale', [1, 1, 1])
    x, y, z, w = q
    r = [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
         0, 0, 0, 1]
    for c in range(3):
        for k in range(3):
            r[4 * c + k] *= s[c]
    r[12], r[13], r[14] = t
    return r


def _glb(path):
    data = open(path, 'rb').read()
    if data[:4] != b'glTF':
        raise ValueError('not a GLB file')
    off, js, bin_ = 12, None, b''
    while off < len(data):
        ln, typ = struct.unpack('<II', data[off:off + 8])
        chunk = data[off + 8:off + 8 + ln]
        if typ == 0x4E4F534A:
            js = json.loads(chunk)
        elif typ == 0x004E4942:
            bin_ = chunk
        off += 8 + ln
    if 'KHR_draco_mesh_compression' in (js.get('extensionsUsed') or []):
        raise ValueError('Draco-compressed GLB is not supported; export without compression')
    acc, views = js.get('accessors', []), js.get('bufferViews', [])
    CT = {5120: ('b', 1), 5121: ('B', 1), 5122: ('h', 2), 5123: ('H', 2), 5125: ('I', 4), 5126: ('f', 4)}
    NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

    def read(i):
        a = acc[i]
        bv = views[a['bufferView']]
        fmt, size = CT[a['componentType']]
        nc = NC[a['type']]
        stride = bv.get('byteStride') or size * nc
        base = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        out = []
        for k in range(a['count']):
            o = base + k * stride
            out.append(struct.unpack('<' + fmt * nc, bin_[o:o + size * nc]))
        return out

    tris = []
    ident = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def walk(ni, parent):
        n = js['nodes'][ni]
        m = _mat_mul(parent, _node_matrix(n))
        if 'mesh' in n:
            for prim in js['meshes'][n['mesh']].get('primitives', []):
                if prim.get('mode', 4) != 4 or 'POSITION' not in prim.get('attributes', {}):
                    continue
                pos = read(prim['attributes']['POSITION'])
                pw = [(m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]) for x, y, z in pos]
                idx = [i[0] for i in read(prim['indices'])] if 'indices' in prim else list(range(len(pw)))
                for k in range(0, len(idx) - 2, 3):
                    tris.append((pw[idx[k]], pw[idx[k + 1]], pw[idx[k + 2]]))
        for c in n.get('children', []):
            walk(c, m)

    scene = js.get('scenes', [{}])[js.get('scene', 0)] if js.get('scenes') else {'nodes': list(range(len(js.get('nodes', []))))}
    for ni in scene.get('nodes', []):
        walk(ni, ident)
    return tris


def read_mesh(path, units=None, up=None):
    """Triangles in engine axes (y up) and millimetres, plus the defaults used."""
    ext = os.path.splitext(path)[1].lower()
    tris = _stl(path) if ext == '.stl' else _obj(path) if ext == '.obj' else _glb(path)
    units = units or ('m' if ext == '.glb' else 'mm')
    up = up or ('y' if ext == '.glb' else 'z')
    k = UNITS[units]
    if up == 'z':
        tris = [tuple((p[0] * k, p[2] * k, -p[1] * k) for p in t) for t in tris]
    else:
        tris = [tuple((p[0] * k, p[1] * k, p[2] * k) for p in t) for t in tris]
    return tris, units, up


def mesh_stats(tris):
    if not tris:
        return None
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    lo, hi = [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]
    vol = 0.0
    area = 0.0
    for a, b, c in tris:
        vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        area += math.sqrt(cr[0] ** 2 + cr[1] ** 2 + cr[2] ** 2) / 2
    return {'min': [round(x, 2) for x in lo], 'max': [round(x, 2) for x in hi], 'size': [round(hi[i] - lo[i], 2) for i in range(3)],
            'triangles': len(tris), 'area_cm2': round(area / 100, 1), 'volume_cm3': round(abs(vol) / 1000, 2)}


def reduce_mesh(tris, limit=MAX_TRIS):
    """Indexed mesh for display. Vertex clustering on a grid until it is under the limit."""
    st = mesh_stats(tris)
    diag = math.sqrt(sum(s * s for s in st['size'])) or 1
    cells = 400
    while True:
        h = diag / cells if len(tris) > limit else 0
        key, verts, idx = {}, [], []
        for t in tris:
            ids = []
            for p in t:
                q = (round(p[0] / h), round(p[1] / h), round(p[2] / h)) if h else p
                if q not in key:
                    key[q] = len(verts)
                    verts.append(p if not h else (q[0] * h, q[1] * h, q[2] * h))
                ids.append(key[q])
            if len(set(ids)) == 3:
                idx.extend(ids)
        if len(idx) // 3 <= limit or cells < 20:
            break
        cells = int(cells * 0.7)
    pos = struct.pack('<%df' % (len(verts) * 3), *[c for v in verts for c in v])
    ind = struct.pack('<%dI' % len(idx), *idx)
    return {'pos': base64.b64encode(pos).decode(), 'idx': base64.b64encode(ind).decode(), 'tris': len(idx) // 3, 'reduced': len(idx) // 3 < len(tris)}


# ---------------------------------------------------------------- validate and pack
def validate_refs(refs, folder, cfg, E, W):
    lst = (refs or {}).get('references', []) or []
    if refs and refs.get('schemaVersion') != 1:
        E.append('context/references.json: schemaVersion must be 1.')
    ids = set()
    per = {}
    for r in lst:
        if r.get('kind') == 'frame' and r.get('frameOf'):
            per[r['frameOf']] = per.get(r['frameOf'], 0) + 1
    for v, n in per.items():
        if n > MAX_FRAMES:
            E.append(f'reference "{v}" has {n} frames; keep it to {MAX_FRAMES} or fewer to save compute.')
    for i, r in enumerate(lst):
        where = f'reference "{r.get("id", i)}"'
        if not SLUG.match(str(r.get('id', ''))):
            E.append(f'reference [{i}] needs an "id" slug.')
            continue
        if r['id'] in ids:
            E.append(f'{where} appears twice.')
        ids.add(r['id'])
        if r.get('kind') not in KINDS:
            E.append(f'{where}: kind must be one of {", ".join(sorted(KINDS))}.')
            continue
        if r.get('role', 'look') not in ROLES:
            E.append(f'{where}: role must be look, target, envelope or context.')
        if r.get('view') and r['view'] not in VIEWS:
            E.append(f'{where}: view must be one of {", ".join(sorted(VIEWS))}.')
        f = r.get('file')
        if f and folder is None:
            # MetaForge: a hosted studio has no context folder, so it never reads
            # a file path from a config. References arrive packed or as chat images.
            E.append(f'{where}: a hosted studio cannot read files; send the reference packed ("data" or "mesh") instead of "file".')
            continue
        if f:
            p = os.path.normpath(os.path.join(folder, f))
            if not os.path.exists(p):
                E.append(f'{where}: file {f} not found under the context folder.')
                continue
            ext = os.path.splitext(p)[1].lower()
            if r['kind'] in ('image', 'sketch', 'frame') and ext not in IMG_TYPES:
                E.append(f'{where}: an image must be PNG, JPEG, GIF or WebP.')
            if r['kind'] == 'mesh' and ext not in MESH_TYPES:
                E.append(f'{where}: a mesh must be STL, OBJ or GLB.')
            if r['kind'] == 'video' and ext not in VIDEO_TYPES:
                E.append(f'{where}: a video must be MP4, WebM or MOV.')
            if ext in IMG_TYPES and os.path.getsize(p) > MAX_IMAGE:
                E.append(f'{where}: {f} is over 2 MB; shrink it with scripts/ref_tools.py shrink.')
        elif r['kind'] != 'video' and r.get('source') != 'chat' and not (folder is None and (r.get('data') or r.get('mesh'))):
            E.append(f'{where} needs a "file", or "source": "chat" for an image seen in the conversation.')
        if r['kind'] == 'mesh':
            if r.get('units') and r['units'] not in UNITS:
                E.append(f'{where}: units must be mm, cm, m or in.')
            if r.get('up') and r['up'] not in ('y', 'z'):
                E.append(f'{where}: up must be y or z.')
            for k in ('at', 'rotate'):
                v = r.get(k)
                if v is not None and not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)):
                    E.append(f'{where}: {k} must be [x, y, z].')
        if r['kind'] == 'video' and len(r.get('frames') or []) > MAX_FRAMES:
            E.append(f'{where} lists {len(r["frames"])} frames; keep it to {MAX_FRAMES} or fewer to save compute.')
        if r['kind'] == 'video' and not r.get('frames'):
            W.append(f'{where} has no frames; extract them with scripts/ref_tools.py frames and list them as frame references.')
        if r['kind'] == 'frame' and r.get('frameOf') and r['frameOf'] not in {x.get('id') for x in lst}:
            E.append(f'{where}: frameOf "{r["frameOf"]}" is not a reference.')
        sc = r.get('scale')
        if sc is not None and not (isinstance(sc, dict) and isinstance(sc.get('px'), (int, float)) and isinstance(sc.get('mm'), (int, float)) and sc['px'] > 0 and sc['mm'] > 0):
            E.append(f'{where}: scale must be {{"px": pixels, "mm": millimetres, "basis": "what was measured"}}.')
        tol = r.get('tolerance')
        if tol is not None and not (isinstance(tol, (int, float)) and 0 < tol < 1):
            E.append(f'{where}: tolerance is a fraction, for example 0.1.')
        for j, x in enumerate(r.get('extracted', []) or []):
            w = f'{where}: extracted[{j}]'
            what = str(x.get('what', ''))
            if not (what in FORM_KEYS or what.split('.')[0] in ('feature', 'color', 'finish', 'use', 'dimension', 'part', 'mount')):
                E.append(f'{w}: "what" must be form.W/D/H/R/F/A/t/P, or start with feature, color, finish, use, dimension, part or mount.')
            if x.get('confidence', 'estimated') not in CONF:
                E.append(f'{w}: confidence must be measured, estimated or read.')
            if what in FORM_KEYS or what.startswith('dimension'):
                if not isinstance(x.get('value'), (int, float)):
                    E.append(f'{w}: a dimension needs a numeric value in mm.')
                if x.get('confidence') == 'measured' and r['kind'] in ('image', 'sketch', 'frame') and not sc:
                    E.append(f'{w}: "measured" from an image needs a scale (a known length in pixels and mm).')
            if not x.get('basis'):
                W.append(f'{w} has no "basis"; say how the value was read from the reference.')
        if r.get('role') in ('target', 'envelope') and r['kind'] in ('image', 'sketch', 'frame') and not any(str(x.get('what', '')).startswith('form.') for x in r.get('extracted', []) or []):
            W.append(f'{where} is a {r["role"]} but gives no form dimensions to check against.')


def pack_refs(refs, folder):
    """References as the page reads them: images as data URIs, meshes reduced and encoded."""
    out, total = [], 0
    for r in (refs or {}).get('references', []) or []:
        q = {k: v for k, v in r.items() if k != 'file'}
        f = r.get('file')
        if f and folder is not None:
            p = os.path.normpath(os.path.join(folder, f))
            ext = os.path.splitext(p)[1].lower()
            q['fileName'] = os.path.basename(p)
            if ext in IMG_TYPES:
                raw = open(p, 'rb').read()
                total += len(raw)
                q['data'] = f'data:{IMG_TYPES[ext]};base64,' + base64.b64encode(raw).decode()
                q['px'] = image_size(p)
            elif ext in MESH_TYPES:
                tris, units, up = read_mesh(p, r.get('units'), r.get('up'))
                q['units'], q['up'] = units, up
                q['stats'] = mesh_stats(tris)
                m = reduce_mesh(tris)
                total += len(m['pos']) + len(m['idx'])
                q['mesh'] = m
        out.append(q)
    if total > MAX_TOTAL:
        raise ValueError(f'references add {total // 1024} KB to the page; keep them under {MAX_TOTAL // 1024 // 1024} MB (shrink images, reduce meshes)')
    return out
