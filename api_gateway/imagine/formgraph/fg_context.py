"""Formgraph product context: real parts, machines, options, models and scenario tests.

A product folder may hold a context/ directory next to product.json:

  context/components.json  real parts with sources and 3D geometry
  context/machines.json    machines and suppliers that can make the parts
  context/options.json     every decision, its candidates and the default pick
  context/models.json      generated models (formulas) for tier 1 and tier 2
  context/scenarios.json   scenario tests asked of the user, bound to models

load_context() reads it, validate_context() checks it, and compile_context()
turns it into what the engine reads: catalogue overrides for the default picks,
part numbers for the BOM, and a _context blob for live switching on the page.

Standard library only.
"""
import ast
import json
import os
import re

from . import fg_refs

# Vendored from formgraph 2.7.0. MetaForge changes: the data
# folder sits beside this file, and the per-user machine registry
# (~/.formgraph/machines.json) is not read: a gateway serves many users, so a
# product's machines come from its own context only.
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MACHINES = os.path.join(HERE, 'data', 'machines.default.json')
UK_SUPPLIERS = os.path.join(HERE, 'data', 'suppliers.uk.json')
USER_MACHINES = None
SCHEMA_VERSION = 1
FILES = ('components', 'machines', 'options', 'models', 'scenarios', 'references', 'manufacturing')
FACES = {'+x', '-x', '+y', '-y', '+z', '-z'}
ACCESS = {'usb', 'button', 'led', 'sound', 'sensor', 'antenna', 'sky', 'connector', 'vent', 'cable', 'other'}
KEEPOUT = {'antenna', 'sky', 'airflow', 'heat', 'magnetic', 'clearance'}
ROLES_FIXED = {'board', 'battery', 'fixing', 'material', 'other'}
PLACES = {'floor', 'ceil', 'wall', 'any'}
BUSES = {'pwr', 'i2c', 'uart', 'spi', 'gpio', 'usb', 'rf', 'pwm', 'can', 'analog'}
OPS = {'>=', '<=', '>', '<', '=='}
FUNCS = {'min', 'max', 'sqrt', 'pow', 'abs', 'exp', 'log', 'log10', 'sin', 'cos', 'tan', 'atan', 'floor', 'ceil', 'round', 'clamp'}
CONSTS = {'pi', 'g', 'e'}
# Quantities the page computes and models may read with "from"
PAGE_QUANTITIES = {
    'bom.mass_g', 'bom.cost_gbp', 'form.W', 'form.D', 'form.H', 'form.t', 'form.volume_cm3',
    'power.avg_mW', 'power.peak_mA', 'battery.wh', 'battery.mah', 'env.opMin', 'env.opMax', 'env.altitude_m',
    'process.qty',
}
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}$')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}$')
URL = re.compile(r'https?://\S+$')
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*$')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def context_dir(cfg, cfg_path):
    """The context folder for a config, or None when the config does not use one."""
    rel = cfg.get('context')
    if not rel:
        return None
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(cfg_path)), rel))


def load_context(folder):
    """Read every context file that exists. Missing files are empty."""
    ctx = {}
    for k in FILES:
        p = os.path.join(folder, f'{k}.json')
        ctx[k] = _load(p) if os.path.exists(p) else {}
    ctx['_folder'] = folder
    return ctx


def merged_machines(ctx):
    """Product machines, then the user's registry, then the built-in UK suppliers, then typical defaults. Earlier ids win."""
    out, seen, mats, procs = [], set(), [], {}
    sources = [ctx.get('machines') or {}]
    if USER_MACHINES and os.path.exists(USER_MACHINES):
        try:
            sources.append(_load(USER_MACHINES))
        except (OSError, json.JSONDecodeError):
            pass
    if os.path.exists(UK_SUPPLIERS):
        sources.append(_load(UK_SUPPLIERS))
    sources.append(_load(DEFAULT_MACHINES))
    for src in sources:
        for m in src.get('machines', []) or []:
            if m.get('id') and m['id'] not in seen:
                seen.add(m['id'])
                out.append(m)
        for m in src.get('materials', []) or []:
            if m.get('id') and all(x['id'] != m['id'] for x in mats):
                mats.append(m)
        for k, v in (src.get('processes') or {}).items():
            procs.setdefault(k, v)
    return out, mats, procs


# ---------------------------------------------------------------- formulas
class FormulaError(ValueError):
    pass


