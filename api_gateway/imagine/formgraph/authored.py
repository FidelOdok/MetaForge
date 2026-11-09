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
