"""Build a Formgraph product page from a product config.

Vendored from formgraph 2.7.0 (skills/formgraph/scripts/build_page.py).
MetaForge changes: package-relative imports, the data folder sits beside this
file, and the command-line entry point is removed (the gateway calls
validate() and build() directly; see api_gateway/imagine/README.md).

The engine is fixed. validate() checks a config against the catalogue and
build() embeds it in a copy of the engine. Errors stop the build; warnings
describe what the page will flag.

Standard library only.
"""
import html
import json
import os
import re

from . import authored, fg_context

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
CATALOGUE = os.path.join(ROOT, 'data', 'catalogue.json')
REGISTRY = os.path.join(ROOT, 'data', 'programme.json')
ENGINE_MAJORS = ('formgraph@1', 'formgraph@2')

TOP_KEYS = {'$schema', 'engine', 'product', 'scenarios', 'requirements', 'decisions', 'form', 'functions', 'power',
            'process', 'simulations', 'nominations', 'catalogue', 'level', 'color', 'ambient', 'programme', 'environment', 'context', 'publish', 'airframe', 'view'}

PROG_KEYS = {'phase', 'markets', 'segment', 'sector', 'environment', 'mains', 'safetyFunction', 'mechanism', 'control', 'human',
             'software', 'firmware', 'budgets', 'business', 'lifecycle', 'sustainability', 'packaging', 'production', 'supply',
             'start', 'ledger', 'assumptions', 'customRequirements', 'rationale', 'tradeoffs', 'risks', 'interfaces', 'flows'}
LEDGER_STATUS = {'unreviewed', 'mapped', 'custom', 'unrepresentable', 'out-of-scope'}
VERIFY = {'test', 'analysis', 'inspection', 'demonstration'}
LEVELS = {'subsystem', 'system', 'acceptance'}


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _tokens(material_id):
    return {t for t in re.split(r'[^a-z0-9]+', str(material_id).lower()) if t}


def resolve_material(proc, material_id):
    """The catalogue material a library material screens as, or None (MetaForge, FORGE-827).

    A process may list material "families": each catalogue material with the
    name tokens that identify it. A library material named for its family,
    such as prusament_petg, bambu-petg-cf or polymaker_asa, screens as that
    family's catalogue material (petg, petgcf, asa). The longest match wins,
    so PETG-CF is never read as plain PETG.
    """
    if not proc or not material_id:
        return None
    if material_id in proc.get('materials', {}):
        return material_id
    toks = _tokens(material_id)
    best = None
    for mid, need in (proc.get('families') or {}).items():
        if need and set(need) <= toks and (best is None or len(need) > len(best[1])):
            best = (mid, need)
    return best[0] if best else None


