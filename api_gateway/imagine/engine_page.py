"""The formgraph engine page: config validation, embedding, assets and CSP.

The engine is fixed (``engine/engine.html``, ``engine.css``, ``engine.js``);
a product's page is the engine with the product config embedded the way the
formgraph plugin's ``build_page.py`` does it: JSON with ``</`` escaped inside
a ``<script type="application/json">`` block the page reads with
``JSON.parse``. Nothing is loaded from a third-party origin: three.js r128 is
vendored under ``engine/vendor`` (see ``MANIFEST.json``) and served from the
gateway, and no web fonts are fetched.

The page is served with a strict Content Security Policy: scripts and
stylesheets only from the gateway itself, no inline script, no ``eval``. The
engine's background solver runs as a ``blob:`` worker it builds from its own
code, and reference images arrive as ``data:`` URIs, so ``worker-src`` and
``img-src`` allow exactly those two. Inline ``style`` attributes stay allowed
(``style-src-attr``): the engine sets colours and positions on elements, and
an attribute cannot run code.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import structlog

from api_gateway.imagine.formgraph import build_page, fg_context
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.engine_page")

ENGINE_DIR = Path(__file__).parent / "engine"
VENDOR_DIR = ENGINE_DIR / "vendor"

#: Published asset name to file. Nothing else is ever served.
ASSETS: dict[str, Path] = {
    "engine.css": ENGINE_DIR / "engine.css",
    "engine.js": ENGINE_DIR / "engine.js",
    "authored.js": ENGINE_DIR / "authored.js",
    "three.min.js": VENDOR_DIR / "three.min.js",
    "OrbitControls.js": VENDOR_DIR / "OrbitControls.js",
    "GLTFExporter.js": VENDOR_DIR / "GLTFExporter.js",
}
_MIME = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}

#: The largest config a PUT may carry (packed reference images included).
MAX_CONFIG_BYTES = 12 * 1024 * 1024

#: Where the engine page may be framed from. The dashboard reaches the
#: gateway through its own origin (the ``/api`` proxy), so ``'self'`` is the
#: default; a deployment serving the dashboard elsewhere sets
#: ``METAFORGE_IMAGINE_FRAME_ANCESTORS`` (space-separated CSP sources).
FRAME_ANCESTORS_ENV = "METAFORGE_IMAGINE_FRAME_ANCESTORS"

_IMAGE_DATA = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$")
_ASSET_SLOT = re.compile(r"\{\{asset:([A-Za-z0-9_.-]+)\}\}")
_FRAME_SOURCE = re.compile(r"^('self'|'none'|https?://[A-Za-z0-9.-]+(:\d+)?)$")


class ConfigError(ValueError):
    """A config the builder refuses, with every error it found."""

    def __init__(self, errors: list[str], warnings: list[str] | None = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.warnings = warnings or []


@dataclass
class ValidatedConfig:
    """A config that passed: what was stored, what the page embeds, and warnings."""

    config: dict[str, Any]
    compiled: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Any]:
    data: dict[str, Any] = build_page.load_json(build_page.CATALOGUE)
    return data


def _inline_context(raw: Any) -> dict[str, Any] | None:
    """The ``context`` object as fg_context's loader would have read it from a folder."""
    if raw is None:
        return None
    ctx: dict[str, Any] = {}
    for name in fg_context.FILES:
        part = raw.get(name)
        ctx[name] = part if isinstance(part, dict) else {}
    ctx["_folder"] = None  # hosted: never read a file path from a config
    return ctx


def _reference_errors(ctx: dict[str, Any] | None) -> list[str]:
    """References must arrive packed: images as data URIs, meshes as encoded arrays."""
    errors: list[str] = []
    refs = ((ctx or {}).get("references") or {}).get("references") or []
    for i, ref in enumerate(refs if isinstance(refs, list) else []):
        if not isinstance(ref, dict):
            errors.append(f"context.references.references[{i}] must be an object.")
            continue
        data = ref.get("data")
        if data is not None and not (isinstance(data, str) and _IMAGE_DATA.match(data)):
            errors.append(
                f'reference "{ref.get("id", i)}": "data" must be a base64 PNG, JPEG, GIF '
                "or WebP data URI."
            )
        mesh = ref.get("mesh")
        if mesh is not None and not (
            isinstance(mesh, dict)
            and isinstance(mesh.get("pos"), str)
            and isinstance(mesh.get("idx"), str)
        ):
            errors.append(f'reference "{ref.get("id", i)}": "mesh" must be {{"pos", "idx"}}.')
    return errors