def check_formula(expr, names):
    """Parse a formula in the safe grammar and return the names it uses.

    Grammar: numbers, names, + - * / ** %, parentheses, unary minus, and calls to
    FUNCS. Names must be in `names` or CONSTS."""
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError('empty formula')
    if '^' in expr:
        raise FormulaError('use ** for powers, not ^')
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as e:
        raise FormulaError(f'cannot parse: {e.msg}') from None
    used = set()

    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)):
            walk(n.left)
            walk(n.right)
            return
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return walk(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return
        if isinstance(n, ast.Name):
            if n.id in CONSTS:
                return
            if n.id not in names:
                raise FormulaError(f'unknown name "{n.id}"')
            used.add(n.id)
            return
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
            for a in n.args:
                walk(a)
            return
        raise FormulaError(f'not allowed: {type(n).__name__}')

    walk(tree)
    return used


def model_outputs(ctx):
    """{"model.output": unit} for every model output."""
    out = {}
    for m in (ctx.get('models') or {}).get('models', []) or []:
        for k, o in (m.get('outputs') or {}).items():
            out[f'{m.get("id")}.{k}'] = (o or {}).get('unit', '')
    return out


def model_inputs(ctx):
    out = {}
    for m in (ctx.get('models') or {}).get('models', []) or []:
        for k, i in (m.get('inputs') or {}).items():
            out[f'{m.get("id")}.{k}'] = i or {}
    return out


# ---------------------------------------------------------------- validation
def validate_context(cfg, ctx, cat, E, W):
    """Check the context against the config and catalogue. Appends to E and W."""
    ext = cfg.get('catalogue', {}) or {}
    known_fn = set(cat['functions']) | set(cat.get('support_blocks', {})) | {k for k in (ext.get('blocks') or {}) if k.startswith('e_')}
    comps = ctx.get('components') or {}
    parts = comps.get('parts', []) or []
    if comps and comps.get('schemaVersion') != SCHEMA_VERSION:
        E.append(f'context/components.json: schemaVersion must be {SCHEMA_VERSION}.')
    pid = {}
    for i, p in enumerate(parts):
        where = f'context part [{i}]'
        if not isinstance(p, dict) or not SLUG.match(str(p.get('id', ''))):
            E.append(f'{where} needs an "id" slug.')
            continue
        where = f'context part "{p["id"]}"'
        if p['id'] in pid:
            E.append(f'{where} appears twice.')
        pid[p['id']] = p
        role = str(p.get('role', ''))
        if role.startswith('fn:'):
            if role[3:] not in known_fn:
                E.append(f'{where}: role "{role}" names no known function or block.')
        elif role not in ROLES_FIXED:
            E.append(f'{where}: role must be fn:<function id>, board, battery, fixing, material or other.')
        if p.get('excluded'):
            if not p.get('standIn'):
                W.append(f'{where} is excluded by the user; give it "standIn": true and typical specs, or remove it.')
            continue
        miss = [k for k in ('name', 'mpn', 'manufacturer', 'datasheet', 'retrieved') if not p.get(k)]
        if miss:
            E.append(f'{where} is missing {", ".join(miss)}. Every component is a real part with a source, unless the user excludes it.')
        if p.get('datasheet') and not URL.match(str(p['datasheet'])):
            E.append(f'{where}: datasheet must be an http(s) link.')
        if p.get('retrieved') and not DATE.match(str(p['retrieved'])):
            E.append(f'{where}: retrieved must be a date like 2026-11-06.')
        c = p.get('cost')
        if c is not None and not (isinstance(c, dict) and isinstance(c.get('unit'), (int, float)) and c['unit'] >= 0):
            E.append(f'{where}: cost must be {{"unit": GBP, "qty": n, "source": "..."}}.')
        validate_geometry(where, p, E, W)
        validate_interfaces(where, p, E, W)
        for k, val in (p.get('sets') or {}).items():
            if k not in model_inputs(ctx):
                E.append(f'{where}: sets "{k}", which is not a model input (model.input).')
            elif not isinstance(val, (int, float)):
                E.append(f'{where}: sets {k} to a non-number.')

    # machines
    machines, mats, procs = merged_machines(ctx)
    mid = {m['id']: m for m in machines}
    for m in (ctx.get('machines') or {}).get('machines', []) or []:
        where = f'context machine "{m.get("id")}"'
        if not m.get('id') or not m.get('process'):
            E.append(f'{where} needs "id" and "process".')
            continue
        if m['process'] not in cat['processes'] and m['process'] not in procs and m['process'] != 'pcba':
            E.append(f'{where}: process "{m["process"]}" is unknown; add it under "processes" with a ruleBase of {", ".join(cat["processes"])}.')
        if not m.get('source'):
            E.append(f'{where} needs a "source": user, default or a link.')
        bv = m.get('buildVolume')
        if bv is not None and not (isinstance(bv, list) and len(bv) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in bv)):
            E.append(f'{where}: buildVolume must be [x, y, z] in mm.')
        for k, v in (m.get('limits') or {}).items():
            if k != 'minPart' and k != 'boardMax' and not (isinstance(v, (int, float)) and v >= 0):
                E.append(f'{where}: limits.{k} must be a number.')
    for k, v in procs.items():
        if v.get('ruleBase') not in cat['processes']:
            E.append(f'context process "{k}": ruleBase must be one of {", ".join(cat["processes"])}.')
    pr = cfg.get('process') or {}
    if pr.get('machine') and pr['machine'] not in mid:
        E.append(f'process.machine "{pr["machine"]}" is not in context/machines.json, the user registry or the defaults.')
    if pr.get('machine') in mid and mid[pr['machine']].get('process') != pr.get('id'):
        E.append(f'process.machine "{pr["machine"]}" is a {mid[pr["machine"]].get("process")} machine, but process.id is "{pr.get("id")}".')

    # options
    opts = (ctx.get('options') or {}).get('decisions', []) or []
    mat_ids = known_materials(mats, cat, opts)
    seen_roles = set()
    for i, d in enumerate(opts):
        where = f'context option "{d.get("id", i)}"'
        kind = d.get('kind')
        cands = d.get('candidates') or []
        if kind not in ('part', 'machine', 'material', 'fixing', 'variant'):
            E.append(f'{where}: kind must be part, machine, material, fixing or variant.')
            continue
        if kind == 'variant':
            vids = {v.get('id') for v in d.get('variants') or []}
            mins = model_inputs(ctx)
            for v in d.get('variants') or []:
                if not v.get('id') or not v.get('label'):
                    E.append(f'{where}: each variant needs "id" and "label".')
                if not v.get('source'):
                    W.append(f'{where}: variant "{v.get("id")}" has no "source".')
                for k, val in (v.get('set') or {}).items():
                    if k not in mins:
                        E.append(f'{where}: variant "{v.get("id")}" sets "{k}", which is not a model input (model.input).')
                    elif not isinstance(val, (int, float)):
                        E.append(f'{where}: variant "{v.get("id")}" sets {k} to a non-number.')
            for c in cands:
                if c not in vids:
                    E.append(f'{where}: candidate "{c}" is not one of its variants.')
            if d.get('pick') not in cands:
                E.append(f'{where}: pick must be one of the candidates.')
            continue
        if not cands:
            E.append(f'{where} has no candidates.')
            continue
        pool = pid if kind == 'part' else mid if kind == 'machine' else mat_ids if kind == 'material' else set(cat['fixings']) | set((ext.get('fixings') or {}))
        for c in cands:
            if c not in pool:
                E.append(f'{where}: candidate "{c}" is not a known {kind}.')
        if d.get('pick') not in cands:
            E.append(f'{where}: pick must be one of the candidates.')
        if not d.get('why'):
            W.append(f'{where} has no "why" for the default pick.')
        if kind == 'part':
            roles = {pid[c].get('role') for c in cands if c in pid}
            if len(roles) > 1:
                E.append(f'{where}: candidates mix roles {", ".join(sorted(map(str, roles)))}.')
            seen_roles |= roles
            if len([c for c in cands if c in pid and not pid[c].get('excluded')]) < 2:
                W.append(f'{where} has fewer than two real candidates; the user has nothing to compare.')
    if parts:
        for t in cfg.get('functions', []) or []:
            if f'fn:{t}' not in seen_roles:
                W.append(f'function "{t}" has no part option in context; it uses typical catalogue values, not a real part.')
        if (cfg.get('power') or {}).get('board') not in (None, 'none') and 'board' not in seen_roles:
            W.append('the board has no part option in context; it uses typical catalogue values.')
    # every picked component needs its 3D before the page builds, so the CAD is complete
    picked = {d.get('pick') for d in opts if d.get('kind') == 'part'}
    for pk in sorted(x for x in picked if x in pid):
        p = pid[pk]
        g = p.get('geometry') or {}
        if not g.get('envelope'):
            E.append(f'context part "{pk}" is picked but has no 3D geometry. Design it first: at least geometry.envelope [L, W, H] from its drawing, with a source.')
    if machines and not any(d.get('kind') == 'machine' for d in opts) and ctx.get('options'):
        W.append('context/options.json has no machine decision; the user cannot compare machines.')

    # models
    outs = {}
    for i, m in enumerate((ctx.get('models') or {}).get('models', []) or []):
        where = f'context model "{m.get("id", i)}"'
        if not m.get('id') or not NAME.match(str(m['id'])):
            E.append(f'{where} needs an "id" (letters, digits, underscore).')
            continue
        if not m.get('source'):
            E.append(f'{where} needs a "source" for its formulas.')
        names = set()
        for k, inp in (m.get('inputs') or {}).items():
            if not NAME.match(k):
                E.append(f'{where}: input name "{k}" must be letters, digits and underscore.')
            inp = inp or {}
            if 'from' in inp:
                if inp['from'] not in PAGE_QUANTITIES:
                    E.append(f'{where}: input {k} reads unknown page quantity "{inp["from"]}". Known: {", ".join(sorted(PAGE_QUANTITIES))}.')
            elif not isinstance(inp.get('value'), (int, float)):
                E.append(f'{where}: input {k} needs a numeric "value" or a "from".')
            elif not inp.get('source'):
                W.append(f'{where}: input {k} has no "source".')
            sp = inp.get('spread')
            if sp is not None and not (isinstance(sp, (int, float)) and 0 <= sp < 1):
                E.append(f'{where}: input {k} spread is a fraction, for example 0.1 for plus or minus 10 %.')
            names.add(k)
        if not m.get('outputs'):
            E.append(f'{where} has no outputs.')
        for k, o in (m.get('outputs') or {}).items():
            try:
                check_formula((o or {}).get('expr'), names)
            except FormulaError as e:
                E.append(f'{where}: output {k}: {e}.')
            names.add(k)
            outs[f'{m["id"]}.{k}'] = o
    ins = model_inputs(ctx)
    for c in (cfg.get('programme') or {}).get('customRequirements', []) or []:
        b = c.get('model')
        if not b:
            continue
        if b not in outs:
            E.append(f'customRequirements.{c.get("id")}.model "{b}" is not a model output.')
        if c.get('op', '>=') not in OPS or not isinstance(c.get('limit'), (int, float)):
            E.append(f'customRequirements.{c.get("id")}: a model binding needs "op" ({", ".join(sorted(OPS))}) and a numeric "limit".')

    # made parts: each with its own process, material and supplier
    validate_manufacturing(ctx, machines, mats, cat, E, W)

    # references
    fg_refs.validate_refs(ctx.get('references'), ctx.get('_folder'), cfg, E, W)

    # scenarios
    sc = (ctx.get('scenarios') or {}).get('scenarios', []) or []
    req_ids = set(cat['requirements']) | {c.get('id') for c in (cfg.get('programme') or {}).get('customRequirements', []) or []}
    for i, s in enumerate(sc):
        where = f'context scenario "{s.get("id", i)}"'
        if not s.get('id') or not s.get('text'):
            E.append(f'{where} needs "id" and "text".')
            continue
        if s.get('by') not in ('user', 'inferred'):
            E.append(f'{where}: "by" must be user or inferred.')
        for k, v in (s.get('set') or {}).items():
            if k not in ins:
                E.append(f'{where}: set "{k}" is not a model input (model.input).')
            elif not isinstance(v, (int, float)):
                E.append(f'{where}: set {k} must be a number.')
        for x in s.get('expect', []) or []:
            if x.get('output') not in outs:
                E.append(f'{where}: expects unknown output "{x.get("output")}".')
            if x.get('op') not in OPS or not isinstance(x.get('value'), (int, float)):
                E.append(f'{where}: each expectation needs op and a numeric value.')
        for r in s.get('requires', []) or []:
            if r not in req_ids:
                E.append(f'{where}: requires unknown requirement "{r}".')
        if not s.get('expect') and not s.get('requires') and not s.get('events'):
            W.append(f'{where} has no expectation; it cannot pass or fail.')
    if ctx.get('components') and not sc:
        W.append('context has no scenario tests; ask the user which scenarios the product must survive.')
    elif sc and not any(s.get('by') == 'user' for s in sc):
        W.append('every scenario test is inferred; confirm them with the user.')