def validate(cfg, cat, ctx=None):
    """Return (errors, warnings) as lists of strings. ctx is the loaded context folder, if any."""
    E, W = [], []
    if not isinstance(cfg, dict):
        return ['The config must be a JSON object.'], W
    for k in cfg:
        if k == '_registry':
            E.append('"_registry" is reserved; the builder adds it.')
        elif k not in TOP_KEYS:
            E.append(f'Unknown top-level key "{k}".')
    for k in ('engine', 'product', 'form', 'process', 'power', 'functions', 'requirements'):
        if k not in cfg:
            E.append(f'Missing "{k}".')
    if E:
        return E, W

    if not str(cfg['engine']).startswith(ENGINE_MAJORS):
        E.append(f'engine must target formgraph@1 or formgraph@2, got "{cfg["engine"]}".')

    p = cfg['product']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,48}', str(p.get('id', ''))):
        E.append('product.id must be a lowercase slug, for example "outdoor-air-sensor".')
    if not p.get('name') or len(p['name']) > 60:
        E.append('product.name is required and must be 60 characters or fewer.')

    ext = cfg.get('catalogue', {}) or {}
    if ctx is not None:
        # validate as the page will see it: default picks applied
        fg_context.validate_context(cfg, ctx, cat, E, W)
        if E:
            return E, W
        cfg = fg_context.compile_context(cfg, ctx)
        ext = cfg.get('catalogue', {}) or {}
        _, cmats, cprocs = fg_context.merged_machines(ctx)
        cat = dict(cat)
        cat['processes'] = dict(cat['processes'])
        for k, v in cprocs.items():
            base = cat['processes'].get(v.get('ruleBase'))
            # MetaForge (FORGE-827): a process the catalogue has natively (fdm)
            # keeps its own rule book; a context entry cannot replace it.
            if base and k not in cat['processes']:
                cat['processes'][k] = dict(base, name=v.get('name', k), materials=v.get('materials', []))

    # form
    f = cfg['form']
    template = f.get('template') if isinstance(f, dict) else None
    if template == 'enclosure':
        E.append(
            'form.template "enclosure" was removed: every form is authored now. Set '
            'form.template to "authored" and build the form from parts, around the real '
            'components in context.components.parts.'
        )
    elif template != authored.TEMPLATE_ID:
        E.append(f'form.template must be "authored" (got {template!r}).')
    else:
        # MetaForge: an agent-authored form (spec 23.6). Its parts carry code, so it is
        # checked by its own module.
        form_errors, form_warnings = authored.check(f)
        E.extend(form_errors)
        W.extend(form_warnings)
        by_id = {
            str(p['id']): p
            for p in (((ctx or {}).get('components') or {}).get('parts') or [])
            if isinstance(p, dict) and p.get('id')
        }
        link_errors, link_warnings = authored.check_links(f, by_id)
        E.extend(link_errors)
        W.extend(link_warnings)

    # process
    pr = cfg['process']
    proc = cat['processes'].get(pr.get('id'))
    if pr.get('machine') and ctx is None:
        E.append('process.machine needs a context folder ("context": "context").')
    if not proc:
        E.append(f'process.id must be one of {", ".join(cat["processes"])}.')
    else:
        mats = set(proc['materials']) | {m.get('id') for m in (ext.get('materials', {}).get(pr['id'], []) or [])}
        family = resolve_material(proc, pr.get('material')) if pr.get('material') not in mats else None
        if family:
            label = proc['materials'][family] if isinstance(proc['materials'], dict) else family
            W.append(f'process.material "{pr.get("material")}" screens as {label} ("{family}"), '
                     'the catalogue material of its family; the twin keeps the name you gave.')
        elif pr.get('material') not in mats:
            E.append(f'process.material "{pr.get("material")}" is not available for {proc["name"]}. Choose from: {", ".join(sorted(mats))}.')
        if pr.get('finish') and pr['finish'] not in proc['finishes']:
            E.append(f'process.finish "{pr["finish"]}" is not available for {proc["name"]}. Choose from: {", ".join(proc["finishes"])}.')
        fixes = set(proc['fixings']) | set((ext.get('fixings') or {}).keys())
        if pr.get('fixings') and pr['fixings'] not in fixes:
            E.append(f'process.fixings "{pr["fixings"]}" cannot be used with {proc["name"]}. Choose from: {", ".join(sorted(fixes))}.')
        q = pr.get('qty')
        if q is not None and (not isinstance(q, int) or q < 1):
            E.append('process.qty must be a whole number of units.')
        if q and pr['id'] == 'im' and q < 1000:
            W.append(f'{q} units is low for injection moulding; the page will suggest a cheaper route.')
        if q and pr['id'] == 'fdm' and q > 200:
            W.append(f'{q} units is high for desktop FDM printing; the page will suggest another route.')
        if q and pr['id'] == 'sls' and q > 5000:
            W.append(f'{q} units is high for SLS printing; the page will suggest moulding.')

    # MetaForge (2026-11-11): each authored part names how it is made and how it looks.
    if template == authored.TEMPLATE_ID:
        behave_errors, behave_warnings = authored.check_behaviours(f)
        E.extend(behave_errors)
        W.extend(behave_warnings)
        make_errors, make_warnings = authored.check_make(
            f, cat['processes'], list(cat.get('colors') or []), pr.get('id'), ext.get('materials') or {}
        )
        E.extend(make_errors)
        W.extend(make_warnings)

    # power
    pw = cfg['power']
    boards = set(cat['boards']) | set((ext.get('boards') or {}).keys())
    batts = set(cat['batteries']) | set((ext.get('batteries') or {}).keys())
    if pw.get('board') not in boards:
        E.append(f'power.board must be one of {", ".join(sorted(boards))}.')
    if pw.get('battery') not in batts:
        E.append(f'power.battery must be one of {", ".join(sorted(batts))}.')
    if pw.get('board') == 'custom':
        c = pw.get('custom') or {}
        miss = [k for k in ('L', 'W', 'H', 'P', 'M') if not isinstance(c.get(k), (int, float)) or c.get(k) <= 0]
        if miss:
            E.append(f'power.custom needs positive numbers for {", ".join(miss)} (length, width, height in mm, power in W, mass in g).')
    if pw.get('board') in ('rpiz2w', 'rpi4') and pw.get('battery') not in (None, 'none'):
        W.append('A 5 V board on a battery needs a 5 V boost; the page offers it as a one-click fix.')

    # functions
    fn = cfg['functions']
    known_fn = set(cat['functions']) | {k for k in (ext.get('blocks') or {}) if k.startswith('e_')}
    for t in fn:
        if t not in known_fn:
            E.append(f'functions: unknown function "{t}". Known: {", ".join(sorted(known_fn))}.')
    radios = [t for t in fn if cat['functions'].get(t, {}).get('radio')]
    if len(radios) > 1:
        E.append(f'functions: choose one radio, not {", ".join(radios)}.')
    if len(fn) != len(set(fn)):
        E.append('functions: each function may appear once.')

    # requirements
    reqs = cfg['requirements']
    ids = set()
    for r in reqs:
        rid = r.get('id')
        spec = cat['requirements'].get(rid)
        if not spec:
            E.append(f'requirements: unknown requirement "{rid}". Known: {", ".join(cat["requirements"])}.')
            continue
        if rid in ids:
            E.append(f'requirements: "{rid}" appears twice.')
        ids.add(rid)
        if r.get('pri') not in cat['priorities']:
            E.append(f'requirements.{rid}.pri must be Must, Should or Off.')
        for k, v in (r.get('values') or {}).items():
            vt = spec['values'].get(k)
            if vt is None:
                E.append(f'requirements.{rid}: unknown value "{k}".')
            elif vt == 'number' and (not isinstance(v, (int, float)) or v <= 0):
                E.append(f'requirements.{rid}.{k} must be a positive number.')
            elif isinstance(vt, list) and v not in vt:
                E.append(f'requirements.{rid}.{k} must be one of {", ".join(vt)}.')
        for k in spec['values']:
            if k not in (r.get('values') or {}):
                W.append(f'requirements.{rid}: no value for "{k}", the engine default is used.')
    must = {r['id'] for r in reqs if r.get('pri') == 'Must'}
    active = {r['id'] for r in reqs if r.get('pri') in ('Must', 'Should')}
    bat = pw.get('battery') not in (None, 'none')
    feats = (cfg['form'].get('features') if isinstance(cfg['form'], dict) else None) or ['slots', 'bosses', 'ribs']
    if 'interval' in active and not radios:
        W.append('"interval" is active but no radio function is chosen; it will show Not met until LTE or LoRa is added.')
    if 'cov' in active and not radios:
        W.append('"cov" is active without a radio; it will show n/a.')
    if 'life' in active and not bat:
        W.append('"life" is active but there is no battery; it will show n/a.')
    if {'touch', 'chgt', 'chgh'} & active and not (bat and 'e_usbc' in fn):
        W.append('Charging requirements are active but there is no battery with USB-C charging; they will show n/a.')
    if 'acc' in active and 'e_th' not in fn:
        W.append('"acc" is active without the temperature sensor; it will show Not met.')
    if 'resp' in active and 'e_btn' not in fn:
        W.append('"resp" is active without a button; it will show Not met.')
    ipv = next((r.get('values', {}).get('ip') for r in reqs if r.get('id') == 'ip'), None)
    membrane = bool((cfg.get('environment') or {}).get('ventMembrane'))
    if 'ip' in active and ipv == 'ip54' and 'slots' in feats and membrane:
        pass  # a vent membrane over the slots keeps IP54
    elif 'ip' in active and ipv in ('ip54', 'ip67') and 'slots' in feats:
        W.append(f'{ipv.upper()} with open vent slots will fail; remove the "slots" feature or plan a vent membrane.')
    if 'ip' in active and ipv == 'ip67' and 'e_usbc' in fn:
        W.append('IP67 with an open USB-C port will fail; it needs a sealed receptacle.')
    if 'ip' in must and ipv in ('ip54', 'ip67') and 'slots' in feats and not (membrane and ipv == 'ip54'):
        W.append('The sealing requirement is a Must and will block gate 3 as configured.')

    # scenarios
    for s in cfg.get('scenarios', []) or []:
        for rid in s.get('requires', []) or []:
            if rid not in cat['requirements']:
                E.append(f'scenario "{s.get("id")}" refers to unknown requirement "{rid}".')
            elif rid not in active:
                W.append(f'scenario "{s.get("id")}" relies on "{rid}", which is Off.')

    # decisions, simulations, misc
    for k, v in (cfg.get('decisions') or {}).items():
        opts = cat['decisions'].get(k)
        if not opts:
            E.append(f'decisions: unknown decision "{k}".')
        elif v not in opts:
            E.append(f'decisions.{k} must be one of {", ".join(opts)}.')
    sims = cfg.get('simulations')
    if sims is not None:
        if not sims:
            E.append('simulations must list at least one test.')
        for k in sims:
            if k not in cat['simulations']:
                E.append(f'simulations: unknown test "{k}".')
        if 'fill' in sims and pr.get('id') != 'im':
            W.append('"fill" only runs for injection moulding; it will show n/a.')
    validate_programme(cfg, E, W)
    validate_environment(cfg, E, W)
    validate_view(cfg, E, W)
    if cfg.get('level') and cfg['level'] not in cat['levels']:
        E.append('level must be simple or eng.')
    if cfg.get('color') and cfg['color'] not in cat['colors']:
        E.append(f'color must be one of {", ".join(cat["colors"])}.')
    return E, W