def _screen_library_material(compiled: dict[str, Any]) -> None:
    """Give the engine the catalogue material a library material screens as (FORGE-827).

    The stored config keeps the person's own material (``prusament_petg``), so
    the manufacturing context and Develop see the library entry. The page
    screens with its family's catalogue figures (``petg``); the original is
    kept beside it as ``process.libraryMaterial``.
    """
    process = compiled.get("process")
    if not isinstance(process, dict):
        return
    proc = (catalogue().get("processes") or {}).get(process.get("id"))
    material = process.get("material")
    extra = ((compiled.get("catalogue") or {}).get("materials") or {}).get(process.get("id")) or []
    if (
        not proc
        or material in proc.get("materials", {})
        or any(isinstance(m, dict) and m.get("id") == material for m in extra)
    ):
        return
    family = build_page.resolve_material(proc, material)
    if family:
        process["libraryMaterial"] = material
        process["material"] = family
        logger.info("imagine_library_material_screened", material=material, family=family)


def validate_config(config: dict[str, Any]) -> ValidatedConfig:
    """Check a product config with the merged formgraph rules; raise :class:`ConfigError`.

    MetaForge differs from the plugin in one place: the product context
    (components, machines, options, models, scenarios, references,
    manufacturing) travels inline as ``config["context"]`` rather than as a
    folder path, because a gateway never reads a path a browser or an agent
    names.
    """
    with tracer.start_as_current_span("imagine.validate_config") as span:
        size = len(json.dumps(config, ensure_ascii=False))
        span.set_attribute("config.bytes", size)
        if size > MAX_CONFIG_BYTES:
            raise ConfigError([f"The config is {size // 1024} KB; keep it under 12 MB."])
        cfg = copy.deepcopy(config)
        raw_ctx = cfg.pop("context", None)
        if raw_ctx is not None and not isinstance(raw_ctx, dict):
            raise ConfigError(
                [
                    '"context" must be an object holding components, machines, options, '
                    "models, scenarios, references and manufacturing; a hosted studio "
                    "never reads a context folder."
                ]
            )
        ctx = _inline_context(raw_ctx)
        early = _reference_errors(ctx)
        if early:
            raise ConfigError(early)
        try:
            errors, warnings = build_page.validate(cfg, catalogue(), ctx)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            # The plugin's validator trusts the config's shape in places.
            raise ConfigError([f"The config is malformed: {exc}"]) from exc
        if errors:
            raise ConfigError(errors, warnings)
        try:
            compiled = fg_context.compile_context(cfg, ctx) if ctx is not None else cfg
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ConfigError([f"The context could not be compiled: {exc}"]) from exc
        _screen_library_material(compiled)
        span.set_attribute("config.warnings", len(warnings))
        return ValidatedConfig(config=config, compiled=compiled, warnings=list(warnings))


# ── the page ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _engine_template() -> str:
    return (ENGINE_DIR / "engine.html").read_text(encoding="utf-8")


@cache
def asset_bytes(name: str) -> bytes:
    path = ASSETS[name]
    return path.read_bytes()


@cache
def asset_version(name: str) -> str:
    return hashlib.sha256(asset_bytes(name)).hexdigest()[:12]


def asset_media_type(name: str) -> str:
    return _MIME[os.path.splitext(name)[1]]


def embed_config(compiled: dict[str, Any], template: str | None = None) -> str:
    """The engine with ``compiled`` embedded, exactly as formgraph's build_page does it."""
    return build_page.build(compiled, template if template is not None else _engine_template())


def render_engine(compiled: dict[str, Any]) -> str:
    """The product's page: config embedded, asset slots pointing at the gateway.

    Asset URLs are relative (``engine/assets/<name>``) so the page works under
    any prefix the gateway is reached through, such as the dashboard's
    ``/api`` proxy. Each carries a content hash so a cached copy never outlives
    the file it was made from.
    """
    page = embed_config(compiled)

    def slot(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in ASSETS:
            raise RuntimeError(f"engine.html names an unknown asset {name!r}")
        return f"engine/assets/{name}?v={asset_version(name)}"

    return _ASSET_SLOT.sub(slot, page)


def frame_ancestors() -> str:
    raw = os.environ.get(FRAME_ANCESTORS_ENV, "").split()
    sources = [s for s in raw if _FRAME_SOURCE.match(s)]
    if raw and len(sources) != len(raw):
        logger.warning("imagine_frame_ancestors_ignored", value=" ".join(raw))
    return " ".join(sources) if sources else "'self'"


def content_security_policy() -> str:
    """The engine page's CSP: everything from the gateway itself, nothing inline that runs."""
    return "; ".join(
        (
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            "style-src-attr 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "worker-src blob:",
            "font-src 'self'",
            "connect-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            f"frame-ancestors {frame_ancestors()}",
        )
    )


def page_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": content_security_policy(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }


def asset_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=86400",
        # The engine frame is sandboxed without allow-same-origin, so its
        # requests come from an opaque origin; the assets carry no project data.
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
