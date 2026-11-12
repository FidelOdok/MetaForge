"""The code that draws a part, and the words it may never use (MetaForge, 2026-11-12).

A studio part and a library part are both drawn by code an agent wrote: the body of a
function that the Imagine page runs in a sandboxed worker. The studio checks a part's code
when the config is written; the component library checks an entry's ``form_code`` when it
is registered, so a model every project reuses is refused once, at the source, rather than
in each studio that places it. Both use this one list. The sandbox removes the same names
from the worker's scope; a token list alone is never relied on.

Layer: stdlib only.
"""

from __future__ import annotations

import re

#: Words the sandbox must never be asked to run. Matching is on whole words, so
#: "important" is fine and "import" is not. Naming one anywhere in the code is refused, in
#: a comment or a string too: refusing a harmless mention costs the agent a rewording, and
#: allowing a hidden one costs the page its sandbox.
BANNED_TOKENS: tuple[str, ...] = (
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
BANNED = re.compile(r"(?<![A-Za-z0-9_$])(" + "|".join(BANNED_TOKENS) + r")(?![A-Za-z0-9_$])")

#: Escapes and template strings let code spell a banned name without writing it.
HIDING = re.compile(r"\\[ux]|`")

#: A library model's code, the same ceiling as one studio part's.
MAX_MODEL_CODE_BYTES = 6_000


def code_problems(code: str) -> list[str]:
    """What is wrong with ``code`` for the sandbox, one line each; empty when it is fine."""
    out = [
        f'uses "{token}", which is not allowed in drawing code'
        for token in sorted({m.group(1) for m in BANNED.finditer(code)})
    ]
    if HIDING.search(code):
        out.append(
            "uses an escape sequence or a template string, which can hide a banned name; "
            "write the value plainly"
        )
    return out


def check_model_code(raw: object) -> str:
    """A library entry's ``form_code``: text, within size, nothing the sandbox refuses.

    Returns the code stripped, or "" for none. Raises ``ValueError`` naming the problem.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError("form_code must be text: the body of a function of g")
    code = raw.strip()
    size = len(code.encode("utf-8"))
    if size > MAX_MODEL_CODE_BYTES:
        raise ValueError(f"form_code is {size} bytes; at most {MAX_MODEL_CODE_BYTES}")
    problems = code_problems(code)
    if problems:
        raise ValueError("form_code " + "; ".join(problems))
    return code