VIEW_SOURCES = ('model:', 'req:', 'check:', 'env:', 'parts', 'af:', 'bom:')


def validate_view(cfg, E, W):
    """Check the product's own view: key questions, summary, sections, studio order and Try it mode."""
    v = cfg.get('view')
    if v is None:
        if cfg.get('context'):
            W.append('No "view": the page uses the default layout. Write key questions, a summary, section order and a Try it mode for this product.')
        return
    for i, q in enumerate(v.get('keyQuestions', []) or []):
        if not q.get('label') or not str(q.get('source', '')).startswith(VIEW_SOURCES):
            E.append(f'view.keyQuestions[{i}] needs a "label" and a "source" starting with one of {", ".join(VIEW_SOURCES)}.')
        if 'op' in q and q['op'] not in ('>=', '<=', '>', '<', '=='):
            E.append(f'view.keyQuestions[{i}].op must be >=, <=, >, < or ==.')
    for i, x in enumerate(v.get('summary', []) or []):
        if not x.get('label') or not str(x.get('source', '')).startswith(VIEW_SOURCES):
            E.append(f'view.summary[{i}] needs a "label" and a "source".')
    for k in ('sections', 'hide', 'studio'):
        if k in v and not (isinstance(v[k], list) and all(isinstance(t, str) for t in v[k])):
            E.append(f'view.{k} must be a list of names.')
    if v.get('tryit', 'device') not in ('device', 'mission'):
        E.append('view.tryit must be device or mission.')
    if v.get('tryit') == 'mission':
        m = v.get('mission') or {}
        legs = m.get('legs') or []
        if not legs:
            E.append('view.mission needs "legs": [{name, min, power}].')
        for j, l in enumerate(legs):
            if not l.get('name') or not isinstance(l.get('min'), (int, float)) or l['min'] <= 0:
                E.append(f'view.mission.legs[{j}] needs a "name" and minutes "min".')
        if 'reserve' in m and not (isinstance(m['reserve'], (int, float)) and 0 <= m['reserve'] < 1):
            E.append('view.mission.reserve is a fraction, for example 0.2.')