def known_materials(mats, cat, uses=()):
    """Material ids the context may name (MetaForge, FORGE-827).

    The context's own materials, every catalogue material, and any library
    material named for a catalogue family (prusament_petg screens as petg).
    ``uses`` are the option decisions or candidates that name materials.
    """
    from .build_page import resolve_material

    ids = {m['id'] for m in mats} | {m for p in cat['processes'].values() for m in p['materials']}
    named = set()
    for u in uses or ():
        if isinstance(u, dict):
            named |= set(u.get('candidates') or []) if u.get('kind') == 'material' else {u.get('material')}
    for n in named:
        if isinstance(n, str) and n not in ids and any(resolve_material(p, n) for p in cat['processes'].values()):
            ids.add(n)
    return ids


MFG_SHAPES = {'tube', 'plate', 'block'}
MFG_FROM = {'airframe.arm', 'airframe.hinge', 'airframe.leg', 'airframe.motor_mount', 'airframe.battery_tray', 'airframe.gimbal_mount'}
JOINS = {'screw', 'bolt', 'rivet', 'snap', 'bond', 'clamp', 'press', 'none'}


def validate_manufacturing(ctx, machines, mats, cat, E, W):
    mf = ctx.get('manufacturing') or {}
    if not mf:
        return
    if mf.get('schemaVersion') != SCHEMA_VERSION:
        E.append(f'context/manufacturing.json: schemaVersion must be {SCHEMA_VERSION}.')
    mid = {m['id']: m for m in machines}
    mat_ids = known_materials(mats, cat, [c for p in mf.get('parts', []) or [] for c in p.get('candidates') or []])
    ids = set()
    for i, p in enumerate(mf.get('parts', []) or []):
        where = f'made part "{p.get("id", i)}"'
        if not SLUG.match(str(p.get('id', ''))):
            E.append(f'made part [{i}] needs an "id" slug.')
            continue
        if p['id'] in ids:
            E.append(f'{where} appears twice.')
        ids.add(p['id'])
        if not p.get('name'):
            E.append(f'{where} needs a "name".')
        if not isinstance(p.get('qty', 1), int) or p.get('qty', 1) < 1:
            E.append(f'{where}: qty must be a whole number per product.')
        if p.get('shape') not in MFG_SHAPES:
            E.append(f'{where}: shape must be tube, plate or block.')
        fr = p.get('from')
        dims = p.get('dims')
        if fr and fr not in MFG_FROM:
            E.append(f'{where}: from must be one of {", ".join(sorted(MFG_FROM))}.')
        if not fr and not (isinstance(dims, list) and len(dims) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in dims)):
            E.append(f'{where} needs "from" (a generated airframe part) or "dims" [L, W, H] in mm.')
        for k in ('wall', 'thickness'):
            if k in p and not (isinstance(p[k], (int, float)) and p[k] > 0):
                E.append(f'{where}: {k} must be a positive number in mm.')
        cands = p.get('candidates') or []
        if not cands:
            E.append(f'{where} has no candidates (machine and material pairs).')
        for c in cands:
            if c.get('machine') not in mid:
                E.append(f'{where}: machine "{c.get("machine")}" is not in the context, the user registry or the defaults.')
            if c.get('material') not in mat_ids:
                E.append(f'{where}: material "{c.get("material")}" is not known.')
        if cands and p.get('pick') not in [c.get('machine') for c in cands]:
            E.append(f'{where}: pick must be the machine of one of its candidates.')
        if len({mid.get(c.get('machine'), {}).get('process') for c in cands}) < 2 and len(cands) < 2:
            W.append(f'{where} has one way to make it; add another process or supplier to compare.')
        j = p.get('join') or {}
        if j.get('method', 'none') not in JOINS:
            E.append(f'{where}: join.method must be one of {", ".join(sorted(JOINS))}.')
        if 'count' in j and not (isinstance(j['count'], int) and j['count'] >= 0):
            E.append(f'{where}: join.count must be a whole number per part.')
        if j.get('method') in ('screw', 'bolt', 'rivet') and not j.get('fastener'):
            W.append(f'{where}: name the fastener for its {j["method"]} joint.')


