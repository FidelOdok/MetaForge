"""Write-time checks for an agent-authored form (spec 23.6).

An authored form is a set of parts. Each part carries the body of one geometry
function, ``build(p, g)``, that the page runs in a sandbox: ``p`` holds the
declared parameters (``form.params``) and ``g`` is a small shape builder. This
module is the first of two layers that keep that code harmless. It refuses, at
write time and naming the part, code that mentions anything the sandbox
should never be asked to run, and code or geometry over the budget. The second
layer is the sandbox itself, which removes the same names from the worker's
scope; a token list alone is never relied on.

It is pure: no I/O, no engine, no clock. ``check`` returns ``(errors,
warnings)`` as lists of strings, the shape ``build_page.validate`` uses.
"""

from __future__ import annotations

import re
from typing import Any

TEMPLATE_ID = "authored"
KINDS = ("generated", "primitive", "component")

# IMG-FORM-006. Per revision, enforced when the config is written.
MAX_PARTS = 24
MAX_PART_CODE_BYTES = 6_000
MAX_TOTAL_CODE_BYTES = 40_000
MAX_PART_TRIANGLES = 20_000
MAX_TOTAL_TRIANGLES = 100_000

# Words the sandbox must never be asked to run. Matching is on whole words, so
# "important" is fine and "import" is not. Naming one anywhere in the code is
# refused, in a comment or a string too: refusing a harmless mention costs the
# agent a rewording, and allowing a hidden one costs the page its sandbox.
BANNED_TOKENS = (
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "importScripts",
    "import",
    "eval",
    "Function",
    "document",
    "window",
    "self",
    "globalThis",
    "postMessage",
    "constructor",
    "prototype",
    "__proto__",
    "require",
    "process",
    "this",
)
_BANNED = re.compile(r"(?<![A-Za-z0-9_$])(" + "|".join(BANNED_TOKENS) + r")(?![A-Za-z0-9_$])")

# Escapes and template strings let code spell a banned name without writing it.
_HIDING = re.compile(r"\\[ux]|`")

