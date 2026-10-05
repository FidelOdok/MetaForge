"""Authored-geometry recorder for twin.commit_geometry (MET-529).

Closes the MCP-authored-geometry persistence gap. The FreeCAD adapter authors a
solid headless and returns the STEP **bytes** (base64) — but the adapter lives in
``tool_registry`` (Layer 3) and must not import ``digital_twin`` / ``twin_core``,
and on the containerized path it cannot reach MinIO or the twin at all. So
persistence lives here, in the api_gateway layer, and is injected into the twin
MCP adapter as an opaque async callable — exactly like ``make_decision_recorder``
(MET-495). One call does all three persistence facets:

1. store the STEP blob in MinIO (graceful — node still created on failure),
2. create a validated CAD ``WorkProduct`` with ``content_hash`` +
   ``metadata["minio_object_key"]`` so ``GET /v1/twin/nodes/{id}/model`` resolves
   it (MinIO-first) and the OCCT converter renders it as GLB in the viewer,
3. link it to its project so it shows on the Projects page.

The resulting node is loadable by the existing viewer path with zero extra work.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.twin.item_revisions import (
    finish_definition_revision,
    plan_definition_revision,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.geometry_recorder")

_EXT_CONTENT_TYPE = {
    "step": "application/step",
    "stp": "application/step",
    "stl": "model/stl",
    "iges": "application/iges",
    "igs": "application/iges",
    "brep": "application/octet-stream",
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "geometry")[:60]


async def _find_current_work_product(
    twin: Any, work_product_type: Any, name: str, project_id: str | None
) -> Any | None:
    """The current (non-superseded) work product with this name+type+project.

    Mirrors ``TwinAPI.get_current_datasheet`` (MET-430): "current" = no
    incoming SUPERSEDES edge. Regenerating the same named part links the
    new node to the one it replaces, so successive versions of a part's
    geometry/parameters form a real chain in the graph — not isolated,
    unlinked nodes. Scoped to a project; unscoped (no project_id) commits
    have no reliable identity to match on, so they're never linked.
    """
    if not project_id:
        return None
    from uuid import UUID as _UUID

    from twin_core.models.enums import EdgeType as _EdgeType

    try:
        candidates = await twin.list_work_products(
            work_product_type=work_product_type, project_id=_UUID(project_id)
        )
    except Exception as exc:  # noqa: BLE001 — a commit must not fail over its history
        # MET-728: returning None here means "no predecessor", so no SUPERSEDES
        # edge gets created and the regeneration chain (MET-630) loses a link.
        # The commit still succeeds, which is the right call -- but the caller
        # cannot otherwise tell "first generation of this part" from "the twin
        # was briefly unreachable", and for a system whose prime rule is that
        # everything is versioned and reviewable, a silently broken provenance
        # chain is the wrong thing to be quiet about.
        logger.warning(
            "geometry_predecessor_lookup_failed",
            project_id=project_id,
            name=name,
            work_product_type=str(work_product_type),
            error=str(exc),
            consequence="no SUPERSEDES edge; this generation will look like the first",
        )
        return None
    for candidate in candidates:
        if candidate.name != name:
            continue
        incoming = await twin.graph.get_edges(
            candidate.id, direction="incoming", edge_type=_EdgeType.SUPERSEDES
        )
        if not incoming:
            return candidate
    return None


def make_geometry_recorder(twin: Any, project_backend: Any = None, git_registry: Any = None) -> Any:
    """Return an async ``record(...)`` bound to a twin + project backend.

    The returned callable is what ``twin.commit_geometry`` invokes; binding the
    dependencies here keeps the MCP adapter free of api_gateway/twin_core imports.

    Args:
        git_registry: Optional ``GitRepoRegistry`` (MET-630). When given and
            the caller supplies ``script_source``, the generation script is
            committed to that project's real git repo (for genuine diffing)
            and linked to the resulting CAD_MODEL node as its provenance.
    """

    async def record(
        *,
        step_base64: str,
        name: str,
        project_id: str | None = None,
        session_id: str | None = None,
        domain: str = "mechanical",
        fmt: str = "step",
        source_tool: str = "freecad.export_model",
        extra_metadata: dict[str, Any] | None = None,
        script_source: str | None = None,
        parameters: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        require_blob_store: bool = False,
        parts: list[dict[str, Any]] | None = None,
        item_key: str | None = None,
        supersedes: str | None = None,
        change_reason: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one geometry commit as the next revision of its item.

        FORGE-523: ``item_key`` (``KEY`` or ``KEY@n``) or ``supersedes`` (a
        prior node id) pin the item explicitly, which is what keeps a part's
        history together when its name drifts. With neither, the item is
        found by name in the project, as the SUPERSEDES chain always was.
        """
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_geometry: 'name' is required (non-empty string)")
        try:
            content = base64.b64decode(step_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("twin.commit_geometry: 'step_base64' is not valid base64") from exc
        if not content:
            raise ValueError("twin.commit_geometry: decoded geometry is empty")

        # FORGE-511: validate the parts BEFORE any side effect, so a bad
        # reference creates nothing.
        resolved_parts = await _resolve_assembly_parts(twin, parts, project_id) if parts else []

        # FORGE-523: resolve the item before any side effect too, so a bad
        # item_key/supersedes creates nothing.
        async def _legacy() -> Any:
            return await _find_current_work_product(
                twin, WorkProductType.CAD_MODEL, name, project_id
            )

        plan = await plan_definition_revision(
            twin,
            item_type="assembly" if resolved_parts else "cad_model",
            name=name,
            project_id=project_id,
            default_author=source_tool,
            item_key=item_key,
            supersedes=supersedes,
            change_reason=change_reason,
            run_id=run_id,
            legacy_lookup=_legacy,
        )

        with tracer.start_as_current_span("twin.commit_geometry") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(content).hexdigest()
            ext = fmt.lower().lstrip(".") or "step"
            filename = f"{_slug(name)}.{ext}"
            span.set_attribute("geometry.name", name)
            span.set_attribute("geometry.size_bytes", len(content))

            # FORGE-237: computed BEFORE any of the work below (MinIO upload,
            # script commit, node creation) so an identical re-commit of the
            # same named part's geometry is a genuine no-op, not just a
            # SUPERSEDES-linked sibling. Live-observed: one turn produced
            # THREE "Upper Arm Link" cad_model nodes (two generate_cad_ir
            # auto-commits + one explicit commit_geometry, all the same
            # geometry) because nothing compared content_hash before
            # creating a new node.
            #
            # Gated on ``script_source is None``: a caller that supplies a
            # script is deliberately authoring a new revision (see
            # test_regenerating_same_name_links_supersedes_chain -- the same
            # STEP bytes with a genuinely edited script must still version,
            # since the script IS the real, diffable authoring record even
            # when its output geometry happens not to have changed yet).
            # Only a bare, scriptless re-commit -- exactly the reported
            # bug's shape -- short-circuits.
            if plan is not None:
                # The item's head is the predecessor, whatever it was named.
                prior_step = (
                    await twin.get_work_product(plan.prior_node_id)
                    if plan.prior_node_id is not None
                    else None
                )
            else:
                prior_step = await _find_current_work_product(
                    twin, WorkProductType.CAD_MODEL, name, project_id
                )
            if (
                script_source is None
                and prior_step is not None
                and prior_step.content_hash == content_hash
            ):
                existing_id = str(prior_step.id)
                logger.info(
                    "geometry_commit_already_exists",
                    node_id=existing_id,
                    name=name,
                    project_id=project_id,
                )
                existing: dict[str, Any] = {
                    "node_id": existing_id,
                    "content_hash": content_hash,
                    "format": prior_step.format,
                    "size_bytes": len(content),
                    "project_linked": bool(project_id),
                    "model_url": f"/v1/twin/nodes/{existing_id}/model",
                    "already_committed": True,
                    "message": (
                        f"'{name}' with this exact geometry is already committed as "
                        f"{existing_id} — nothing new was created. Do not call "
                        "commit_geometry again for the same part unless the geometry "
                        "actually changed."
                    ),
                }
                if plan is not None and plan.item is not None:
                    existing["item_key"] = plan.key
                    existing["revision"] = plan.item.head_revision
                    existing["item_ref"] = f"{plan.key}@{plan.item.head_revision}"
                return existing

            # 1. blob → MinIO (graceful: keep the node even if storage is down).
            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id),
                    filename,
                    content,
                    content_type=_EXT_CONTENT_TYPE.get(ext, "application/octet-stream"),
                )
            except Exception as exc:  # noqa: BLE001 — degrade like /v1/twin/import
                if require_blob_store:
                    # FORGE-501: a cad_model with no stored geometry cannot be
                    # loaded or shown. A deterministic fallback commit must
                    # fail so the phase reports it, not mint an empty node.
                    logger.error("geometry_blob_store_required_failed", name=name, error=str(exc))
                    raise RuntimeError(
                        f"cannot commit '{name}': geometry blob store unavailable ({exc}). "
                        "No cad_model was created."
                    ) from exc
                logger.warning("geometry_blob_store_skipped", name=name, error=str(exc))

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "content_sha256": content_hash,
                "authored_by": source_tool,
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key
            if session_id:
                metadata["session_id"] = session_id
            if extra_metadata:
                metadata.update(extra_metadata)
            if resolved_parts:
                metadata["parts"] = resolved_parts
            if plan is not None:
                metadata.update(plan.stamp())
            # MET-630: structured, queryable geometry semantics — separate
            # from the git-versioned script text below. Parameters are the
            # values that drove generation (e.g. pad length, hole diameter);
            # properties are derived measurements (volume, bounding box,
            # mass properties). Both live in the graph so twin_query_cypher
            # / constraint evaluation can reason over them directly.
            if parameters or properties:
                metadata["geometry_features"] = {
                    "parameters": parameters or {},
                    "properties": properties or {},
                }

            # MET-630: commit the real generation script to the project's
            # git repo — the working, diffable source of truth — as its own
            # CAD_SOURCE_SCRIPT node, linked to the STEP node it produced.
            # Best-effort: a script-commit failure must never block the
            # STEP node itself from being created.
            script_node_id: str | None = None
            git_commit_sha: str | None = None
            if script_source and git_registry is not None:
                try:
                    engine = git_registry.for_project(project_id)
                    try:
                        await engine.create_branch("main")
                    except ValueError:
                        pass  # branch already exists — fine
                    script_hash = hashlib.sha256(script_source.encode()).hexdigest()
                    script_path = f"mechanical/cad_src/{_slug(name)}.py"
                    script_name = f"{name} (script)"
                    prior_script = await _find_current_work_product(
                        twin, WorkProductType.CAD_SOURCE_SCRIPT, script_name, project_id
                    )
                    # FORGE-523: a renamed part keeps its script chain too --
                    # fall back to the script the previous revision named.
                    prior_script_id = (
                        (prior_step.metadata or {}).get("script_node_id")
                        if (prior_script is None and prior_step is not None)
                        else None
                    )
                    if prior_script_id:
                        prior_script = await twin.get_work_product(UUID(str(prior_script_id)))
                    script_wp = WorkProduct(
                        name=script_name,
                        type=WorkProductType.CAD_SOURCE_SCRIPT,
                        domain=domain,
                        file_path=script_path,
                        content_hash=script_hash,
                        format="py",
                        metadata={"source_tool": source_tool},
                        created_by=source_tool,
                        project_id=project_id,
                    )
                    created_script = await twin.create_work_product(script_wp)
                    script_id = getattr(created_script, "id", script_wp.id)
                    if prior_script is not None and prior_script.id != script_id:
                        await twin.add_edge(script_id, prior_script.id, EdgeType.SUPERSEDES)
                    script_version = await engine.commit(
                        "main",
                        f"author {name}",
                        [script_id],
                        source_tool,
                        content={script_id: script_source.encode()},
                        # Stable path (derived from the part's name, not its
                        # fresh id) — regenerating "Bracket" always lands on
                        # the same file, so git actually accumulates history.
                        paths={script_id: script_path},
                    )
                    script_node_id = str(script_id)
                    git_commit_sha = script_version.git_commit_sha
                    metadata["git_commit_sha"] = git_commit_sha
                    metadata["git_path"] = script_path
                    metadata["script_node_id"] = script_node_id
                except Exception as exc:  # noqa: BLE001 — script commit is best-effort
                    logger.warning("geometry_script_commit_failed", name=name, error=str(exc))

            # MET-630: link successive generations of the "same" named part
            # (by project_id + name) so parameter/property changes across
            # regenerations form a real version chain in the graph, not
            # isolated nodes — mirrors TwinAPI.ingest_datasheet's SUPERSEDES
            # pattern. `prior_step` was already resolved above (FORGE-237,
            # for the identical-content short circuit) — reused here as the
            # SUPERSEDES predecessor rather than looked up twice.

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.CAD_MODEL,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format=ext,
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=source_tool,
                project_id=project_id,  # pydantic coerces str → UUID
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            if prior_step is not None and prior_step.id != created.id:
                try:
                    await twin.add_edge(created.id, prior_step.id, EdgeType.SUPERSEDES)
                except Exception as exc:  # noqa: BLE001 — supersedes link is best-effort
                    logger.warning(
                        "geometry_supersedes_edge_failed", node_id=node_id, error=str(exc)
                    )
                else:
                    # FORGE-314: prior_step just stopped being the current tip
                    # (it now has an incoming SUPERSEDES edge) -- any Evidence
                    # pinned to prior_step.id@0 is now behind, and everything
                    # that in turn depends on THAT evidence transitively too
                    # (spec section 21's mount-CAD -> simulation -> BOM
                    # example). Best-effort, same posture as the edge itself:
                    # a staleness-propagation failure must never fail the
                    # geometry commit that triggered it.
                    if project_id:
                        try:
                            from uuid import UUID as _UUID

                            from twin_core.consistency.staleness import StalenessEngine

                            await StalenessEngine(twin).propagate(
                                _UUID(project_id), "work_product", prior_step.id
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "geometry_staleness_propagation_failed",
                                node_id=node_id,
                                error=str(exc),
                            )

            if script_node_id is not None:
                try:
                    script_id = getattr(created_script, "id")
                    await twin.add_edge(script_id, created.id, EdgeType.PARENT_OF)
                except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                    logger.warning("geometry_script_edge_failed", node_id=node_id, error=str(exc))

            # FORGE-511: assembly -> part containment edges.
            for part in resolved_parts:
                try:
                    await twin.add_edge(created.id, UUID(part["node_id"]), EdgeType.PARENT_OF)
                except Exception as exc:  # noqa: BLE001 - containment edge is best-effort
                    logger.warning(
                        "geometry_assembly_edge_failed",
                        node_id=node_id,
                        part=part["node_id"],
                        error=str(exc),
                    )

            # FORGE-523: this node is the item's new head. SUPERSEDES was
            # added above (with its staleness propagation), so not again here.
            item_fields: dict[str, Any] = {}
            await finish_definition_revision(
                twin,
                plan,
                created.id,
                name=name,
                result=item_fields,
                link_supersedes=False,
            )

            # 2. project junction link so it shows on the Projects page.
            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, name, "cad_model")
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("geometry_project_link_failed", error=str(exc))

            logger.info(
                "geometry_committed",
                node_id=node_id,
                project_id=project_id,
                linked=linked,
                minio_object_key=minio_object_key,
                size_bytes=len(content),
                script_node_id=script_node_id,
                git_commit_sha=git_commit_sha,
                supersedes=str(prior_step.id) if prior_step is not None else None,
                item_ref=item_fields.get("item_ref"),
            )
            out = {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "format": ext,
                "size_bytes": len(content),
                "project_linked": linked,
                "model_url": f"/v1/twin/nodes/{node_id}/model",
            }
            if script_node_id is not None:
                out["script_node_id"] = script_node_id
                out["git_commit_sha"] = git_commit_sha
            if prior_step is not None:
                out["supersedes_node_id"] = str(prior_step.id)
            out.update(item_fields)
            # MET-584: soft-warn (never block) when geometry lands in a project
            # with no recorded requirements — the model sees the warning in the
            # tool result and can course-correct; gates enforce, chat nudges.
            warning = await _unconstrained_warning(project_backend, project_id)
            if warning:
                out["warning"] = warning
            return out

    return record