def validate_interfaces(where, p, E, W):
    """Each interface: id, bus, dir, and for power the voltage window and current."""
    ifs = p.get('interfaces')
    if ifs is None:
        if str(p.get('role', '')).startswith('fn:') or p.get('role') in ('board', 'battery'):
            W.append(f'{where} has no interfaces; wiring uses the catalogue ports for its role.')
        return
    seen = set()
    for j, f in enumerate(ifs):
        w = f'{where}: interfaces[{j}]'
        if not f.get('id') or not NAME.match(str(f['id'])):
            E.append(f'{w} needs an "id" (letters, digits, underscore).')
            continue
        if f['id'] in seen:
            E.append(f'{w}: "{f["id"]}" appears twice.')
        seen.add(f['id'])
        if f.get('bus') not in BUSES:
            E.append(f'{w}: bus must be one of {", ".join(sorted(BUSES))}.')
        if f.get('dir') not in ('in', 'out', 'bi'):
            E.append(f'{w}: dir must be in, out or bi.')
        v = f.get('v')
        if v is not None and not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) and v[0] <= v[1]):
            E.append(f'{w}: v must be [min, max] in volts.')
        if f.get('bus') == 'pwr' and v is None:
            E.append(f'{w}: a power interface needs its voltage window "v".')
        for k in ('imax_mA', 'level_V'):
            if k in f and not (isinstance(f[k], (int, float)) and f[k] > 0):
                E.append(f'{w}: {k} must be a positive number.')
        c = f.get('connector')
        if c is not None and not (isinstance(c, dict) and c.get('family') and isinstance(c.get('pins'), int) and c['pins'] > 0):
            E.append(f'{w}: connector needs "family" and a whole number of "pins".')
        at = f.get('at')
        if at is not None and not (isinstance(at, list) and len(at) == 3 and all(isinstance(x, (int, float)) for x in at)):
            E.append(f'{w}: at must be [x, y, z] in mm from the part centre.')