def validate_environment(cfg, E, W):
    """Check the environment block: ranges in order, known options, and the charge window."""
    env = cfg.get('environment')
    if env is None:
        W.append('No "environment" block: the page assumes 0 to 40 C indoors, no sun, 1,000 m, fixed mount.')
        return
    known = {'opMin', 'opMax', 'storeMin', 'storeMax', 'humidity', 'condensing', 'sun', 'altitude_m', 'salt', 'chemicals', 'uvHours', 'vibration', 'outdoor', 'ventMembrane'}
    for k in env:
        if k not in known:
            E.append(f'environment: unknown key "{k}". Known: {", ".join(sorted(known))}.')
    num = lambda k: isinstance(env.get(k), (int, float))
    for k in ('opMin', 'opMax', 'storeMin', 'storeMax', 'altitude_m', 'uvHours'):
        if k in env and not num(k):
            E.append(f'environment.{k} must be a number.')
    if num('opMin') and num('opMax') and env['opMin'] >= env['opMax']:
        E.append('environment.opMin must be below opMax.')
    if num('storeMin') and num('storeMax') and env['storeMin'] >= env['storeMax']:
        E.append('environment.storeMin must be below storeMax.')
    if num('storeMin') and num('opMin') and env['storeMin'] > env['opMin']:
        W.append('environment: storage starts above the operating minimum; storage is usually the wider range.')
    h = env.get('humidity')
    if h is not None and not (isinstance(h, list) and len(h) == 2 and all(isinstance(x, (int, float)) and 0 <= x <= 100 for x in h) and h[0] <= h[1]):
        E.append('environment.humidity must be [min, max] in %RH, for example [10, 90].')
    if env.get('sun', 'none') not in ('none', 'partial', 'full'):
        E.append('environment.sun must be none, partial or full.')
    if env.get('vibration', 'fixed') not in ('fixed', 'handheld', 'vehicle', 'aircraft'):
        E.append('environment.vibration must be fixed, handheld, vehicle or aircraft.')
    if 'altitude_m' in env and num('altitude_m') and not (-500 <= env['altitude_m'] <= 10000):
        E.append('environment.altitude_m must be between -500 and 10,000.')
    if env.get('outdoor') and env.get('sun', 'none') == 'none':
        W.append('environment: outdoor with no sun; set sun to partial or full unless it is always shaded.')
    bat = (cfg.get('power') or {}).get('battery') not in (None, 'none')
    charges = bat and 'e_usbc' in (cfg.get('functions') or [])
    fw = ((cfg.get('programme') or {}).get('firmware') or {})
    if charges and num('opMin') and env['opMin'] < 0 and not fw.get('chargeInhibit'):
        W.append('The product charges a Li-ion cell and can be below 0 C: set programme.firmware.chargeInhibit, or the page fails the charge window.')
    if env.get('ventMembrane') and 'slots' not in ((cfg.get('form') or {}).get('features') or ['slots']):
        W.append('environment.ventMembrane is set but the form has no vent slots; the membrane still helps with condensation.')


