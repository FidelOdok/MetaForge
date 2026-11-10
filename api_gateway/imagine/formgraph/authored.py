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
        env = ((real or {}).get("geometry") or {}).get("envelope") if isinstance(real, dict) else None
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