def part_ports(p):
    """Engine ports from interfaces: ins, outs, input voltage windows and output rails."""
    ins, outs, inV, out, lvl, conn = [], [], {}, {}, {}, {}
    board = p.get('role') == 'board'
    for f in p.get('interfaces') or []:
        k, bus = f['id'], f['bus']
        if board and bus not in ('pwr', 'usb', 'rf'):
            # the engine wires peripherals to the board port named after the bus
            k = bus
            if any(o[0] == k for o in outs):
                continue
            outs.append([k, bus])
        elif f['dir'] == 'out':
            outs.append([k, bus])
            if bus == 'pwr':
                out[k] = {'v': f.get('v'), 'imax': f.get('imax_mA', 500)}
        else:
            ins.append([k, bus])
            if bus == 'pwr' and f.get('v'):
                inV[k] = f['v']
        if f.get('level_V'):
            lvl[k] = f['level_V']
        if f.get('connector'):
            conn[k] = f['connector']
    r = {'ins': ins, 'outs': outs}
    for name, v in (('inV', inV), ('out', out), ('lvl', lvl), ('conn', conn)):
        if v:
            r[name] = v
    return r


def part_geom(p):
    """Geometry the engine draws and checks: bodies, holes, access faces, keep-outs."""
    g = p.get('geometry') or {}
    if not g.get('envelope'):
        return None
    return {k: g[k] for k in ('envelope', 'body', 'holes', 'access', 'keepouts', 'model', 'source') if g.get(k)}


