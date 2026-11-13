"""Library models in a studio: the code a library part is drawn with (MetaForge, 2026-11-12).

A component library entry may carry ``form_code``, the body of a function of ``g`` that
draws the part once, in mm, centred on its body (x its length, y its height, z its width).
A studio part places it with ``g.model("name")``, so a part researched and drawn in one
project is drawn the same way in every other.

The engine page has no network (its CSP is ``connect-src 'none'``), so on each studio write
the gateway copies every model a part names into ``form.authored.models`` as
``{name: {code, version, entry}}``. The revision then holds the exact code it drew with: a
later change to the library entry reaches a studio on its next write, never silently.

A name the library lacks, or an entry with no ``form_code``, is a note on the write, not a
refusal: the part shows its error on the page until the model exists.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

import structlog

from twin_core.library.form_code import code_problems

logger = structlog.get_logger(__name__)

#: The same pattern the page uses to choose which models a part's worker gets.
MODEL_CALL = re.compile(r"""g\.model\(\s*['"]([A-Za-z0-9_.-]{1,80})['"]\s*\)""")

#: All library models in one studio together, so a page stays small enough to load.
MAX_MODELS_BYTES = 36_000

Lookup = Callable[[str], "dict[str, Any] | None"]


def _parts(config: dict[str, Any]) -> list[dict[str, Any]]:
    form = config.get("form") if isinstance(config, dict) else None
    block = form.get("authored") if isinstance(form, dict) else None
    parts = block.get("parts") if isinstance(block, dict) else None
    return [p for p in parts or [] if isinstance(p, dict)]


def model_names(config: dict[str, Any]) -> list[str]:
    """The library models the studio's parts name, in first-use order."""
    seen: dict[str, None] = {}
    for part in _parts(config):
        code = part.get("code")
        if isinstance(code, str):
            for match in MODEL_CALL.finditer(code):
                seen.setdefault(match.group(1), None)
    return list(seen)


def embed(config: dict[str, Any], lookup: Lookup) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with ``form.authored.models`` set from the library, and notes.

    ``lookup(name)`` returns ``{"code", "version", "entry"}`` for a library entry (``code``
    empty when it has no model) or None when there is no such entry.
    """
    out = copy.deepcopy(config)
    form = out.get("form") if isinstance(out, dict) else None
    block = form.get("authored") if isinstance(form, dict) else None
    if not isinstance(block, dict):
        return out, []
    block.pop("models", None)
    models: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    used = 0
    for name in model_names(out):
        try:
            found = lookup(name)
        except Exception as exc:
            logger.warning("imagine_library_model_lookup_failed", model=name, error=str(exc))
            notes.append(f'g.model("{name}"): the component library could not be read.')
            continue
        if found is None:
            notes.append(
                f'g.model("{name}"): the component library has no entry "{name}" '
                "(library.list_components lists them)."
            )
            continue
        code = str(found.get("code") or "")
        if not code:
            notes.append(
                f'g.model("{name}"): library entry "{name}" has no form_code yet; add the '
                "code that draws it with library.update_component."
            )
            continue
        problems = code_problems(code)
        if problems:
            notes.append(f'g.model("{name}"): its library code {"; ".join(problems)}.')
            continue
        size = len(code.encode("utf-8"))
        if used + size > MAX_MODELS_BYTES:
            notes.append(
                f'g.model("{name}"): the studio\'s library models would pass '
                f"{MAX_MODELS_BYTES} bytes together; draw this part with g primitives."
            )
            continue
        used += size
        models[name] = {
            "code": code,
            "version": found.get("version"),
            "entry": found.get("entry"),
        }
    if models:
        block["models"] = models
    return out, notes


def linked_models(config: dict[str, Any], lookup: Lookup) -> set[str]:
    """The library entries named by real parts (their ``library``) that carry a model.

    A lookup that fails counts as no model: the part is drawn as its box and the library
    link check says the rest.
    """
    comps = (config.get("context") or {}).get("components") or {}
    out: set[str] = set()
    for part in comps.get("parts") or []:
        name = str((part or {}).get("library") or "").strip() if isinstance(part, dict) else ""
        if not name or name in out:
            continue
        try:
            found = lookup(name)
        except Exception as exc:
            logger.warning("imagine_library_model_lookup_failed", model=name, error=str(exc))
            continue
        if found and found.get("code"):
            out.add(name)
    return out


def catalog_model(name: str) -> dict[str, Any] | None:
    """The gateway's library entry of this name as a model, or None when there is none."""
    from api_gateway.library.catalog_routes import get_catalog
    from twin_core.library.catalog import CatalogNotFoundError

    try:
        entry = get_catalog().get(name, "component")
    except CatalogNotFoundError:
        return None
    return {
        "code": getattr(entry, "form_code", "") or "",
        "version": entry.version,
        "entry": entry.id,
    }


def cached(lookup: Lookup) -> Lookup:
    """One library read per name for the length of one write."""
    memo: dict[str, dict[str, Any] | None] = {}

    def read(name: str) -> dict[str, Any] | None:
        if name not in memo:
            memo[name] = lookup(name)
        return memo[name]

    return read