async def _resolve_assembly_parts(
    twin: Any, parts: list[dict[str, Any]], project_id: str | None
) -> list[dict[str, Any]]:
    """Check each part is an existing cad_model in the project; build ``metadata.parts``."""
    out: list[dict[str, Any]] = []
    for part in parts:
        raw = str(part.get("node_id") or "")
        try:
            part_id = UUID(raw)
        except ValueError as exc:
            raise ValueError(f"twin.commit_geometry: part node_id {raw!r} is not a UUID") from exc
        node = await twin.get_work_product(part_id)
        if node is None:
            raise ValueError(f"twin.commit_geometry: part {raw} does not exist in the twin")
        node_type = getattr(node.type, "value", node.type)
        if str(node_type) != "cad_model":
            raise ValueError(f"twin.commit_geometry: part {raw} is a {node_type}, not a cad_model")
        node_project = getattr(node, "project_id", None)
        if project_id and node_project is not None and str(node_project) != str(project_id):
            raise ValueError(f"twin.commit_geometry: part {raw} belongs to a different project")
        meta = getattr(node, "metadata", None) or {}
        entry: dict[str, Any] = {
            "node_id": str(part_id),
            "name": part.get("name") or node.name,
            "material": part.get("material") or meta.get("material"),
        }
        bbox = part.get("position_bbox_mm") or meta.get("bbox_mm")
        if bbox:
            entry["position_bbox_mm"] = bbox
        out.append(entry)
    return out


async def _unconstrained_warning(project_backend: Any, project_id: str | None) -> str | None:
    """A warning string when the target project lacks prd/constraint_set.

    Best-effort: any lookup failure returns None — the commit already
    succeeded and this must never taint it.
    """
    if not project_id or project_backend is None:
        return None
    try:
        project = await project_backend.get_project(project_id)
    except Exception:  # noqa: BLE001 — advisory only
        return None
    if project is None:
        return None
    types = {str(getattr(wp.type, "value", wp.type)) for wp in project.work_products}
    if types & {"prd", "constraint_set"}:
        return None
    return (
        "This project has no recorded requirements or constraints (no prd or "
        "constraint_set work product) — the committed geometry cannot be "
        "validated against anything. Elicit the key quantified requirements "
        "from the user and record them with twin.record_constraint_set."
    )