def validate_programme(cfg, E, W):
    """Check the programme section: intent ledger, assumptions, custom requirements, markets and records."""
    pg = cfg.get('programme')
    if pg is None:
        W.append('No "programme" section: the programme view runs on defaults, with no intent ledger, assumptions or markets.')
        return
    if not isinstance(pg, dict):
        E.append('programme must be an object.')
        return
    reg = load_json(REGISTRY)
    for k in pg:
        if k not in PROG_KEYS:
            E.append(f'programme: unknown key "{k}". Known: {", ".join(sorted(PROG_KEYS))}.')
    phases = [p['id'] for p in reg['phases']]
    if pg.get('phase') and pg['phase'] not in phases:
        E.append(f'programme.phase must be one of {", ".join(phases)}.')
    for m in pg.get('markets', []) or []:
        if str(m).upper() not in ('UK', 'EU', 'US', 'OTHER'):
            W.append(f'programme.markets: "{m}" has no standards mapping yet; only UK, EU and US drive the compliance list.')
    if not pg.get('markets'):
        W.append('programme.markets is empty; the Define phase will not close until markets are chosen.')
    if pg.get('segment') and pg['segment'] not in ('consumer', 'business', 'industrial'):
        E.append('programme.segment must be consumer, business or industrial.')
    if pg.get('sector') and pg['sector'] not in ('ict', 'industrial', 'luminaire', 'household', 'medical', 'automotive', 'aircraft'):
        E.append('programme.sector must be ict, industrial, luminaire, household, medical, automotive or aircraft.')
    ledger = pg.get('ledger', []) or []
    for i, x in enumerate(ledger):
        if not isinstance(x, dict) or not x.get('text'):
            E.append(f'programme.ledger[{i}] needs "text".')
            continue
        if x.get('status', 'unreviewed') not in LEDGER_STATUS:
            E.append(f'programme.ledger[{i}].status must be one of {", ".join(sorted(LEDGER_STATUS))}.')
    if not ledger:
        W.append('programme.ledger is empty; record every statement of the brief so nothing is silently dropped.')
    cids = set()
    for i, c in enumerate(pg.get('customRequirements', []) or []):
        if not c.get('id') or not c.get('text'):
            E.append(f'programme.customRequirements[{i}] needs "id" and "text".')
            continue
        cids.add(c['id'])
        if c.get('pri', 'Should') not in ('Must', 'Should'):
            E.append(f'customRequirements.{c["id"]}.pri must be Must or Should.')
        if c.get('verify', 'test') not in VERIFY:
            E.append(f'customRequirements.{c["id"]}.verify must be one of {", ".join(sorted(VERIFY))}.')
        if c.get('level', 'system') not in LEVELS:
            E.append(f'customRequirements.{c["id"]}.level must be subsystem, system or acceptance.')
        if c.get('phase', 'dvt') not in phases:
            E.append(f'customRequirements.{c["id"]}.phase must be a phase id.')
        if not c.get('measure'):
            W.append(f'customRequirements.{c["id"]} has no "measure"; say how it will be judged.')
    for i, x in enumerate(ledger):
        if not isinstance(x, dict):
            continue
        if x.get('status') == 'custom' and not any(str(m).startswith('custom:') and str(m)[7:] in cids for m in x.get('maps', [])):
            W.append(f'programme.ledger[{i}] is marked custom but does not map to a custom requirement ("custom:<id>").')
        if x.get('status') == 'unrepresentable':
            W.append(f'programme.ledger[{i}] "{x["text"][:50]}" is not representable; add a custom requirement or mark it out-of-scope.')
    aids = set()
    for i, a in enumerate(pg.get('assumptions', []) or []):
        if not a.get('id') or not a.get('text'):
            E.append(f'programme.assumptions[{i}] needs "id" and "text".')
        elif a['id'] in aids:
            E.append(f'programme.assumptions: duplicate id "{a["id"]}".')
        aids.add(a.get('id'))
    for k, v in (pg.get('budgets') or {}).items():
        if k not in ('mass_g', 'power_mW', 'cost_gbp') or not isinstance(v, (int, float)) or v <= 0:
            E.append(f'programme.budgets.{k}: use mass_g, power_mW or cost_gbp with a positive number.')
    b = pg.get('business') or {}
    if 'price' in b and (not isinstance(b['price'], (int, float)) or b['price'] <= 0):
        E.append('programme.business.price must be a positive number in GBP.')
    if 'margin' in b and not (isinstance(b['margin'], (int, float)) and 0 < b['margin'] < 1):
        E.append('programme.business.margin is a fraction, for example 0.4.')
    for i, r in enumerate(pg.get('risks', []) or []):
        for k in ('S', 'O', 'D'):
            if k in r and not (isinstance(r[k], int) and 1 <= r[k] <= 10):
                E.append(f'programme.risks[{i}].{k} must be a whole number from 1 to 10.')
    rat = pg.get('rationale') or {}
    for k in ('process', 'material', 'board', 'battery', 'size'):
        if k not in rat:
            W.append(f'programme.rationale.{k} is missing; that trade-off will show as open.')
    fw = pg.get('firmware') or {}
    if pg.get('segment') == 'consumer' and 'UK' in [str(m).upper() for m in pg.get('markets', [])]:
        for k in ('uniqueCredentials', 'vulnPolicy', 'supportYears'):
            if not fw.get(k):
                W.append(f'UK PSTI applies to connected consumer products: set programme.firmware.{k}.')
    if pg.get('start') and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(pg['start'])):
        E.append('programme.start must be a date like 2026-11-09.')


def build(cfg, engine_html):
    cfg = dict(cfg)
    cfg['_registry'] = load_json(REGISTRY)
    data = json.dumps(cfg, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    out, n = re.subn(r'<script id="fg-config" type="application/json">.*?</script>',
                     lambda m: f'<script id="fg-config" type="application/json">{data}</script>', engine_html, count=1, flags=re.S)
    if n != 1:
        raise RuntimeError('The engine has no config slot. Rebuild runtime/engine.html.')
    title = html.escape(cfg['product']['name'])
    out = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', out, count=1, flags=re.S)
    return out