def validate_geometry(where, p, E, W):
    g = p.get('geometry')
    if not g:
        W.append(f'{where} has no geometry yet; it must have one before it can be picked.')
        return
    env = g.get('envelope')
    if not (isinstance(env, list) and len(env) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in env)):
        E.append(f'{where}: geometry.envelope must be [L, W, H] in mm.')
        return
    if not g.get('source'):
        W.append(f'{where}: geometry has no "source" (the datasheet drawing it came from).')
    for j, b in enumerate(g.get('body', []) or []):
        if b.get('shape') not in ('box', 'cyl'):
            E.append(f'{where}: body[{j}].shape must be box or cyl.')
        for k, n in (('at', 3), ('size', 3)):
            v = b.get(k)
            if not (isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) for x in v)):
                E.append(f'{where}: body[{j}].{k} must be [x, y, z] in mm.')
    for j, h in enumerate(g.get('holes', []) or []):
        if not (isinstance(h, list) and len(h) == 3 and all(isinstance(x, (int, float)) for x in h) and h[2] > 0):
            E.append(f'{where}: holes[{j}] must be [x, y, diameter] in mm, from the part centre.')
    for j, a in enumerate(g.get('access', []) or []):
        if a.get('kind') not in ACCESS:
            E.append(f'{where}: access[{j}].kind must be one of {", ".join(sorted(ACCESS))}.')
        if a.get('face') not in FACES:
            E.append(f'{where}: access[{j}].face must be one of +x, -x, +y, -y, +z, -z.')
        sz = a.get('size')
        if not (isinstance(sz, list) and len(sz) == 2 and all(isinstance(x, (int, float)) and x > 0 for x in sz)):
            E.append(f'{where}: access[{j}].size must be [w, h] in mm.')
    for j, k in enumerate(g.get('keepouts', []) or []):
        if k.get('kind') not in KEEPOUT:
            E.append(f'{where}: keepouts[{j}].kind must be one of {", ".join(sorted(KEEPOUT))}.')
        if k.get('face') not in FACES or not isinstance(k.get('depth'), (int, float)):
            E.append(f'{where}: keepouts[{j}] needs a face and a depth in mm.')
    if g.get('place') and g['place'] not in PLACES:
        E.append(f'{where}: geometry.place must be floor, ceil, wall or any.')
    if g.get('model') and not URL.match(str(g['model'])):
        E.append(f'{where}: geometry.model must be a link to the maker\'s 3D model.')
    validate_fidelity(where, g, E, W)


FIDELITY = ('exact', 'generated', 'approximation', 'placeholder')