_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
_PART_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def check(form: Any) -> tuple[list[str], list[str]]:
    """Errors and warnings for ``form`` when ``form.template`` is ``authored``."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(form, dict):
        return ["form must be an object."], warnings

    params = form.get("params") or {}
    declared: set[str] = set()
    if not isinstance(params, dict):
        errors.append("form.params must be an object of numbers.")
    else:
        for name, value in params.items():
            if not _PARAM_NAME.fullmatch(str(name)):
                errors.append(
                    f'form.params: "{name}" is not a usable name '
                    "(letters, digits and underscore, starting with a letter)."
                )
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"form.params.{name} must be a number.")
            else:
                declared.add(str(name))

    block = form.get("authored")
    parts = block.get("parts") if isinstance(block, dict) else None
    if not isinstance(parts, list) or not parts:
        errors.append("form.authored needs at least one part: a list under form.authored.parts.")
        return errors, warnings
    if len(parts) > MAX_PARTS:
        errors.append(f"form.authored has {len(parts)} parts; at most {MAX_PARTS} are allowed.")

    seen: set[str] = set()
    total_bytes = 0
    total_triangles = 0
    bodies = 0
    for index, part in enumerate(parts[: MAX_PARTS + 1]):
        if not isinstance(part, dict):
            errors.append(f"form.authored.parts[{index}] must be an object.")
            continue
        pid = str(part.get("id", ""))
        label = f'part "{pid}"' if pid else f"part #{index}"
        if not _PART_ID.fullmatch(pid):
            errors.append(f'{label}: id must be a lowercase slug such as "arm".')
        elif pid in seen:
            errors.append(f'{label}: duplicate id "{pid}".')
        seen.add(pid)
        if not part.get("name"):
            errors.append(f"{label}: name is required.")
        if part.get("kind") not in KINDS:
            errors.append(f"{label}: kind must be one of {', '.join(KINDS)}.")

        code = part.get("code")
        if not isinstance(code, str) or not code.strip():
            if not isinstance(part.get("component_ref"), str):
                errors.append(f"{label}: code is required, the body of build(p, g).")
        else:
            size = len(code.encode("utf-8"))
            total_bytes += size
            if size > MAX_PART_CODE_BYTES:
                errors.append(
                    f"{label}: code is {size} bytes; at most {MAX_PART_CODE_BYTES} bytes per part."
                )
            for token in sorted({m.group(1) for m in _BANNED.finditer(code)}):
                errors.append(f'{label}: code uses "{token}", which is not allowed in a form.')
            if _HIDING.search(code):
                errors.append(
                    f"{label}: code uses an escape sequence or a template string, "
                    "which can hide a banned name. Write the value plainly."
                )

        uses = part.get("uses") or []
        if not isinstance(uses, list):
            errors.append(f"{label}: uses must be a list of parameter names.")
        else:
            for name in uses:
                if name not in declared:
                    errors.append(f'{label}: uses "{name}", which is not in form.params.')

        triangles = part.get("max_triangles")
        if triangles is not None:
            if isinstance(triangles, bool) or not isinstance(triangles, int) or triangles < 1:
                errors.append(f"{label}: max_triangles must be a positive whole number.")
            else:
                total_triangles += triangles
                if triangles > MAX_PART_TRIANGLES:
                    errors.append(
                        f"{label}: max_triangles is {triangles}; "
                        f"at most {MAX_PART_TRIANGLES} triangles per part."
                    )
        if part.get("body"):
            bodies += 1

    if total_bytes > MAX_TOTAL_CODE_BYTES:
        errors.append(
            f"form.authored: the total code is {total_bytes} bytes; "
            f"at most {MAX_TOTAL_CODE_BYTES} bytes across all parts."
        )
    if total_triangles > MAX_TOTAL_TRIANGLES:
        errors.append(
            f"form.authored: declared triangles total {total_triangles}; "
            f"at most {MAX_TOTAL_TRIANGLES} across all parts."
        )
    if bodies > 1:
        errors.append('form.authored: only one part may be marked "body": true.')
    if bodies == 0 and not errors:
        warnings.append(
            'form.authored has no part marked "body": true, so the engine cannot place the '
            "electronics inside the form; checks that need an enclosure will report not_assessed."
        )
    return errors, warnings


def check_links(form: Any, context_parts: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Errors and warnings for the links from form parts to real parts.

    A ``component`` part names its real part with ``component_ref`` (a part id in
    ``context.components.parts``) and says where it sits with ``at``. Without a link it is
    a stand-in, and the warning says so.
    """
    errors: list[str] = []
    warnings: list[str] = []
    block = form.get("authored") if isinstance(form, dict) else None
    parts = block.get("parts") if isinstance(block, dict) else None
    if not isinstance(parts, list):
        return errors, warnings
    for part in parts:
        if not isinstance(part, dict):
            continue
        label = f'part "{part.get("id", "")}"'
        ref = part.get("component_ref")
        if ref is None:
            if part.get("kind") == "component":
                warnings.append(
                    f"{label}: a component with no component_ref is a stand-in, not a real "
                    "part; set component_ref to a part in context.components.parts."
                )
            continue
        if part.get("kind") != "component":
            errors.append(f"{label}: component_ref is only for a part whose kind is component.")
            continue
        if not isinstance(ref, str) or ref not in context_parts:
            known = ", ".join(sorted(context_parts)) or "none yet"
            errors.append(
                f'{label}: component_ref "{ref}" is not a part in context.components.parts '
                f"(known: {known})."
            )
            continue
        for key in ("at", "rot"):
            if key in part and not _is_vector(part[key]):
                errors.append(f"{label}: {key} must be [x, y, z], three numbers.")
        real = context_parts[ref]
        env = (
            ((real or {}).get("geometry") or {}).get("envelope") if isinstance(real, dict) else None
        )
        sized = (
            isinstance(env, list)
            and len(env) == 3
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in env)
        )
        has_code = isinstance(part.get("code"), str) and part["code"].strip()
        if not sized and not has_code:
            errors.append(
                f'{label}: its part "{ref}" has no size, so it cannot be drawn; give the part '
                "a size or write the code yourself."
            )
        elif sized and not has_code:
            errors.append(
                f"{label}: its code is derived when the studio is written; write it through "
                "twin.imagine_put_studio."
            )
    return errors, warnings


