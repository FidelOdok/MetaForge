"""Inspect and import one SimReady package (FORGE-711).

``inspect`` fetches a package into a scratch folder, checks it and reports
what it is, then deletes the folder. ``import`` does the same into the shared
adapter workspace and keeps it, adding what MetaForge needs beside the
package:

====================  ===================================================
``usd/``              the package's OpenUSD layers, byte for byte
``preview.glb``       every visible mesh, millimetres, Z up, coloured
``LICENSE.txt``       the package's own licence text
``ATTRIBUTION.txt``   the credit line the licence asks for
``simready.json``     what was measured, and a ready library entry
====================  ===================================================

``twin.record_design_files`` with ``work_product_type='purchased_model'``
records that folder as a versioned item, reading the licence, the digest
and the mass from ``simready.json`` rather than from the caller.

The licence is decided before anything is downloaded beyond the manifest:
a package that fails it costs three small requests, not 60 MB.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from tool_registry.tools.simready.licence import decide, detect_licence_text
from tool_registry.tools.simready.package import (
    Fetcher,
    Manifest,
    PackageError,
    PackageRef,
    download,
    read_manifest,
    select_items,
)

logger = structlog.get_logger(__name__)

IMPORT_ROOT = "simready"

LICENCE_URLS = {
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "BSD-2-Clause": "https://opensource.org/license/bsd-2-clause",
    "BSD-3-Clause": "https://opensource.org/license/bsd-3-clause",
    "MIT": "https://opensource.org/license/mit",
}

#: Publishers whose datasets are the manufacturer's own models. NVIDIA's own
#: library is generic props ("an air compressor"), so its masses are recorded
#: as assumptions rather than a manufacturer's figure.
_GENERIC_PUBLISHERS = frozenset({"nvidia", "PalatialSim", "aabreu-nv"})

_FAMILY_WORDS = (
    ("gripper", "actuator"),
    ("hand", "actuator"),
    ("arm", "actuator"),
    ("robot", "actuator"),
    ("actuator", "actuator"),
    ("motor", "motor"),
    ("bearing", "bearing"),
    ("gear", "gear"),
    ("hinge", "hinge"),
    ("spring", "spring"),
    ("pulley", "pulley"),
)
_ROBOT_PUBLISHERS = frozenset(
    {"FANUC-CORPORATION", "Comau", "frankarobotics", "standardbots", "TeradyneRobotics"}
)


def _probe() -> Any:
    from tool_registry.tools.simready import probe

    return probe


def family_for(ref: PackageRef, labels: dict[str, list[str]]) -> str:
    text = " ".join([ref.asset, *labels.get("wikidata_class", [])]).lower()
    for word, family in _FAMILY_WORDS:
        if word in text:
            return family
    return "actuator" if ref.namespace in _ROBOT_PUBLISHERS else "other"


def library_name(ref: PackageRef) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", ref.asset.lower()).strip("_")
    return f"simready_{slug}"[:80]


def attribution_line(record: dict[str, Any]) -> str:
    """Title, author, source, licence: what CC BY asks for, and enough for the others."""
    licence = record.get("licence") or "unknown licence"
    url = LICENCE_URLS.get(licence, "")
    author = record.get("author") or record.get("owner")
    line = (
        f'"{record.get("asset")}" by {author}, from {record.get("dataset")} on Hugging Face '
        f"({record.get('source_url')}), licensed under {licence}"
    )
    line += f" ({url})." if url else "."
    return line + " MetaForge added a GLB preview; the USD layers are unchanged."


def licence_notice(record: dict[str, Any], licence_text: str | None) -> str:
    """What LICENSE.txt holds: the package's own text, or a notice saying where it is.

    Some packages declare a licence in their manifest, dataset card and USD
    layer but ship no text. An empty file would read as "no licence"; a
    reconstructed one would put words in the owner's mouth.
    """
    if licence_text and licence_text.strip():
        return licence_text
    licence = record.get("licence") or "unknown licence"
    url = LICENCE_URLS.get(licence, "")
    holder = record.get("author") or record.get("owner") or "the package's owner"
    lines = [
        f"SPDX-License-Identifier: {licence}",
        "",
        f"{record.get('dataset')}/{record.get('asset')} ships no licence text of its own.",
        "It declares this licence in its packaging manifest, its dataset card and",
        "its root USD layer.",
        "",
        f"Copyright holder named by the package: {holder}",
        f"Source: {record.get('source_url')}",
    ]
    if url:
        lines.append(f"Licence text: {url}")
    return "\n".join(lines) + "\n"


async def _licence(
    manifest: Manifest, dataset_licence: str | None, layer_licence: str | None
) -> Any:
    return decide(
        {
            "dataset": dataset_licence,
            "package": manifest.licence,
            "licence_file": detect_licence_text(manifest.licence_text),
            "usd_layer": layer_licence,
        }
    )


def _record(
    manifest: Manifest, probe: dict[str, Any], licence: str, files: list[str]
) -> dict[str, Any]:
    ref = manifest.ref
    extents = probe.get("extents_m") or []
    record: dict[str, Any] = {
        "source": "simready",
        "asset": ref.asset,
        "dataset": ref.dataset_id,
        "owner": ref.namespace,
        "author": (probe.get("asset") or {}).get("author") or ref.namespace,
        "package_id": manifest.package_id,
        "package_url": ref.url,
        "source_url": ref.browse_url,
        "commit": ref.commit,
        "package_sha256": manifest.package_sha256,
        "licence": licence,
        "root_usd": f"usd/{manifest.root_usd}",
        "usd_files": [f"usd/{f}" for f in files],
        "extents_m": extents,
        "mass_kg": probe.get("mass_kg"),
        "mass_source": probe.get("mass_source"),
        "mass_is_assumption": ref.namespace in _GENERIC_PUBLISHERS,
        "rigid_bodies": probe.get("rigid_bodies") or [],
        "joints": probe.get("joints") or [],
        "contact_materials": probe.get("contact_materials") or [],
        "semantic_labels": probe.get("semantic_labels") or {},
        "physics_variants": probe.get("physics_variants") or {},
        "conformance": probe.get("conformance") or {},
        "imported_at": datetime.now(UTC).isoformat(),
    }
    if len(extents) == 3:
        record["bounding_box"] = {"size_mm": [round(v * 1000, 3) for v in extents]}
    record["attribution"] = attribution_line(record)
    return record


def component_entry(record: dict[str, Any], ref: PackageRef) -> dict[str, Any]:
    """A ``library.register_component`` body for the part, every number sourced."""
    kind = "assumption" if record.get("mass_is_assumption") else "manufacturer_spec"
    source = {"kind": kind, "ref": record["package_url"]}
    specs: dict[str, Any] = {}
    if record.get("mass_kg"):
        specs["mass_kg"] = {"value": record["mass_kg"], "unit": "kg", "source": source}
    extents = record.get("extents_m") or []
    for key, value in zip(("length_mm", "width_mm", "height_mm"), extents, strict=False):
        if value:
            specs[key] = {"value": round(value * 1000, 3), "unit": "mm", "source": source}
    return {
        "name": library_name(ref),
        "title": ref.asset,
        "family": family_for(ref, record.get("semantic_labels") or {}),
        "origin": "purchased",
        "specs": specs,
        "geometry": {
            "usd_url": record["package_url"],
            "sha256": record["package_sha256"],
            "licence": record["licence"],
            "package_id": record["package_id"],
            "attribution": record["attribution"],
            "source": "simready",
        },
        "notes": (
            f"NVIDIA SimReady package from {record['dataset']}. length/width/height are the "
            "bounding box along the stage's X/Y/Z, not datasheet dimensions."
            + (
                " The mass is the publisher's estimate for a generic model."
                if record.get("mass_is_assumption")
                else ""
            )
        ),
    }


async def _fetch_and_probe(
    ref: PackageRef,
    fetcher: Fetcher,
    dataset_licence: str | None,
    dest: Path,
    *,
    include_textures: bool,
    max_bytes: int,
) -> tuple[Manifest, dict[str, Any], Any, list[str]]:
    manifest = await read_manifest(ref, fetcher)
    early = await _licence(manifest, dataset_licence, None)
    if not early.accepted:
        raise PackageError(f"not imported: {early.reason}")
    items = select_items(manifest, include_textures=include_textures)
    files = await download(manifest, items, dest, fetcher, max_bytes=max_bytes)
    probe = await asyncio.to_thread(_probe().probe_stage, dest / manifest.root_usd)
    decision = await _licence(manifest, dataset_licence, probe.get("asset_license"))
    if not decision.accepted:
        raise PackageError(f"not imported: {decision.reason}")
    return manifest, probe, decision, files


async def inspect_package(
    ref: PackageRef,
    fetcher: Fetcher,
    dataset_licence: str | None,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    """Everything an import would record, without keeping the files.

    A licence MetaForge does not accept is reported, not raised: knowing why
    a part cannot be used is the point of inspecting it.
    """
    manifest = await read_manifest(ref, fetcher)
    decision = await _licence(manifest, dataset_licence, None)
    out: dict[str, Any] = {
        "asset": ref.asset,
        "dataset": ref.dataset_id,
        "package_id": manifest.package_id,
        "package_url": ref.url,
        "source_url": ref.browse_url,
        "files_listed": len(manifest.items),
        "bytes_listed": manifest.total_bytes,
        "bytes_to_import": sum(int(i.get("size") or 0) for i in select_items(manifest)),
        "licence": decision.as_dict(),
    }
    if not decision.accepted:
        return out
    with tempfile.TemporaryDirectory(prefix="simready-inspect-") as tmp:
        _, probe, decision, files = await _fetch_and_probe(
            ref,
            fetcher,
            dataset_licence,
            Path(tmp),
            include_textures=False,
            max_bytes=max_bytes,
        )
    out["licence"] = decision.as_dict()
    out["hash_verified_files"] = len(files)
    out.update({k: v for k, v in probe.items() if k != "asset_license"})
    return out


async def import_package(
    ref: PackageRef,
    fetcher: Fetcher,
    dataset_licence: str | None,
    workspace: Path,
    *,
    include_textures: bool,
    max_bytes: int,
) -> dict[str, Any]:
    """Fetch, check and keep one package; return its folder and library entry."""
    rel_folder = Path(IMPORT_ROOT) / f"{library_name(ref)}-{ref.commit[:8]}"
    folder = workspace / rel_folder
    staging = workspace / IMPORT_ROOT / f".staging-{ref.commit[:8]}-{library_name(ref)}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        manifest, probe, decision, files = await _fetch_and_probe(
            ref,
            fetcher,
            dataset_licence,
            staging / "usd",
            include_textures=include_textures,
            max_bytes=max_bytes,
        )
        preview = await asyncio.to_thread(
            _probe().stage_to_glb, staging / "usd" / manifest.root_usd, staging / "preview.glb"
        )
        record = _record(manifest, probe, str(decision.licence), files)
        record["preview_glb"] = "preview.glb"
        record["licence_obligations"] = decision.obligations
        component = component_entry(record, ref)
        (staging / "LICENSE.txt").write_text(
            licence_notice(record, manifest.licence_text), encoding="utf-8"
        )
        (staging / "ATTRIBUTION.txt").write_text(record["attribution"] + "\n", encoding="utf-8")
        (staging / "simready.json").write_text(
            json.dumps({"record": record, "library_component": component}, indent=2),
            encoding="utf-8",
        )
        if folder.exists():
            shutil.rmtree(folder)
        staging.rename(folder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    logger.info(
        "simready_package_imported",
        package_id=manifest.package_id,
        folder=str(rel_folder),
        files=len(files),
        mass_kg=record.get("mass_kg"),
        licence=record["licence"],
    )
    return {
        "folder": rel_folder.as_posix(),
        "record": {k: v for k, v in record.items() if k != "contact_materials"},
        "contact_material_count": len(record["contact_materials"]),
        "preview": preview,
        "library_component": component,
        "next": [
            "twin.record_design_files with work_product_type='purchased_model', "
            f"file_path='{rel_folder.as_posix()}', name='{ref.asset}' and the project_id",
            "library.register_component with library_component, to reuse the part across projects",
        ],
    }


def read_import(workspace: Path, folder: str) -> dict[str, Any]:
    """The ``simready.json`` record of a folder an import wrote."""
    base = (workspace / IMPORT_ROOT).resolve()
    path = (workspace / folder).resolve()
    if base not in path.parents:
        raise PackageError(f"'{folder}' is not a folder simready.import wrote")
    data = json.loads((path / "simready.json").read_text(encoding="utf-8"))
    record = data.get("record")
    if not isinstance(record, dict):
        raise PackageError(f"'{folder}/simready.json' has no record")
    return record