def validate_fidelity(where, g, E, W):
    """How the part's 3D was made. A part is never shown as better than it is.

    exact: the maker's or a library's own model was read. generated: built from the
    datasheet's package drawing. approximation: the closest shape we can justify.
    placeholder: nothing usable was found, so a generic block stands in. Every word
    needs its basis in words; the last two are listed as warnings so nobody mistakes
    them for a real part.
    """
    size_basis = g.get('size_basis')
    if size_basis is not None and size_basis not in ('read', 'typical', 'generic'):
        E.append(f'{where}: geometry.size_basis must be one of read, typical, generic.')
    word = g.get('fidelity')
    if word is None:
        W.append(f'{where}: geometry does not say how its 3D was made; set geometry.fidelity to one of {", ".join(FIDELITY)}.')
        return
    if word not in FIDELITY:
        E.append(f'{where}: geometry.fidelity must be one of {", ".join(FIDELITY)}.')
        return
    basis = g.get('fidelity_basis')
    if not (isinstance(basis, str) and basis.strip()):
        E.append(f'{where}: geometry.fidelity_basis must say, in words, what the {word} 3D rests on.')
    if word in ('approximation', 'placeholder'):
        W.append(f'{where}: its 3D is a{"n" if word == "approximation" else ""} {word}, not the real part ({str(basis or "no basis given").strip()}).')


# ---------------------------------------------------------------- compile
def part_block(p):
    """Engine block override for a part (fn role)."""
    s = dict(p.get('specs') or {})
    if p.get('interfaces'):
        for k, v in part_ports(p).items():
            s.setdefault(k, v)
    g = p.get('geometry') or {}
    if g.get('envelope') and 'dims' not in s:
        L, Wd, H = g['envelope']
        s['dims'] = [L, H, Wd]  # engine block dims are [x, y (height), z]
    if g.get('place') and g['place'] != 'any':
        s.setdefault('place', 'floor' if g['place'] == 'wall' else g['place'])
    if isinstance(p.get('cost'), dict):
        s['cost'] = p['cost'].get('unit', s.get('cost'))
    if isinstance(p.get('mass'), (int, float)):
        s['mass'] = p['mass']
    geom = part_geom(p)
    if geom:
        s['geom'] = geom
        kinds = [a.get('kind') for a in geom.get('access', [])]
        for k in ('button', 'led', 'sound'):
            if k in kinds:
                s.setdefault('open', k)
                break
        for k in geom.get('keepouts', []):
            if k.get('kind') == 'sky':
                s.setdefault('only', 'ceil')
                s.setdefault('place', 'ceil')
            if k.get('kind') == 'antenna':
                s.setdefault('keep', k.get('depth'))
    if p.get('excluded'):
        s['standIn'] = True
    s['name'] = p.get('name') or s.get('name')
    s['mpn'] = p.get('mpn')
    s['mfr'] = p.get('manufacturer')
    s['src'] = p.get('datasheet')
    return {k: v for k, v in s.items() if v is not None}


def part_board(p):
    s = dict(p.get('specs') or {})
    g = p.get('geometry') or {}
    if g.get('envelope'):
        s.setdefault('L', g['envelope'][0])
        s.setdefault('Wd', g['envelope'][1])
        s.setdefault('h', g['envelope'][2])
    holes = g.get('holes') or []
    if holes and 'holes' not in s:
        xs = sorted({h[0] for h in holes})
        ys = sorted({h[1] for h in holes})
        s['holes'] = [round(xs[-1] - xs[0], 2), round(ys[-1] - ys[0], 2)]
        s.setdefault('holeOff', round((xs[-1] + xs[0]) / 2, 2))
    if 'elec' not in s:
        vin = next((f.get('v') for f in p.get('interfaces') or [] if f.get('bus') == 'pwr' and f.get('dir') == 'in' and f.get('v')), None)
        if vin:
            vn = (vin[0] + vin[1]) / 2
            active = s.get('active_mA') or round((s.get('power') or 1.0) * 1000 / vn, 1)
            buses = [f.get('bus') for f in p.get('interfaces') or []]
            s['elec'] = {'vin': vin, 'sleep': s.get('sleep_mA', round(active * 0.3, 2)), 'active': active, 'wake': 0.05,
                         'clock': s.get('clock_MHz', 100), 'gpio': max(4, buses.count('gpio') + buses.count('pwm') * 4),
                         'uart': max(1, buses.count('uart')), 'spi': buses.count('spi'), 'wireless': bool(s.get('wireless'))}
    s.setdefault('power', round(((s.get('elec') or {}).get('active', 0) * sum((s.get('elec') or {}).get('vin', [0, 0])) / 2) / 1000, 2) or None)
    s['name'] = p.get('name')
    s['mfr'] = p.get('manufacturer')
    s['src'] = p.get('datasheet')
    s['mpn'] = p.get('mpn')
    if part_geom(p):
        s['geom'] = part_geom(p)
    if p.get('excluded'):
        s['standIn'] = True
    if isinstance(p.get('cost'), dict):
        s['cost'] = p['cost'].get('unit')
    if isinstance(p.get('mass'), (int, float)):
        s['mass'] = p['mass']
    return {k: v for k, v in s.items() if v is not None}