def _is_vector(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
    )


#: How a part is made and how it looks (MetaForge, 2026-11-11): each part is manufactured
#: on its own, so it names its own process, material, finish and colour.
MAKE_KEYS = ("process", "material", "finish", "color", "source", "why")
MAKE_SOURCES = ("proposed", "person")
MAX_MAKE_WHY = 200
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def _names(table: Any) -> set[str]:
    if isinstance(table, dict):
        return {str(k) for k in table}
    if isinstance(table, list):
        return {str(m.get("id") if isinstance(m, dict) else m) for m in table}
    return set()


def check_make(
    form: Any,
    processes: dict[str, Any],
    colors: list[str],
    default_process: str | None,
    extra_materials: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Errors and warnings for each part's ``make`` block.

    ``processes`` is the catalogue's process table (each with ``name``, ``materials`` and
    ``finishes``), ``colors`` the catalogue's colour ids and ``default_process`` the
    product's own ``process.id``, used when a part names none. Every value must be one the
    page can draw with; an invented one is refused naming the part. A look the agent
    proposed is listed as waiting for the person, never as chosen.
    """
    errors: list[str] = []
    warnings: list[str] = []
    block = form.get("authored") if isinstance(form, dict) else None
    parts = block.get("parts") if isinstance(block, dict) else None
    if not isinstance(parts, list):
        return errors, warnings
    extra = extra_materials or {}
    proposed: list[str] = []
    without: list[str] = []
    any_make = False
    for part in parts:
        if not isinstance(part, dict):
            continue
        pid = str(part.get("id", ""))
        label = f'part "{pid}"'
        make = part.get("make")
        if make is None:
            if part.get("kind") != "component":
                without.append(f'"{pid}"')
            continue
        any_make = True
        if part.get("kind") == "component":
            errors.append(
                f"{label}: a bought component keeps its own look and is not manufactured "
                "here; remove its make block."
            )
            continue
        if not isinstance(make, dict):
            errors.append(f"{label}: make must be an object.")
            continue
        for key in sorted(set(make) - set(MAKE_KEYS)):
            errors.append(f"{label}: make.{key} is not known; use {', '.join(MAKE_KEYS)}.")
        proc_id = str(make.get("process") or default_process or "")
        proc = processes.get(proc_id)
        if not proc:
            errors.append(
                f'{label}: make.process "{proc_id}" is not a process; choose from: '
                f"{', '.join(sorted(processes)) or 'none'}."
            )
        else:
            pname = proc.get("name", proc_id)
            mats = _names(proc.get("materials")) | _names(extra.get(proc_id))
            material = make.get("material")
            if not material:
                errors.append(f"{label}: make.material is required.")
            elif str(material) not in mats:
                errors.append(
                    f'{label}: make.material "{material}" is not available for {pname}. '
                    f"Choose from: {', '.join(sorted(mats))}."
                )
            finish = make.get("finish")
            finishes = _names(proc.get("finishes"))
            if finish is not None and str(finish) not in finishes:
                errors.append(
                    f'{label}: make.finish "{finish}" is not available for {pname}. '
                    f"Choose from: {', '.join(sorted(finishes))}."
                )
        color = make.get("color")
        if not color:
            errors.append(f"{label}: make.color is required, a catalogue colour or #RRGGBB.")
        elif str(color) not in colors and not _HEX.fullmatch(str(color)):
            errors.append(
                f'{label}: make.color "{color}" is neither a catalogue colour '
                f"({', '.join(colors)}) nor a #RRGGBB value."
            )
        source = make.get("source")
        if source not in MAKE_SOURCES:
            errors.append(
                f"{label}: make.source must say who chose it: "
                f'"proposed" (the agent, for the person to confirm) or "person".'
            )
        elif source == "proposed":
            proposed.append(f'"{pid}"')
        why = make.get("why")
        if why is not None and (not isinstance(why, str) or len(why) > MAX_MAKE_WHY):
            errors.append(f"{label}: make.why must be text of at most {MAX_MAKE_WHY} characters.")
    if proposed:
        warnings.append(
            f"How {', '.join(proposed)} {'is' if len(proposed) == 1 else 'are'} made and look "
            "is proposed by the agent, not chosen: the person confirms the process, material "
            "and colour of each."
        )
    if any_make and without:
        warnings.append(
            f"{', '.join(without)} {'has' if len(without) == 1 else 'have'} no make block, so "
            "the studio draws it in the product colour and the product's process; give each "
            "made part its own."
        )
    return errors, warnings


#: How the twin behaves (MetaForge, 2026-11-11): a form says how its parts move, light up
#: and switch, so the studio can show the product working, not only its shape.
BEHAVIOUR_KINDS = ("rotate", "slide", "glow", "toggle", "light")
BEHAVIOUR_KEYS = (
    "id",
    "name",
    "kind",
    "parts",
    "axis",
    "origin",
    "range",
    "color",
    "default",
    "at",
    "direction",
    "angle",
    "intensity",
)
MAX_BEHAVIOURS = 12
MAX_ROTATE_DEG = 360.0
MAX_SLIDE_MM = 1000.0
_BEHAVIOUR_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def _vec3(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        and all(abs(float(v)) < 1e6 for v in value)
    )


def check_behaviours(form: Any) -> tuple[list[str], list[str]]:
    """Errors and warnings for ``form.authored.behaviours``.

    ``rotate`` turns its parts about ``axis`` through ``origin`` (degrees, within
    ``range``); ``slide`` moves them along ``axis`` (millimetres); ``glow`` lights them in
    ``color``; ``toggle`` shows or hides them. Each names real parts of the form.
    """
    errors: list[str] = []
    warnings: list[str] = []
    block = form.get("authored") if isinstance(form, dict) else None
    if not isinstance(block, dict) or "behaviours" not in block:
        return errors, warnings
    items = block.get("behaviours")
    if not isinstance(items, list):
        return ["form.authored.behaviours must be a list."], warnings
    if len(items) > MAX_BEHAVIOURS:
        errors.append(
            f"form.authored.behaviours has {len(items)} behaviours; at most {MAX_BEHAVIOURS}."
        )
    part_ids = {
        str(p.get("id"))
        for p in [*(block.get("parts") or []), *(block.get("scene") or [])]
        if isinstance(p, dict) and p.get("id")
    }
    seen: set[str] = set()
    for index, item in enumerate(items[: MAX_BEHAVIOURS + 1]):
        if not isinstance(item, dict):
            errors.append(f"form.authored.behaviours[{index}] must be an object.")
            continue
        bid = str(item.get("id", ""))
        label = f'behaviour "{bid}"' if bid else f"behaviour #{index}"
        if not _BEHAVIOUR_ID.fullmatch(bid):
            errors.append(f'{label}: id must be a lowercase slug such as "tilt".')
        elif bid in seen:
            errors.append(f'{label}: duplicate id "{bid}".')
        seen.add(bid)
        for key in sorted(set(item) - set(BEHAVIOUR_KEYS)):
            errors.append(f"{label}: {key} is not known; use {', '.join(BEHAVIOUR_KEYS)}.")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            errors.append(f"{label}: name is required, the words a person sees on its control.")
        kind = item.get("kind")
        if kind not in BEHAVIOUR_KINDS:
            errors.append(f"{label}: kind must be one of {', '.join(BEHAVIOUR_KINDS)}.")
        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            errors.append(f"{label}: parts must list the ids of the parts it acts on.")
        else:
            for pid in parts:
                if str(pid) not in part_ids:
                    errors.append(f'{label}: part "{pid}" is not a part of this form.')
        if kind in ("rotate", "slide"):
            axis = item.get("axis")
            if not _vec3(axis) or not any(float(v) for v in axis):
                errors.append(
                    f"{label}: axis must be three numbers, not all zero, such as [0, 1, 0]."
                )
            if "origin" in item and not _vec3(item.get("origin")):
                errors.append(f"{label}: origin must be three numbers in millimetres.")
            rng = item.get("range")
            limit = MAX_ROTATE_DEG if kind == "rotate" else MAX_SLIDE_MM
            unit = "degrees" if kind == "rotate" else "mm"
            if (
                not isinstance(rng, list)
                or len(rng) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rng)
                or not float(rng[0]) < float(rng[1])
                or max(abs(float(v)) for v in rng) > limit
            ):
                errors.append(
                    f"{label}: range must be [min, max] with min below max, within ±{limit:g} {unit}."
                )
        if kind in ("glow", "light") and not (
            isinstance(item.get("color"), str) and _HEX.fullmatch(item["color"])
        ):
            errors.append(f"{label}: color must be the light's #RRGGBB colour.")
        if kind == "light":
            if "at" in item and not _vec3(item.get("at")):
                errors.append(
                    f"{label}: at must be three numbers in millimetres, where the light is."
                )
            d = item.get("direction", [0, -1, 0])
            if not _vec3(d) or not any(float(v) for v in d):
                errors.append(
                    f"{label}: direction must be three numbers, not all zero, such as [0, -1, 0]."
                )
            ang = item.get("angle", 45)
            if isinstance(ang, bool) or not isinstance(ang, (int, float)) or not 5 <= ang <= 90:
                errors.append(f"{label}: angle must be the beam's half-angle, 5 to 90 degrees.")
            inten = item.get("intensity", 1)
            if (
                isinstance(inten, bool)
                or not isinstance(inten, (int, float))
                or not 0 < inten <= 10
            ):
                errors.append(f"{label}: intensity must be above 0 and at most 10.")
    return errors, warnings


#: Variants (MetaForge, 2026-11-11): the same parts with other sizes and makes, offered side
#: by side so the person can pick one.
MAX_VARIANTS = 4
VARIANT_KEYS = ("id", "name", "why", "params", "makes")


def check_variants(
    form: Any,
    processes: dict[str, Any],
    colors: list[str],
    default_process: str | None,
    extra_materials: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Errors and warnings for ``form.authored.variants``.

    A variant may move declared sizes (``params``, within ``form.ranges``) and give real,
    made parts another make (checked like a part's own ``make``). It must change something.
    """
    errors: list[str] = []
    warnings: list[str] = []
    block = form.get("authored") if isinstance(form, dict) else None
    if not isinstance(block, dict) or "variants" not in block:
        return errors, warnings
    items = block.get("variants")
    if not isinstance(items, list):
        return ["form.authored.variants must be a list."], warnings
    if len(items) > MAX_VARIANTS:
        errors.append(f"form.authored.variants has {len(items)} variants; at most {MAX_VARIANTS}.")
    params = form.get("params") or {}
    ranges = form.get("ranges") or {}
    parts = {
        str(p.get("id")): p for p in block.get("parts") or [] if isinstance(p, dict) and p.get("id")
    }
    seen: set[str] = set()
    for index, item in enumerate(items[: MAX_VARIANTS + 1]):
        if not isinstance(item, dict):
            errors.append(f"form.authored.variants[{index}] must be an object.")
            continue
        vid = str(item.get("id", ""))
        label = f'variant "{vid}"' if vid else f"variant #{index}"
        if not _BEHAVIOUR_ID.fullmatch(vid):
            errors.append(f'{label}: id must be a lowercase slug such as "sporty".')
        elif vid in seen:
            errors.append(f'{label}: duplicate id "{vid}".')
        seen.add(vid)
        for key in sorted(set(item) - set(VARIANT_KEYS)):
            errors.append(f"{label}: {key} is not known; use {', '.join(VARIANT_KEYS)}.")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            errors.append(f"{label}: name is required, the words a person sees on it.")
        why = item.get("why")
        if why is not None and (not isinstance(why, str) or len(why) > MAX_MAKE_WHY):
            errors.append(f"{label}: why must be text of at most {MAX_MAKE_WHY} characters.")
        vparams = item.get("params") or {}
        makes = item.get("makes") or {}
        if not isinstance(vparams, dict):
            errors.append(f"{label}: params must map size names to numbers.")
            vparams = {}
        if not isinstance(makes, dict):
            errors.append(f"{label}: makes must map part ids to make blocks.")
            makes = {}
        if not vparams and not makes:
            errors.append(f"{label}: a variant must change something (params or makes).")
        for key, value in vparams.items():
            if key not in params:
                errors.append(f'{label}: size "{key}" is not in form.params.')
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{label}: {key} must be a number.")
            else:
                rng = ranges.get(key)
                if isinstance(rng, list) and len(rng) == 2 and not rng[0] <= value <= rng[1]:
                    errors.append(f"{label}: {key} = {value} is outside its range {rng}.")
        synthetic = []
        for pid, make in makes.items():
            part = parts.get(str(pid))
            if part is None:
                errors.append(f'{label}: part "{pid}" is not a part of this form.')
                continue
            synthetic.append({"id": pid, "kind": part.get("kind"), "make": make})
        if synthetic:
            make_errors, _ = check_make(
                {"authored": {"parts": synthetic}},
                processes,
                colors,
                default_process,
                extra_materials,
            )
            errors.extend(f"{label}: {e}" for e in make_errors)
    return errors, warnings


#: The scene (MetaForge, 2026-11-11): what the product lives with (a desk, a wrist, a hand), so
#: its behaviours have something to act on. Written as code like parts and run in the same
#: sandbox; never measured, costed or handed over.
MAX_SCENE = 8
SCENE_ROLES = ("surface", "body", "hand", "object", "wall")
SCENE_KEYS = ("id", "name", "role", "code", "uses", "max_triangles")


def check_scene(form: Any) -> tuple[list[str], list[str]]:
    """Errors and warnings for ``form.authored.scene``: the same code checks as a part's."""
    errors: list[str] = []
    warnings: list[str] = []
    block = form.get("authored") if isinstance(form, dict) else None
    if not isinstance(block, dict) or "scene" not in block:
        return errors, warnings
    items = block.get("scene")
    if not isinstance(items, list):
        return ["form.authored.scene must be a list."], warnings
    if len(items) > MAX_SCENE:
        errors.append(f"form.authored.scene has {len(items)} objects; at most {MAX_SCENE}.")
    part_ids = {str(p.get("id")) for p in block.get("parts") or [] if isinstance(p, dict)}
    synthetic = []
    for index, item in enumerate(items[: MAX_SCENE + 1]):
        if not isinstance(item, dict):
            errors.append(f"form.authored.scene[{index}] must be an object.")
            continue
        sid = str(item.get("id", ""))
        label = f'scene object "{sid}"'
        for key in sorted(set(item) - set(SCENE_KEYS)):
            errors.append(f"{label}: {key} is not known; use {', '.join(SCENE_KEYS)}.")
        if sid in part_ids:
            errors.append(f"{label}: has the same id as a part; give it its own.")
        role = item.get("role", "object")
        if role not in SCENE_ROLES:
            errors.append(f"{label}: role must be one of {', '.join(SCENE_ROLES)}.")
        synthetic.append(
            {k: v for k, v in item.items() if k in ("id", "name", "code", "uses", "max_triangles")}
            | {"kind": "primitive"}
        )
    if synthetic:
        code_errors, _ = check(
            {"params": form.get("params") or {}, "authored": {"parts": synthetic}}
        )
        errors.extend(
            e.replace('part "', 'scene object "').replace("form.authored", "form.authored.scene")
            for e in code_errors
        )
    return errors, warnings