def part_battery(p):
    s = dict(p.get('specs') or {})
    g = p.get('geometry') or {}
    if g.get('envelope') and 'L' not in s:
        L, Wd, H = g['envelope']
        if s.get('shape') == 'cyl':
            s.setdefault('d', Wd)
            s['L'] = L
        else:
            s.setdefault('shape', 'box')
            s['L'], s['w'], s['t'] = L, Wd, H
    s['name'] = p.get('name')
    s['eg'] = p.get('mpn')
    if part_geom(p):
        s['geom'] = part_geom(p)
    if isinstance(p.get('cost'), dict):
        s['cost'] = p['cost'].get('unit')
    if isinstance(p.get('mass'), (int, float)):
        s['mass'] = p['mass']
    return {k: v for k, v in s.items() if v is not None}


def compile_context(cfg, ctx):
    """Return a new config with the default picks applied and a _context blob for the page."""
    cfg = json.loads(json.dumps(cfg))
    parts = {p['id']: p for p in (ctx.get('components') or {}).get('parts', []) or [] if isinstance(p, dict) and p.get('id')}
    machines, mats, procs = merged_machines(ctx)
    ext = cfg.setdefault('catalogue', {})
    nom = cfg.setdefault('nominations', {})
    decisions = (ctx.get('options') or {}).get('decisions', []) or []
    for d in decisions:
        if d.get('kind') != 'part' or d.get('pick') not in parts:
            continue
        p = parts[d['pick']]
        role = p.get('role', '')
        if role.startswith('fn:'):
            t = role[3:]
            ext.setdefault('blocks', {})[t] = part_block(p)
            if p.get('mpn') and not p.get('excluded'):
                nom[t] = p['mpn']
        elif role == 'board':
            ext.setdefault('boards', {})[p['id']] = part_board(p)
            if p.get('interfaces'):
                ext.setdefault('blocks', {})['e_board'] = part_ports(p)
            cfg.setdefault('power', {})['board'] = p['id']
            if p.get('mpn') and not p.get('excluded'):
                nom['board'] = p['mpn']
        elif role == 'battery':
            ext.setdefault('batteries', {})[p['id']] = part_battery(p)
            cfg.setdefault('power', {})['battery'] = p['id']
            if p.get('mpn') and not p.get('excluded'):
                nom['batt'] = p['mpn']
    for d in decisions:
        if d.get('kind') == 'machine' and d.get('pick'):
            m = next((x for x in machines if x['id'] == d['pick']), None)
            if m:
                cfg.setdefault('process', {})['machine'] = m['id']
                cfg['process']['id'] = m['process']
        if d.get('kind') == 'material' and d.get('pick'):
            cfg.setdefault('process', {})['material'] = d['pick']
        if d.get('kind') == 'fixing' and d.get('pick'):
            cfg.setdefault('process', {})['fixings'] = d['pick']
    # extra materials as catalogue materials, keyed by process
    for m in mats:
        lst = ext.setdefault('materials', {}).setdefault(m.get('process'), [])
        if all(x.get('id') != m['id'] for x in lst):
            lst.append({k: v for k, v in m.items() if k != 'process'})
    if not ext:
        cfg.pop('catalogue', None)
    if not nom:
        cfg.pop('nominations', None)
    cfg['_context'] = {
        'schemaVersion': SCHEMA_VERSION,
        'parts': list(parts.values()),
        'machines': machines,
        'materials': mats,
        'processes': procs,
        'options': decisions,
        'models': (ctx.get('models') or {}).get('models', []) or [],
        'scenarios': (ctx.get('scenarios') or {}).get('scenarios', []) or [],
        'compiled': compile_parts(parts),
        'manufacturing': (ctx.get('manufacturing') or {}).get('parts', []) or [],
        'refs': fg_refs.pack_refs(ctx.get('references'), ctx.get('_folder')) if ctx.get('references') else [],
    }
    return cfg


def compile_parts(parts):
    """Engine-ready form of every candidate, so the page can switch options live."""
    out = {}
    for pid, p in parts.items():
        role = p.get('role', '')
        if role.startswith('fn:'):
            out[pid] = {'kind': 'block', 'type': role[3:], 'block': part_block(p)}
        elif role == 'board':
            out[pid] = {'kind': 'board', 'id': pid, 'board': part_board(p), 'ports': part_ports(p) if p.get('interfaces') else None}
        elif role == 'battery':
            out[pid] = {'kind': 'battery', 'id': pid, 'batt': part_battery(p)}
    return out
