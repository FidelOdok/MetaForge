"""Exact CAD on commit, B-Rep validity and revision-linked STEP (CDE slice 6).

The fast preview is drawn on the client from the design IR and never comes
here. This module is the exact path (FRS DEF-002, GEO-003, GEO-007, GEO-008):

* :meth:`ExactCadService.build` runs on an explicit commit or on demand, never
  per pointer event. It lowers the design IR (or runs a CadQuery script)
  through the CadQuery adapter over MCP, checks the B-Rep with
  ``cadquery.validate_shape``, and only on a ``pass`` commits the result as
  the item's next revision, born ``validated``. With no new source it
  validates the current head in place. A ``fail`` (or a check that could not
  run) records the attempt on the revision it started from and leaves the
  head where it was, so the last valid exact model stays current.
* :meth:`ExactCadService.export_step` writes a STEP only from a ``validated``
  (or ``released``) revision, with the adapter's configured schema, re-imports
  it and compares volume, bounding box and solid count. The export lives on
  the revision it came from (``step_export``) and a repeat request for the
  same revision and geometry returns it. When the head moves on, older
  exports read ``stale`` and stay downloadable.

Both run as background jobs (``twin.build_exact_cad``, ``twin.export_cad_step``)
on the process job runner, so slice 2's supersede and stale rules apply: a
build names the item as its ``target_key`` and the head it started from as
its ``base_revision``, and the twin refuses a commit pinned to a base that
moved (the job then ends ``stale``, and the newer head is kept).

Operation events (``domain: mechanical``), on the job's own operation:

=========================  ==================  =================================
FRS name                   event ``type``      when
=========================  ==================  =================================
``exact_geometry_ready``   ``milestone``       the exact B-Rep was built
``artifact_ready``         ``partial_result``  a validated revision or a STEP
``validation_completed``   ``completed``       the job ended with a verdict
``failed``                 ``failed``          the check failed or could not run
``stale``                  ``stale``           an older STEP export was superseded
=========================  ==================  =================================
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.geometry_state import (
    EXACT_ATTEMPTS_KEY,
    EXACT_SOURCE_KEY,
    GEOMETRY_STATE_KEY,
    MAX_ATTEMPTS,
    STEP_EXPORT_KEY,
    VALIDITY_KEY,
    GeometryState,
    StepExportState,
    ValidityOutcome,
    authority,
    badge,
    check_transition,
    geometry_state_of,
    is_illustrative,
    set_node_metadata,
    step_export_view,
    validated_states,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.exact_cad")

EXACT_BUILD_TOOL = "twin.build_exact_cad"
STEP_EXPORT_TOOL = "twin.export_cad_step"
EXACT_BUILD_EXPECTED_SECONDS = 20.0
STEP_EXPORT_EXPECTED_SECONDS = 10.0
STEP_CACHE_ENV = "METAFORGE_CAD_CACHE_DIR"

#: Round-trip tolerances: relative volume, bounding box (absolute mm, or
#: relative to the box diagonal, whichever is larger).
ROUND_TRIP_VOLUME_REL = 1e-3
ROUND_TRIP_BBOX_ABS_MM = 0.01
ROUND_TRIP_BBOX_REL = 1e-4

_BBOX_KEYS = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")

#: Fixed phrases the operation stream shows. Never a tool's own text.
MSG_EXACT_READY = "Exact geometry built; engineering checks running"
MSG_VALIDATED = "Geometry validated"
MSG_ALREADY_VALIDATED = "Geometry was already validated"
MSG_NOT_VALIDATED = "Exact geometry built; checks raised warnings, so it was not validated"
MSG_VALIDITY_FAILED = "Geometry check failed; the last valid revision was kept"
MSG_CHECK_UNAVAILABLE = "Geometry checks could not run; the last valid revision was kept"
MSG_STEP_READY = "STEP ready"
MSG_STEP_ROUND_TRIP_FAILED = "STEP re-import did not match; not usable for release"
MSG_STEP_FAILED = "STEP export failed"
MSG_STEP_STALE = "This STEP belongs to an older revision"


class ExactCadError(ValueError):
    """A request that cannot be honoured. The message is caller-facing."""


class RequiredInputMissingError(ExactCadError):
    """A parametric sketch's feature still needs a value only a person can give."""


def cad_key_for_sketch(sketch_key: str) -> str:
    """The CAD item an approved parametric sketch builds into: ``PSK-X`` -> ``CAD-X``."""
    stem = sketch_key.split("-", 1)[1] if "-" in sketch_key else sketch_key
    return f"CAD-{stem}"[:80]


class PlaneReattachmentRequiredError(ExactCadError):
    """The sketch's plane must be reattached first (PLN-010): a 409 at the route."""


class StepExportRefusedError(ExactCadError):
    """A STEP was asked of a revision that is not validated."""


class GeometryValidityError(RuntimeError):
    """The exact B-Rep failed its validity check; the head did not move."""

    operation_message = MSG_VALIDITY_FAILED


class GeometryCheckUnavailableError(RuntimeError):
    """The validity check could not run; nothing was validated."""

    operation_message = MSG_CHECK_UNAVAILABLE


class StepExportError(RuntimeError):
    """The STEP export or its required round trip did not succeed."""

    operation_message = MSG_STEP_FAILED


_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _record(method: str, *args: Any) -> None:
    try:
        getattr(_collector(), method)(*args)
    except Exception:  # noqa: BLE001 - a metric never fails the work
        pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    """Unwrap an MCP result (bare or ``{status, data}``), raising on an error status."""
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RuntimeError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


class _UnwrappingBridge:
    """Hands the lowering pass bare tool results, whatever the bridge returns."""

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        return _data(await self._bridge.invoke(tool_id, params, timeout=timeout), tool_id)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def _bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        return [float(raw[k]) for k in _BBOX_KEYS]
    except (KeyError, TypeError, ValueError):
        return None


def compare_round_trip(
    source: dict[str, Any] | None, reimported: dict[str, Any] | None
) -> tuple[str, list[dict[str, str]]]:
    """Compare a shape before export with the re-imported STEP. Pure.

    ``pass`` only when solid count, volume and bounding box all agree within
    tolerance; ``fail`` on any disagreement; ``not_evaluated`` when either
    side's measures are missing.
    """
    if not source or not reimported:
        return ValidityOutcome.NOT_EVALUATED.value, [
            {"check": "round_trip", "outcome": "not_evaluated", "detail": "no measures to compare"}
        ]
    findings: list[dict[str, str]] = []
    s_solids, r_solids = source.get("solid_count"), reimported.get("solid_count")
    if s_solids is None or r_solids is None:
        findings.append(
            {"check": "solids", "outcome": "not_evaluated", "detail": "solid count missing"}
        )
    elif int(s_solids) != int(r_solids):
        findings.append(
            {
                "check": "solids",
                "outcome": "fail",
                "detail": f"{s_solids} solid(s) became {r_solids}",
            }
        )
    else:
        findings.append({"check": "solids", "outcome": "pass", "detail": f"{r_solids} solid(s)"})

    s_vol, r_vol = source.get("volume_mm3"), reimported.get("volume_mm3")
    if s_vol is None or r_vol is None:
        findings.append({"check": "volume", "outcome": "not_evaluated", "detail": "volume missing"})
    else:
        s_v, r_v = float(s_vol), float(r_vol)
        rel = abs(s_v - r_v) / max(abs(s_v), 1e-9)
        ok = rel <= ROUND_TRIP_VOLUME_REL
        findings.append(
            {
                "check": "volume",
                "outcome": "pass" if ok else "fail",
                "detail": f"{s_v:.3f} vs {r_v:.3f} mm3 (relative difference {rel:.2e})",
            }
        )

    s_box, r_box = _bbox(source.get("bounding_box")), _bbox(reimported.get("bounding_box"))
    if s_box is None or r_box is None:
        findings.append(
            {"check": "bounding_box", "outcome": "not_evaluated", "detail": "box missing"}
        )
    else:
        diag = sum((s_box[i + 3] - s_box[i]) ** 2 for i in range(3)) ** 0.5
        tol = max(ROUND_TRIP_BBOX_ABS_MM, diag * ROUND_TRIP_BBOX_REL)
        worst = max(abs(a - b) for a, b in zip(s_box, r_box, strict=True))
        findings.append(
            {
                "check": "bounding_box",
                "outcome": "pass" if worst <= tol else "fail",
                "detail": f"largest corner difference {worst:.4f} mm (tolerance {tol:.4f} mm)",
            }
        )
    outcomes = {f["outcome"] for f in findings}
    if "fail" in outcomes:
        return ValidityOutcome.FAIL.value, findings
    if "not_evaluated" in outcomes:
        return ValidityOutcome.NOT_EVALUATED.value, findings
    return ValidityOutcome.PASS.value, findings


# ---------------------------------------------------------------------------
# Local cache of STEP bytes (used when the blob store is unavailable)
# ---------------------------------------------------------------------------


def cache_dir() -> Path:
    raw = (os.environ.get(STEP_CACHE_ENV) or "").strip()
    return Path(raw) if raw else Path.home() / ".metaforge" / "cad_cache"


def _cache_put(content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    path = cache_dir() / f"{digest}.step"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
    except OSError as exc:
        logger.warning("cad_cache_write_failed", path=str(path), error=str(exc))
    return digest


def _cache_get(digest: str | None) -> bytes | None:
    if not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    path = cache_dir() / f"{digest}.step"
    try:
        return path.read_bytes() if path.exists() else None
    except OSError:
        return None


def _default_blob_reader(wp: Any) -> bytes | None:
    """The revision's STEP: the blob store first, then the local cache."""
    if (wp.metadata or {}).get("minio_object_key") or wp.file_path:
        try:
            from api_gateway.twin.blob_store import resolve_work_product_blob

            content, _name = resolve_work_product_blob(wp)
            return content
        except Exception as exc:  # noqa: BLE001 - fall back to the cache
            logger.info("cad_blob_unreadable", node_id=str(wp.id), error=str(exc))
    return _cache_get(getattr(wp, "content_hash", None))


def _default_blob_writer(node_id: str, filename: str, content: bytes) -> str | None:
    try:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        return store_work_product_blob(node_id, filename, content, content_type="application/step")
    except Exception as exc:  # noqa: BLE001 - the local cache still holds it
        logger.info("step_export_blob_store_skipped", node_id=node_id, error=str(exc))
        return None


def read_export_file(export: dict[str, Any]) -> bytes | None:
    """The bytes of a recorded STEP export, from the blob store or the cache."""
    key = export.get("object_key")
    if key:
        try:
            from digital_twin.storage.work_product_blobs import fetch_work_product_blob

            return fetch_work_product_blob(str(key))
        except Exception as exc:  # noqa: BLE001 - try the cache
            logger.info("step_export_blob_unreadable", key=key, error=str(exc))
    return _cache_get(export.get("file_sha256"))


def _slug(name: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "part")[:60]


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


Publisher = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def _publish_via_seam(project_id: str, event: dict[str, Any]) -> None:
    from mcp_core.operation_events import publish_operation_event

    await publish_operation_event(project_id, event)


class ExactCadService:
    """Exact builds, validity checks and STEP exports for CAD items."""

    def __init__(
        self,
        twin: Any,
        *,
        bridge: Any,
        recorder: Any,
        blob_reader: Callable[[Any], bytes | None] | None = None,
        blob_writer: Callable[[str, str, bytes], str | None] | None = None,
        publisher: Publisher | None = None,
        job_runner: Any = None,
    ) -> None:
        self._twin = twin
        self._bridge = bridge
        self._recorder = recorder
        self._read_blob = blob_reader or _default_blob_reader
        self._write_blob = blob_writer or _default_blob_writer
        self._publish = publisher or _publish_via_seam
        self._runner = job_runner

    # -- shared with face attachment (CDE slice 9) ------------------------

    async def invoke_tool(self, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call an adapter tool over this service's bridge; the unwrapped result."""
        return _data(await self._bridge.invoke(tool_id, params), tool_id)

    def read_step(self, wp: Any) -> bytes | None:
        """A revision's stored STEP (blob store, then the local cache)."""
        return self._read_blob(wp)

    # -- lookups ---------------------------------------------------------

    def _job_runner(self) -> Any:
        if self._runner is not None:
            return self._runner
        from tool_registry.jobs import default_job_runner

        return default_job_runner()

    async def _item(self, project_id: str, item_key: str) -> Any:
        from twin_core.items import find_item, parse_item_ref, supports_items

        if not supports_items(self._twin):
            raise ExactCadError("this twin holds no items")
        try:
            key, _pin = parse_item_ref(item_key)
            UUID(str(project_id))
        except ValueError as exc:
            raise ExactCadError(str(exc)) from exc
        item = await find_item(self._twin, key, project_id)
        if item is None or item.head_node_id is None:
            raise ExactCadError(f"no CAD item {key} with a current revision in this project")
        if item.item_type not in ("cad_model", "assembly"):
            raise ExactCadError(f"{key} is a {item.item_type} item, not CAD geometry")
        return item

    async def _wp(self, node_id: Any) -> Any:
        try:
            wp = await self._twin.get_work_product(UUID(str(node_id)))
        except ValueError as exc:
            raise ExactCadError(f"{node_id!r} is not a node id") from exc
        if wp is None:
            raise ExactCadError(f"no work product {node_id}")
        return wp

    async def _revision_of(self, node_id: UUID) -> tuple[Any, int] | None:
        from twin_core.items import item_for_node

        return await item_for_node(self._twin, node_id)

    async def _emit(
        self,
        project_id: str | None,
        *,
        name: str,
        event_type: str,
        sequence: int,
        message: str,
        artifact_refs: list[dict[str, Any]] | None = None,
        target_key: str | None = None,
        base_revision: str | None = None,
        severity: str = "info",
        job_id: str | None = None,
    ) -> None:
        """One operation event on the running job's operation. Never raises."""
        from tool_registry.jobs import current_job_id

        job = job_id or current_job_id()
        if not project_id or not job:
            return
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"metaforge:job:{job}:{name}")),
            "operation_id": f"job:{job}",
            "kind": "job",
            "sequence": sequence,
            "type": event_type,
            "state": "stale" if event_type == "stale" else "running",
            "domain": "mechanical",
            "stage_id": name,
            "stage_title": message,
            "occurred_at": _now(),
            "severity": severity,
            "message": message,
            "artifact_refs": artifact_refs or [],
        }
        if event_type == "stale":
            event.pop("stage_id")
            event.pop("stage_title")
        if target_key:
            event["target_key"] = target_key[:200]
        if base_revision:
            event["base_revision"] = base_revision[:200]
        try:
            await self._publish(project_id, event)
        except Exception as exc:  # noqa: BLE001 - a status line never fails the work
            logger.info("exact_cad_event_dropped", name=name, error=str(exc))

    # -- validity --------------------------------------------------------

    async def _validate(self, step: bytes, *, expect_solid: bool = True) -> dict[str, Any]:
        """The adapter's verdict, or ``not_evaluated`` when it could not run."""
        started = time.monotonic()
        try:
            result = _data(
                await self._bridge.invoke(
                    "cadquery.validate_shape",
                    {
                        "step_base64": base64.b64encode(step).decode("ascii"),
                        "expect_solid": expect_solid,
                    },
                ),
                "cadquery.validate_shape",
            )
        except Exception as exc:  # noqa: BLE001 - no evidence is not a pass
            logger.warning("exact_cad_validity_unavailable", error=str(exc))
            result = {
                "outcome": ValidityOutcome.NOT_EVALUATED.value,
                "findings": [
                    {
                        "check": "kernel",
                        "outcome": "not_evaluated",
                        "detail": f"cadquery.validate_shape could not run: {exc}"[:300],
                    }
                ],
            }
        outcome = str(result.get("outcome") or ValidityOutcome.NOT_EVALUATED.value)
        if outcome not in {o.value for o in ValidityOutcome}:
            outcome = ValidityOutcome.NOT_EVALUATED.value
        result["outcome"] = outcome
        duration = time.monotonic() - started
        _record("record_geometry_validity", outcome)
        _record("record_cad_artifact_duration", "validate", duration)
        return result

    @staticmethod
    def _validity_record(result: dict[str, Any], *, base: str | None) -> dict[str, Any]:
        keep = (
            "is_valid",
            "solid_count",
            "closed_solid_count",
            "volume_mm3",
            "free_edge_count",
            "bounding_box",
            "kernel",
        )
        return {
            "outcome": result["outcome"],
            "findings": list(result.get("findings") or [])[:20],
            **{k: result[k] for k in keep if k in result},
            "tool": "cadquery.validate_shape",
            "checked_at": _now(),
            "base_revision": base,
        }

    # -- source ----------------------------------------------------------

    @staticmethod
    def parse_source(design_ir: Any = None, script: Any = None) -> dict[str, Any] | None:
        """``{kind, design_ir | script}`` for an explicit source; ``ExactCadError`` if bad."""
        if design_ir is not None and script is not None:
            raise ExactCadError("give design_ir or script, not both")
        if design_ir is not None:
            from twin_core.design_ir.models import DesignIR
            from twin_core.design_ir.validation import validate_design_ir

            try:
                doc = DesignIR.model_validate(design_ir)
            except Exception as exc:  # noqa: BLE001 - pydantic ValidationError and friends
                raise ExactCadError(f"design_ir is not a valid design IR document: {exc}") from exc
            errors = validate_design_ir(doc)
            if not doc.entities:
                errors = ["it has no entities", *errors]
            if errors:
                raise ExactCadError("design_ir is not buildable: " + "; ".join(errors[:5]))
            return {"kind": "design_ir", "design_ir": doc.model_dump(mode="json")}
        if script is not None:
            if not isinstance(script, str) or not script.strip():
                raise ExactCadError("script must be a non-empty CadQuery script")
            return {"kind": "cadquery_script", "script": script}
        return None

    async def _run_source(self, source: dict[str, Any]) -> tuple[bytes, dict[str, Any], str]:
        """Build exact geometry from a source; ``(step, properties, script)``."""
        if source["kind"] == "design_ir":
            from domain_agents.shared.cadquery_lowering import lower_design_ir_cadquery
            from twin_core.design_ir.models import DesignIR

            lowered = await lower_design_ir_cadquery(
                _UnwrappingBridge(self._bridge),  # type: ignore[arg-type]
                DesignIR.model_validate(source["design_ir"]),
            )
            props = {
                "volume_mm3": lowered.volume_mm3,
                "surface_area_mm2": lowered.surface_area_mm2,
                "bounding_box": lowered.bounding_box,
            }
            return lowered.step_bytes, props, lowered.script_text
        result = _data(
            await self._bridge.invoke("cadquery.execute_script", {"script": source["script"]}),
            "cadquery.execute_script",
        )
        step_b64 = result.get("step_base64")
        if not step_b64:
            raise ExactCadError("cadquery.execute_script returned no STEP")
        props = {
            k: result[k] for k in ("volume_mm3", "surface_area_mm2", "bounding_box") if k in result
        }
        return base64.b64decode(step_b64), props, str(source["script"])

    # -- attempts --------------------------------------------------------

    async def _record_attempt(
        self,
        node: Any,
        *,
        state: GeometryState,
        validity: dict[str, Any],
        job_id: str | None,
        content_hash: str | None,
    ) -> dict[str, Any]:
        attempts = list((node.metadata or {}).get(EXACT_ATTEMPTS_KEY) or [])
        attempt = {
            "attempt_id": str(uuid.uuid4()),
            "state": state.value,
            "outcome": validity.get("outcome"),
            "findings": validity.get("findings") or [],
            "content_hash": content_hash,
            "job_id": job_id,
            "attempted_at": _now(),
        }
        attempts.append(attempt)
        await set_node_metadata(self._twin, node, **{EXACT_ATTEMPTS_KEY: attempts[-MAX_ATTEMPTS:]})
        return attempt

    # -- the exact build -------------------------------------------------

    async def build(
        self,
        *,
        project_id: str,
        item_key: str,
        base_revision: str | int | None = None,
        design_ir: Any = None,
        script: Any = None,
        _source_extra: dict[str, Any] | None = None,
        _generated_from: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build (or re-validate) exact geometry for ``item_key``. See the module docstring."""
        from tool_registry.jobs import current_job_id
        from twin_core.items import ItemRevisionConflictError

        job_id = current_job_id()
        started = time.monotonic()
        with tracer.start_as_current_span("twin.exact_cad.build") as span:
            item = await self._item(project_id, item_key)
            base = int(base_revision) if base_revision not in (None, "") else item.head_revision
            span.set_attribute("item.key", item.key)
            span.set_attribute("item.base_revision", base)
            if base != item.head_revision:
                # Raised as the twin's own conflict, so the job ends stale.
                raise ItemRevisionConflictError(
                    f"{item.key} is at @{item.head_revision}, not @{base}"
                )
            head = await self._wp(item.head_node_id)
            source = self.parse_source(design_ir, script)
            if source is not None and _source_extra:
                source = {**_source_extra, **source}
            stored = (head.metadata or {}).get(EXACT_SOURCE_KEY)
            if source is None and isinstance(stored, dict) and stored.get("kind"):
                # CDE slice 9: a rebuild uses where the sketch's plane is now,
                # and is refused while that plane needs reattaching.
                source = await self._refresh_plane(dict(stored), project_id)
            elif source is not None:
                await self._check_plane_block(self._source_plane_ref(source), project_id)
            ref = f"{item.key}@{base}"
            current = geometry_state_of(head.type.value, head.metadata)
            if source is None and current in validated_states():
                # Nothing new to build and the head is already validated.
                _record("record_exact_build", "already_validated")
                return {
                    "item_key": item.key,
                    "item_ref": ref,
                    "node_id": str(head.id),
                    "geometry_state": current.value if current else None,
                    "validated": True,
                    "already_validated": True,
                    "operation_event": {
                        "message": MSG_ALREADY_VALIDATED,
                        "artifact_refs": [self._wp_ref(head, ref, current)],
                    },
                }
            if is_illustrative(head.type.value, head.metadata):
                raise ExactCadError(f"{ref} is illustrative; it has no exact geometry to build")

            span.set_attribute("exact.mode", "build" if source else "validate_in_place")
            if source is not None:
                step, properties, script_text = await self._run_source(source)
            else:
                content = self._read_blob(head)
                if not content:
                    raise ExactCadError(f"{ref} has no stored STEP to validate")
                step, properties, script_text = content, {}, ""
            content_hash = hashlib.sha256(step).hexdigest()
            _cache_put(step)
            _record("record_cad_artifact_duration", "exact_build", time.monotonic() - started)
            await self._emit(
                project_id,
                name="exact_geometry_ready",
                event_type="milestone",
                sequence=30,
                message=MSG_EXACT_READY,
                target_key=item.key,
                base_revision=str(base),
                artifact_refs=[
                    {
                        "kind": "step",
                        "id": content_hash,
                        "label": f"{item.name} exact geometry from {ref}",
                        "authority": "exact",
                    }
                ],
            )

            result = await self._validate(step)
            validity = self._validity_record(result, base=ref)
            outcome = validity["outcome"]
            span.set_attribute("validity.outcome", outcome)
            if outcome != ValidityOutcome.PASS.value:
                state = (
                    GeometryState.EXACT_READY
                    if outcome == ValidityOutcome.WARN.value
                    else GeometryState.FAILED
                )
                attempt = await self._record_attempt(
                    head, state=state, validity=validity, job_id=job_id, content_hash=content_hash
                )
                logger.warning(
                    "exact_cad_not_validated",
                    item_key=item.key,
                    base=ref,
                    outcome=outcome,
                    findings=[
                        f.get("check") for f in validity["findings"] if f.get("outcome") != "pass"
                    ],
                )
                if outcome == ValidityOutcome.WARN.value:
                    _record("record_exact_build", "not_validated")
                    return {
                        "item_key": item.key,
                        "item_ref": ref,
                        "node_id": str(head.id),
                        "validated": False,
                        "validity": validity,
                        "attempt": attempt,
                        "operation_event": {"message": MSG_NOT_VALIDATED, "artifact_refs": []},
                    }
                if outcome == ValidityOutcome.FAIL.value:
                    _record("record_exact_build", "failed")
                    raise GeometryValidityError(
                        f"{ref}: the exact geometry failed its validity check ("
                        + "; ".join(
                            f["detail"] for f in validity["findings"] if f.get("outcome") == "fail"
                        )[:400]
                        + "); the last valid revision was kept"
                    )
                _record("record_exact_build", "not_evaluated")
                raise GeometryCheckUnavailableError(
                    f"{ref}: the validity check could not run, so nothing was validated"
                )

            stale_exports = await self._ready_exports(item)
            if source is not None:
                out = await self._commit_validated(
                    item=item,
                    base=base,
                    step=step,
                    properties=properties,
                    source=source,
                    script_text=script_text,
                    validity=validity,
                    project_id=project_id,
                    job_id=job_id,
                    generated_from=_generated_from,
                )
            else:
                out = await self._validate_in_place(head, validity=validity, ref=ref)
            out["item_key"] = item.key
            if out.get("revision") and int(out["revision"]) != base:
                for node_id, export in stale_exports:
                    await self._emit_export_stale(project_id, node_id, export, item.key)
            _record("record_exact_build", "validated")
            _record("record_cad_artifact_duration", "exact_total", time.monotonic() - started)
            logger.info(
                "exact_cad_validated",
                item_key=item.key,
                base=ref,
                item_ref=out.get("item_ref"),
                node_id=out.get("node_id"),
                duration_s=round(time.monotonic() - started, 3),
            )
            return out

    @staticmethod
    def _wp_ref(wp: Any, ref: str, state: GeometryState | None) -> dict[str, Any]:
        return {
            "kind": "work_product",
            "id": str(wp.id),
            "label": f"{wp.name} ({ref})",
            "authority": authority(state) if state else "exact",
        }

    async def _commit_validated(
        self,
        *,
        item: Any,
        base: int,
        step: bytes,
        properties: dict[str, Any],
        source: dict[str, Any],
        script_text: str,
        validity: dict[str, Any],
        project_id: str,
        job_id: str | None,
        generated_from: list[str] | None = None,
    ) -> dict[str, Any]:
        check_transition(None, GeometryState.VALIDATED, validity=validity["outcome"])
        stored_source = dict(source)
        if script_text and source["kind"] == "design_ir":
            stored_source["lowered_script_sha256"] = hashlib.sha256(
                script_text.encode("utf-8")
            ).hexdigest()
        result: dict[str, Any] = await self._recorder(
            step_base64=base64.b64encode(step).decode("ascii"),
            name=item.name,
            project_id=project_id,
            source_tool=EXACT_BUILD_TOOL,
            # Pinned: if the head moved while this ran, the twin refuses the
            # write and the job ends stale; the newer head is kept.
            item_key=f"{item.key}@{base}",
            change_reason="exact geometry built and validated",
            properties=properties or None,
            source_node_ids=generated_from or None,
            extra_metadata={
                GEOMETRY_STATE_KEY: GeometryState.VALIDATED.value,
                VALIDITY_KEY: validity,
                EXACT_SOURCE_KEY: stored_source,
                "exact_build": {
                    "job_id": job_id,
                    "base_revision": base,
                    "built_at": _now(),
                    "tool": EXACT_BUILD_TOOL,
                },
            },
        )
        if result.get("already_committed"):
            # The same geometry is already the head: validate it there.
            await self._pin_plane(result["node_id"], source, project_id)
            head = await self._wp(result["node_id"])
            return await self._validate_in_place(head, validity=validity, ref=f"{item.key}@{base}")
        node_id = str(result["node_id"])
        ref = str(result.get("item_ref") or f"{item.key}@{base + 1}")
        await self._pin_plane(node_id, source, project_id)
        wp = await self._wp(node_id)
        artifact = self._wp_ref(wp, ref, GeometryState.VALIDATED)
        await self._emit(
            project_id,
            name="artifact_ready",
            event_type="partial_result",
            sequence=40,
            message=MSG_VALIDATED,
            target_key=item.key,
            base_revision=str(base),
            artifact_refs=[artifact],
        )
        return {
            "node_id": node_id,
            "item_ref": ref,
            "revision": result.get("revision"),
            # The head this job itself moved to: the runner does not call it stale.
            "target_revision": str(result.get("revision")),
            "geometry_state": GeometryState.VALIDATED.value,
            "validated": True,
            "validity": validity,
            "supersedes_node_id": result.get("supersedes_node_id"),
            "operation_event": {"message": MSG_VALIDATED, "artifact_refs": [artifact]},
        }

    async def _validate_in_place(
        self, head: Any, *, validity: dict[str, Any], ref: str
    ) -> dict[str, Any]:
        meta = dict(head.metadata or {})
        current = geometry_state_of(head.type.value, meta)
        extra: dict[str, Any] = {}
        if current is GeometryState.STALE and str(meta.get("staleness") or "") in (
            "stale",
            "invalid",
        ):
            # Stale through a pin (its sketch plane moved), and re-checked now
            # against where the plane is: revalidated, from its own state.
            extra = {"staleness": "revalidated"}
            raw = meta.get(GEOMETRY_STATE_KEY)
            current = GeometryState(str(raw)) if raw else GeometryState.EXACT_READY
            if current is GeometryState.STALE:
                current = GeometryState.EXACT_READY
            meta = {**meta, **extra}
        state = check_transition(current, GeometryState.VALIDATED, validity=validity["outcome"])
        await set_node_metadata(
            self._twin,
            head,
            **{GEOMETRY_STATE_KEY: state.value, VALIDITY_KEY: validity, **extra},
        )
        artifact = self._wp_ref(head, ref, state)
        return {
            "node_id": str(head.id),
            "item_ref": ref,
            "revision": int(ref.rpartition("@")[2]),
            "geometry_state": state.value,
            "validated": True,
            "validated_in_place": True,
            "validity": validity,
            "operation_event": {"message": MSG_VALIDATED, "artifact_refs": [artifact]},
        }

    async def _ready_exports(self, item: Any) -> list[tuple[str, dict[str, Any]]]:
        """``(node_id, export)`` for every ready STEP export of ``item``'s revisions."""
        from twin_core.items import item_history

        out: list[tuple[str, dict[str, Any]]] = []
        try:
            for rev in await item_history(self._twin, item, include_drafts=False):
                node = await self._twin.graph.get_node(rev.node_id)
                export = (getattr(node, "metadata", None) or {}).get(STEP_EXPORT_KEY)
                if isinstance(export, dict) and export.get("state") == StepExportState.READY.value:
                    out.append((str(rev.node_id), export))
        except Exception as exc:  # noqa: BLE001 - only the stale events depend on it
            logger.info("exact_cad_export_scan_failed", item_key=item.key, error=str(exc))
        return out

    async def _emit_export_stale(
        self, project_id: str, node_id: str, export: dict[str, Any], item_key: str
    ) -> None:
        job = export.get("job_id")
        if not job:
            return
        _record("record_step_export", "stale")
        await self._emit(
            project_id,
            name="stale",
            event_type="stale",
            sequence=95,
            message=MSG_STEP_STALE,
            severity="warning",
            job_id=str(job),
            artifact_refs=[
                {
                    "kind": "step",
                    "id": str(export.get("export_id") or node_id),
                    "label": f"STEP of {export.get('item_ref') or item_key}",
                    "authority": "exact",
                }
            ],
        )

    # -- STEP export -----------------------------------------------------

    async def _step_target(self, node_id: str) -> tuple[Any, Any, int]:
        wp = await self._wp(node_id)
        if wp.type.value != "cad_model":
            raise ExactCadError(f"{node_id} is a {wp.type.value}, not CAD geometry")
        found = await self._revision_of(wp.id)
        if found is None:
            raise ExactCadError(f"{wp.name} is not a revision of an item; commit it first")
        item, revision = found
        state = geometry_state_of(wp.type.value, wp.metadata)
        if state not in validated_states():
            raise StepExportRefusedError(
                f"{item.key}@{revision} is {state.value if state else 'not geometry'}; a STEP "
                "is exported only from a validated revision. Build or validate it first."
            )
        return wp, item, revision

    @staticmethod
    def cached_export(wp: Any) -> dict[str, Any] | None:
        """The revision's ready STEP export for its current geometry, if any."""
        export = (wp.metadata or {}).get(STEP_EXPORT_KEY)
        if not isinstance(export, dict):
            return None
        if export.get("state") not in (StepExportState.READY.value, StepExportState.STALE.value):
            return None
        if export.get("content_hash") != getattr(wp, "content_hash", None):
            return None
        return export

    async def export_step(self, *, node_id: str, critical: bool = False) -> dict[str, Any]:
        """Export a validated revision as STEP, with its round trip. See the module docstring."""
        from tool_registry.jobs import current_job_id

        job_id = current_job_id()
        started = time.monotonic()
        with tracer.start_as_current_span("twin.exact_cad.export_step") as span:
            wp, item, revision = await self._step_target(node_id)
            ref = f"{item.key}@{revision}"
            span.set_attribute("item.ref", ref)
            cached = self.cached_export(wp)
            if cached is not None:
                _record("record_step_export", "cache_hit")
                return self._export_result(wp, item, revision, cached, critical=critical, hit=True)
            _record("record_step_export", "cache_miss")
            project_id = str(wp.project_id) if wp.project_id else None
            try:
                content = self._read_blob(wp)
                if not content:
                    raise StepExportError(f"{ref} has no stored STEP to export from")
                exported = _data(
                    await self._bridge.invoke(
                        "cadquery.export_step",
                        {"step_base64": base64.b64encode(content).decode("ascii")},
                    ),
                    "cadquery.export_step",
                )
                step_b64 = exported.get("step_base64")
                if not step_b64:
                    raise StepExportError("cadquery.export_step returned no STEP")
                written = base64.b64decode(step_b64)
            except (StepExportError, ExactCadError):
                await self._mark_export_failed(wp, ref, job_id)
                raise
            except Exception as exc:  # noqa: BLE001 - recorded on the revision, then raised
                await self._mark_export_failed(wp, ref, job_id, error=str(exc))
                raise StepExportError(f"{ref}: STEP export failed: {exc}") from exc
            _record("record_cad_artifact_duration", "step_export", time.monotonic() - started)

            rt_started = time.monotonic()
            reimport = await self._validate(written)
            outcome, findings = compare_round_trip(exported.get("source"), reimport)
            if reimport.get("outcome") == ValidityOutcome.NOT_EVALUATED.value:
                outcome = ValidityOutcome.NOT_EVALUATED.value
            _record("record_step_round_trip", outcome)
            _record("record_cad_artifact_duration", "round_trip", time.monotonic() - rt_started)
            span.set_attribute("round_trip.outcome", outcome)

            file_sha = _cache_put(written)
            filename = f"{_slug(item.name)}-r{revision}.step"
            object_key = self._write_blob(str(wp.id), filename, written)
            export = {
                "export_id": str(uuid.uuid4()),
                "state": StepExportState.READY.value,
                "item_key": item.key,
                "revision": revision,
                "item_ref": ref,
                "content_hash": wp.content_hash,
                "file_sha256": file_sha,
                "filename": filename,
                "size_bytes": len(written),
                "object_key": object_key,
                "schema": exported.get("schema"),
                "schema_requested": exported.get("schema_requested"),
                "kernel": exported.get("kernel"),
                "round_trip": {
                    "outcome": outcome,
                    "findings": findings,
                    "source": {
                        k: (exported.get("source") or {}).get(k)
                        for k in ("solid_count", "volume_mm3", "bounding_box")
                    },
                    "reimported": {
                        k: reimport.get(k) for k in ("solid_count", "volume_mm3", "bounding_box")
                    },
                    "tolerance": {
                        "volume_rel": ROUND_TRIP_VOLUME_REL,
                        "bbox_abs_mm": ROUND_TRIP_BBOX_ABS_MM,
                        "bbox_rel": ROUND_TRIP_BBOX_REL,
                    },
                    "reader": "cadquery.validate_shape (OCCT STEPControl_Reader)",
                },
                "critical": bool(critical),
                "job_id": job_id,
                "exported_at": _now(),
                "duration_s": round(time.monotonic() - started, 3),
            }
            if critical and outcome != ValidityOutcome.PASS.value:
                export["state"] = StepExportState.FAILED.value
                export["error"] = "a gate or release export needs a passing round trip"
            await set_node_metadata(self._twin, wp, **{STEP_EXPORT_KEY: export})
            logger.info(
                "step_export_recorded",
                item_ref=ref,
                schema=export["schema"],
                round_trip=outcome,
                size_bytes=len(written),
                critical=critical,
                duration_s=export["duration_s"],
            )
            if export["state"] == StepExportState.FAILED.value:
                _record("record_step_export", "failed")
                raise StepExportError(
                    f"{ref}: the STEP re-import round trip is {outcome}, and a gate or release "
                    "export needs it to pass"
                )
            _record("record_step_export", "ready")
            await self._emit(
                project_id,
                name="artifact_ready",
                event_type="partial_result",
                sequence=40,
                message=MSG_STEP_READY
                if outcome == ValidityOutcome.PASS.value
                else MSG_STEP_ROUND_TRIP_FAILED,
                artifact_refs=[self._step_ref(wp, export)],
            )
            return self._export_result(wp, item, revision, export, critical=critical, hit=False)

    @staticmethod
    def _step_ref(wp: Any, export: dict[str, Any]) -> dict[str, Any]:
        passed = (export.get("round_trip") or {}).get("outcome") == ValidityOutcome.PASS.value
        return {
            "kind": "step",
            "id": str(export.get("export_id")),
            "label": f"STEP of {export.get('item_ref')}",
            "url": f"/v1/twin/nodes/{wp.id}/exports/step/file",
            "authority": "validated" if passed else "exact",
        }

    def _export_result(
        self,
        wp: Any,
        item: Any,
        revision: int,
        export: dict[str, Any],
        *,
        critical: bool,
        hit: bool,
    ) -> dict[str, Any]:
        view = step_export_view(
            {STEP_EXPORT_KEY: export},
            revision=revision,
            head_revision=item.head_revision,
            content_hash=wp.content_hash,
        ) or dict(export)
        passed = (view.get("round_trip") or {}).get("outcome") == ValidityOutcome.PASS.value
        if critical and not passed:
            raise StepExportError(
                f"{item.key}@{revision}: the recorded STEP's round trip did not pass, and a gate "
                "or release export needs it to"
            )
        ref = self._step_ref(wp, view)
        return {
            "node_id": str(wp.id),
            "item_key": item.key,
            "item_ref": f"{item.key}@{revision}",
            "cache_hit": hit,
            "step_export": view,
            "url": ref["url"],
            "operation_event": {
                "message": MSG_STEP_READY if passed else MSG_STEP_ROUND_TRIP_FAILED,
                "artifact_refs": [ref],
            },
        }

    async def _mark_export_failed(
        self, wp: Any, ref: str, job_id: str | None, *, error: str | None = None
    ) -> None:
        _record("record_step_export", "failed")
        try:
            await set_node_metadata(
                self._twin,
                wp,
                **{
                    STEP_EXPORT_KEY: {
                        "state": StepExportState.FAILED.value,
                        "item_ref": ref,
                        "content_hash": wp.content_hash,
                        "job_id": job_id,
                        "error": (error or "export failed")[:300],
                        "failed_at": _now(),
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001 - the failure is still raised
            logger.warning("step_export_failure_unrecorded", item_ref=ref, error=str(exc))

    async def mark_export_queued(self, node_id: str, job_id: str) -> None:
        """Say on the revision that its STEP is queued (no ready export is overwritten)."""
        wp = await self._wp(node_id)
        if self.cached_export(wp) is not None:
            return
        await set_node_metadata(
            self._twin,
            wp,
            **{
                STEP_EXPORT_KEY: {
                    "state": StepExportState.QUEUED.value,
                    "content_hash": wp.content_hash,
                    "job_id": job_id,
                    "queued_at": _now(),
                }
            },
        )

    # -- from a parametric sketch ----------------------------------------

    async def parametric_source(
        self,
        psk_node: Any,
        *,
        feature_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A buildable design IR from an approved parametric sketch, or ``ExactCadError``.

        The feature plan's ``required_input`` parameters (depth, angle,
        material) are never invented: each must be ``provided`` on the plan
        or supplied in ``inputs`` by the person asking. The sketch must be
        approved, in millimetres and on a confirmed principal plane.
        """
        from twin_core.design_ir.sketch_compiler import ParametricSketch

        meta = dict(psk_node.metadata or {})
        if not meta.get("approved"):
            raise ExactCadError(
                "this parametric sketch has not been approved yet; approve it before exact CAD"
            )
        try:
            sketch = ParametricSketch.model_validate(meta.get("parametric_sketch") or {})
        except Exception as exc:  # noqa: BLE001 - a stored sketch that does not parse
            raise ExactCadError(f"the parametric sketch could not be read: {exc}") from exc
        plan = list(sketch.feature_plan)
        if not plan:
            raise ExactCadError("the parametric sketch has no feature plan (no closed profile)")
        chosen = next((f for f in plan if f.id == feature_id), None) if feature_id else plan[0]
        if chosen is None:
            raise ExactCadError(
                f"no feature {feature_id!r} in the plan; choose one of {[f.id for f in plan]}"
            )
        supplied = dict(inputs or {})
        values: dict[str, Any] = {}
        missing: list[str] = []
        for name, param in chosen.parameters.items():
            if name in supplied and supplied[name] not in (None, ""):
                values[name] = supplied[name]
            elif param.status == "provided" and param.value not in (None, ""):
                values[name] = param.value
            else:
                missing.append(f"{name} ({param.question})" if param.question else name)
        if missing:
            raise RequiredInputMissingError(
                f"{chosen.id} needs input before exact CAD: "
                + "; ".join(missing)
                + ". Nothing is assumed for these."
            )
        if sketch.unit != "mm":
            raise ExactCadError(
                f"the sketch's unit is {sketch.unit}; place it on a plane in millimetres first"
            )
        if not sketch.plane_id:
            raise ExactCadError("the sketch is not placed on a plane yet")
        placement, plane_used = await self._plane_fields(str(sketch.plane_id), psk_node.project_id)
        numeric = "depth" if chosen.op == "extrude" else "angle"
        try:
            amount = float(values[numeric])
        except (TypeError, ValueError) as exc:
            raise ExactCadError(f"{numeric} must be a number, not {values[numeric]!r}") from exc
        if amount <= 0:
            raise ExactCadError(f"{numeric} must be above zero")
        profile = set(chosen.profile_primitive_ids)
        entity = sketch.to_sketch_entity("sketch1", "body1", **placement)
        elements = [
            el
            for el in entity.elements
            if el.construction or not profile or getattr(el, "id", None) in profile
        ]
        entities: list[dict[str, Any]] = [
            {"id": "body1", "op": "create_body"},
            entity.model_copy(update={"elements": elements}).model_dump(mode="json"),
        ]
        if chosen.op == "extrude":
            entities.append(
                {
                    "id": "feature1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "sketch1",
                    "depth": amount,
                }
            )
        else:
            entities.append(
                {
                    "id": "feature1",
                    "op": "revolve",
                    "body_ref": "body1",
                    "sketch_ref": "sketch1",
                    "angle": min(amount, 360.0),
                }
            )
        source = self.parse_source({"entities": entities}, None)
        assert source is not None
        source["feature_id"] = chosen.id
        source["inputs"] = values
        source["plane_ref"] = plane_used
        return source

    async def _check_plane_block(self, plane_ref: str | None, project_id: Any) -> None:
        """Refuse (409) while the sketch's plane needs reattaching (PLN-010)."""
        from api_gateway.twin.face_attachment import plane_block_reason, record_blocked

        reason = await plane_block_reason(self._twin, project_id, plane_ref)
        if reason:
            record_blocked("exact_build")
            logger.warning("exact_build_blocked_by_plane", plane_ref=plane_ref, reason=reason)
            raise PlaneReattachmentRequiredError(f"exact CAD refused: {reason}")

    async def _plane_fields(self, plane_ref: str, project_id: Any) -> tuple[dict[str, Any], str]:
        """``(design-IR placement, plane ref used)``; a face plane follows its head."""
        from api_gateway.twin.face_attachment import resolve_build_plane
        from twin_core.items import ItemError

        await self._check_plane_block(plane_ref, project_id)
        try:
            return await resolve_build_plane(self._twin, plane_ref, project_id)
        except (ItemError, KeyError, ValueError) as exc:
            raise ExactCadError(
                f"the sketch's plane {plane_ref} cannot be used for CAD: {exc}"
            ) from exc

    @staticmethod
    def _source_plane_ref(source: dict[str, Any] | None) -> str | None:
        """The sketch plane a build source depends on, if any."""
        if not isinstance(source, dict):
            return None
        if source.get("plane_ref"):
            return str(source["plane_ref"])
        for entity in (source.get("design_ir") or {}).get("entities") or []:
            if isinstance(entity, dict) and entity.get("op") == "sketch" and entity.get("plane_id"):
                return str(entity["plane_id"])
        return None

    async def _refresh_plane(self, source: dict[str, Any], project_id: Any) -> dict[str, Any]:
        """A stored source with the current frame of its (face) plane; refuses when blocked.

        A face plane moves with its face (a remap); rebuilding from the stored
        design IR must use where the plane is now, not where it was.
        """
        plane_ref = self._source_plane_ref(source)
        if not plane_ref:
            return source
        await self._check_plane_block(plane_ref, project_id)
        if source.get("kind") != "design_ir":
            return source
        fields, used = await self._plane_fields(plane_ref, project_id)
        if used == source.get("plane_ref") or "frame" not in fields:
            return source
        doc = dict(source.get("design_ir") or {})
        entities = []
        for entity in doc.get("entities") or []:
            if isinstance(entity, dict) and entity.get("op") == "sketch" and entity.get("plane_id"):
                entity = {**entity, **fields}
            entities.append(entity)
        doc["entities"] = entities
        logger.info(
            "exact_build_plane_refreshed", plane_from=source.get("plane_ref"), plane_to=used
        )
        return {**source, "design_ir": doc, "plane_ref": used}

    async def _pin_plane(self, node_id: Any, source: dict[str, Any], project_id: Any) -> None:
        plane_ref = self._source_plane_ref(source)
        if not plane_ref:
            return
        from api_gateway.twin.face_attachment import pin_build_to_plane

        await pin_build_to_plane(self._twin, node_id, plane_ref, project_id)

    async def build_from_parametric_sketch(
        self,
        *,
        project_id: str,
        sketch_key: str,
        feature_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        base_revision: str | int | None = None,
    ) -> dict[str, Any]:
        """Exact CAD from an approved parametric sketch, committed to ``CAD-<sketch>``."""
        from twin_core.items import ItemRevisionConflictError, find_item

        psk_item = await self._parametric_item(project_id, sketch_key)
        psk = await self._wp(psk_item.head_node_id)
        source = await self.parametric_source(psk, feature_id=feature_id, inputs=inputs)
        source["parametric_sketch_ref"] = f"{psk_item.key}@{psk_item.head_revision}"
        cad_key = cad_key_for_sketch(psk_item.key)
        cad = await find_item(self._twin, cad_key, project_id)
        if cad is not None and cad.head_node_id is not None:
            head = str(cad.head_revision)
            if base_revision not in (None, "") and str(base_revision) != head:
                raise ItemRevisionConflictError(f"{cad_key} is at @{head}, not @{base_revision}")
            return await self.build(
                project_id=project_id,
                item_key=cad_key,
                base_revision=head,
                design_ir=source["design_ir"],
                _source_extra=source,
                _generated_from=[str(psk.id)],
            )
        return await self._build_new_item(
            project_id=project_id,
            cad_key=cad_key,
            name=psk.name.removeprefix("Parametric sketch: ") or psk.name,
            source=source,
            psk=psk,
        )

    async def _parametric_item(self, project_id: str, sketch_key: str) -> Any:
        from twin_core.items import find_item, parse_item_ref

        try:
            key, _pin = parse_item_ref(sketch_key)
        except ValueError as exc:
            raise ExactCadError(str(exc)) from exc
        item = await find_item(self._twin, key, project_id)
        if item is None or item.head_node_id is None or item.item_type != "parametric_sketch":
            raise ExactCadError(f"no parametric sketch {key} with a current revision")
        return item

    async def _build_new_item(
        self, *, project_id: str, cad_key: str, name: str, source: dict[str, Any], psk: Any
    ) -> dict[str, Any]:
        """The first exact revision of a sketch's CAD item: born validated, or nothing."""
        from tool_registry.jobs import current_job_id

        job_id = current_job_id()
        step, properties, script_text = await self._run_source(source)
        content_hash = hashlib.sha256(step).hexdigest()
        _cache_put(step)
        await self._emit(
            project_id,
            name="exact_geometry_ready",
            event_type="milestone",
            sequence=30,
            message=MSG_EXACT_READY,
            target_key=cad_key,
            artifact_refs=[
                {
                    "kind": "step",
                    "id": content_hash,
                    "label": f"{name} exact geometry",
                    "authority": "exact",
                }
            ],
        )
        validity = self._validity_record(await self._validate(step), base=None)
        if validity["outcome"] != ValidityOutcome.PASS.value:
            await self._record_attempt(
                psk,
                state=GeometryState.EXACT_READY
                if validity["outcome"] == ValidityOutcome.WARN.value
                else GeometryState.FAILED,
                validity=validity,
                job_id=job_id,
                content_hash=content_hash,
            )
            _record(
                "record_exact_build", "failed" if validity["outcome"] == "fail" else "not_validated"
            )
            if validity["outcome"] == ValidityOutcome.FAIL.value:
                raise GeometryValidityError(
                    f"{cad_key}: the exact geometry failed its validity check"
                )
            if validity["outcome"] == ValidityOutcome.NOT_EVALUATED.value:
                raise GeometryCheckUnavailableError(f"{cad_key}: the validity check could not run")
            return {
                "item_key": cad_key,
                "validated": False,
                "validity": validity,
                "operation_event": {"message": MSG_NOT_VALIDATED, "artifact_refs": []},
            }
        result: dict[str, Any] = await self._recorder(
            step_base64=base64.b64encode(step).decode("ascii"),
            name=name,
            project_id=project_id,
            source_tool=EXACT_BUILD_TOOL,
            item_key=cad_key,
            change_reason="exact geometry built from an approved parametric sketch",
            properties=properties or None,
            source_node_ids=[str(psk.id)],
            extra_metadata={
                GEOMETRY_STATE_KEY: GeometryState.VALIDATED.value,
                VALIDITY_KEY: validity,
                EXACT_SOURCE_KEY: source,
                "exact_build": {
                    "job_id": job_id,
                    "base_revision": 0,
                    "built_at": _now(),
                    "tool": EXACT_BUILD_TOOL,
                },
            },
        )
        _record("record_exact_build", "validated")
        ref = str(result.get("item_ref") or f"{cad_key}@1")
        await self._pin_plane(result["node_id"], source, project_id)
        wp = await self._wp(result["node_id"])
        artifact = self._wp_ref(wp, ref, GeometryState.VALIDATED)
        return {
            "item_key": cad_key,
            "node_id": str(result["node_id"]),
            "item_ref": ref,
            "revision": result.get("revision"),
            "target_revision": str(result.get("revision")),
            "geometry_state": GeometryState.VALIDATED.value,
            "validated": True,
            "validity": validity,
            "operation_event": {"message": MSG_VALIDATED, "artifact_refs": [artifact]},
        }

    # -- jobs ------------------------------------------------------------

    async def start_build(
        self,
        *,
        project_id: str,
        item_key: str,
        base_revision: str | int | None = None,
        design_ir: Any = None,
        script: Any = None,
        session_id: str,
        wait: bool = False,
        feature_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start the exact build as a job (or run it now with ``wait``).

        ``item_key`` may name an approved parametric sketch (``PSK-*``): its
        chosen feature is built into ``CAD-*`` once every ``required_input``
        is supplied (on the plan or in ``inputs``); otherwise it is refused at
        once, and nothing is assumed.

        Everything that would refuse the request is checked first, so a bad
        call fails at once rather than on the job.
        """
        from twin_core.items import ItemRevisionConflictError

        psk_item = await self._maybe_parametric(project_id, item_key)
        if psk_item is not None:
            return await self._start_parametric_build(
                project_id=project_id,
                psk_item=psk_item,
                feature_id=feature_id,
                inputs=inputs,
                base_revision=base_revision,
                session_id=session_id,
                wait=wait,
            )
        item = await self._item(project_id, item_key)
        parsed = self.parse_source(design_ir, script)
        if parsed is None:
            head = await self._wp(item.head_node_id)
            parsed = (head.metadata or {}).get(EXACT_SOURCE_KEY)
        # CDE slice 9: refused at once while the sketch's plane needs reattaching.
        await self._check_plane_block(self._source_plane_ref(parsed), project_id)
        base = str(base_revision) if base_revision not in (None, "") else str(item.head_revision)
        if base != str(item.head_revision):
            # Already behind: say so now instead of paying for a stale job.
            raise ItemRevisionConflictError(
                f"{item.key} is at @{item.head_revision}, not @{base}; re-read it and build "
                "from the current head"
            )
        arguments: dict[str, Any] = {
            "project_id": project_id,
            "item_key": item.key,
            "base_revision": base,
        }
        if design_ir is not None:
            arguments["design_ir"] = design_ir
        if script is not None:
            arguments["script"] = script
        if wait:
            return await self.build(**arguments)
        runner = self._job_runner()
        job, reused = await runner.start(
            EXACT_BUILD_TOOL,
            arguments,
            lambda: self.build(**arguments),
            session_id=session_id,
            project_id=project_id,
            expected_seconds=EXACT_BUILD_EXPECTED_SECONDS,
            target_key=item.key,
            base_revision=base,
        )
        logger.info(
            "exact_cad_job_started",
            job_id=job.job_id,
            reused=reused,
            item_key=item.key,
            base_revision=base,
        )
        return dict(runner.reply(job, reused))

    async def _maybe_parametric(self, project_id: str, item_key: str) -> Any:
        from twin_core.items import find_item, parse_item_ref

        try:
            key, _pin = parse_item_ref(item_key)
            UUID(str(project_id))
        except ValueError:
            return None
        item = await find_item(self._twin, key, project_id)
        return item if item is not None and item.item_type == "parametric_sketch" else None

    async def _start_parametric_build(
        self,
        *,
        project_id: str,
        psk_item: Any,
        feature_id: str | None,
        inputs: dict[str, Any] | None,
        base_revision: str | int | None,
        session_id: str,
        wait: bool,
    ) -> dict[str, Any]:
        from twin_core.items import find_item

        if psk_item.head_node_id is None:
            raise ExactCadError(f"{psk_item.key} has no current revision")
        # Refuses now when a required input is missing: nothing is invented.
        await self.parametric_source(
            await self._wp(psk_item.head_node_id), feature_id=feature_id, inputs=inputs
        )
        arguments: dict[str, Any] = {"project_id": project_id, "sketch_key": psk_item.key}
        if feature_id:
            arguments["feature_id"] = feature_id
        if inputs:
            arguments["inputs"] = dict(inputs)
        cad_key = cad_key_for_sketch(psk_item.key)
        cad = await find_item(self._twin, cad_key, project_id)
        base = str(cad.head_revision) if cad is not None and cad.head_node_id else None
        if base is not None:
            arguments["base_revision"] = str(base_revision) if base_revision else base
        if wait:
            return await self.build_from_parametric_sketch(**arguments)
        runner = self._job_runner()
        job, reused = await runner.start(
            EXACT_BUILD_TOOL,
            arguments,
            lambda: self.build_from_parametric_sketch(**arguments),
            session_id=session_id,
            project_id=project_id,
            expected_seconds=EXACT_BUILD_EXPECTED_SECONDS,
            target_key=cad_key,
            base_revision=base,
        )
        return dict(runner.reply(job, reused))

    async def start_export(
        self, *, node_id: str, critical: bool = False, session_id: str, wait: bool = False
    ) -> dict[str, Any]:
        """A cached export at once, else the export as a job (or now with ``wait``)."""
        wp, item, revision = await self._step_target(node_id)
        cached = self.cached_export(wp)
        if cached is not None:
            _record("record_step_export", "cache_hit")
            out = self._export_result(wp, item, revision, cached, critical=critical, hit=True)
            out["status"] = "succeeded"
            return out
        if wait:
            return await self.export_step(node_id=node_id, critical=critical)
        runner = self._job_runner()
        project_id = str(wp.project_id) if wp.project_id else None
        arguments = {"node_id": str(wp.id), "critical": bool(critical)}
        job, reused = await runner.start(
            STEP_EXPORT_TOOL,
            arguments,
            lambda: self.export_step(node_id=str(wp.id), critical=bool(critical)),
            session_id=session_id,
            project_id=project_id,
            expected_seconds=STEP_EXPORT_EXPECTED_SECONDS,
            # Not an item key, so an exact build on the item never supersedes it.
            target_key=f"STEP:{item.key}@{revision}",
        )
        if not reused:
            await self.mark_export_queued(str(wp.id), job.job_id)
        return dict(runner.reply(job, reused))

    def build_retry(self, arguments: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
        """The work of a retried build job, from its stored arguments."""
        if arguments.get("sketch_key"):
            psk_args = {
                k: arguments[k]
                for k in ("project_id", "sketch_key", "feature_id", "inputs", "base_revision")
                if k in arguments
            }
            return lambda: self.build_from_parametric_sketch(**psk_args)
        args = {
            k: arguments[k]
            for k in ("project_id", "item_key", "base_revision", "design_ir", "script")
            if k in arguments
        }
        if not args.get("project_id") or not args.get("item_key"):
            raise ValueError("the failed build job has no project or item to retry")
        return lambda: self.build(**args)

    def export_retry(self, arguments: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError("the failed export job names no revision")
        critical = bool(arguments.get("critical"))
        return lambda: self.export_step(node_id=str(node_id), critical=critical)

    def register_retries(self) -> None:
        from tool_registry.jobs import register_retry

        register_retry(EXACT_BUILD_TOOL, self.build_retry)
        register_retry(STEP_EXPORT_TOOL, self.export_retry)

    # -- read model ------------------------------------------------------

    async def artifact_view(self, node_id: str) -> dict[str, Any]:
        """Geometry state and GLB, native CAD and STEP states of one revision (UI-010)."""
        wp = await self._wp(node_id)
        meta = dict(wp.metadata or {})
        found = await self._revision_of(wp.id)
        item, revision = found if found is not None else (None, None)
        head_revision = item.head_revision if item is not None else None
        state = geometry_state_of(wp.type.value, meta)
        has_blob = bool(meta.get("minio_object_key") or wp.file_path or wp.content_hash)
        exports: list[dict[str, Any]] = [
            {
                "format": "glb",
                "state": "READY" if has_blob else "NONE",
                "detail": "converted from this revision's exact geometry on view"
                if has_blob
                else "no stored geometry to convert",
            },
            {
                "format": "native",
                "state": "READY"
                if (meta.get(EXACT_SOURCE_KEY) or meta.get("script_node_id"))
                else "NONE",
                "detail": "feature source kept with this revision"
                if (meta.get(EXACT_SOURCE_KEY) or meta.get("script_node_id"))
                else "no native feature source recorded",
            },
        ]
        step = step_export_view(
            meta, revision=revision, head_revision=head_revision, content_hash=wp.content_hash
        )
        step_state = str((step or {}).get("state") or "")
        exports.append(
            {
                "format": "step",
                "state": step_state.upper() if step_state else "NONE",
                "detail": (step or {}).get("stale_reason")
                or (step or {}).get("error")
                or ("" if step else "not exported yet"),
                "schema": (step or {}).get("schema"),
                "round_trip": ((step or {}).get("round_trip") or {}).get("outcome"),
                "url": f"/v1/twin/nodes/{wp.id}/exports/step/file"
                if step_state in ("ready", "stale")
                else None,
                "revision": (step or {}).get("revision"),
            }
        )
        last_validated = await self._last_validated(item) if item is not None else None
        attempts = list(meta.get(EXACT_ATTEMPTS_KEY) or [])
        return {
            "node_id": str(wp.id),
            "item_key": item.key if item is not None else None,
            "revision": revision,
            "head_revision": head_revision,
            "is_head": revision is not None and revision == head_revision,
            "geometry_state": state.value if state else None,
            "badge": badge(state),
            "authority": authority(state),
            "validity": meta.get(VALIDITY_KEY),
            "latest_attempt": attempts[-1] if attempts else None,
            "last_validated": last_validated,
            "can_export_step": state in validated_states(),
            "exports": exports,
        }

    async def _last_validated(self, item: Any) -> dict[str, Any] | None:
        from twin_core.items import item_history

        try:
            history = await item_history(self._twin, item, include_drafts=False)
        except Exception:  # noqa: BLE001 - optional context
            return None
        for rev in sorted(history, key=lambda r: r.revision, reverse=True):
            node = await self._twin.graph.get_node(rev.node_id)
            meta = getattr(node, "metadata", None) or {}
            wp_type = getattr(getattr(node, "type", None), "value", "cad_model")
            if geometry_state_of(str(wp_type), meta) in validated_states():
                return {
                    "revision": rev.revision,
                    "node_id": str(rev.node_id),
                    "item_ref": f"{item.key}@{rev.revision}",
                    "is_head": rev.revision == item.head_revision,
                }
        return None


def geometry_state_view(wp_type: str, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """``{state, badge, authority, itemKey, sourceRevision}`` for a node response."""
    meta = metadata or {}
    state = geometry_state_of(wp_type, meta)
    if state is None:
        return None
    revision = meta.get("item_revision")
    return {
        "state": state.value,
        "badge": badge(state),
        "authority": authority(state),
        "itemKey": meta.get("item_key"),
        "sourceRevision": int(revision) if isinstance(revision, int) else None,
    }


_service: ExactCadService | None = None


def set_exact_cad_service(service: ExactCadService | None) -> None:
    global _service  # noqa: PLW0603
    _service = service
    if service is not None:
        service.register_retries()


def get_exact_cad_service() -> ExactCadService | None:
    return _service


__all__ = [
    "EXACT_BUILD_TOOL",
    "STEP_EXPORT_TOOL",
    "ExactCadError",
    "ExactCadService",
    "GeometryCheckUnavailableError",
    "GeometryValidityError",
    "PlaneReattachmentRequiredError",
    "StepExportError",
    "StepExportRefusedError",
    "compare_round_trip",
    "geometry_state_view",
    "get_exact_cad_service",
    "read_export_file",
    "set_exact_cad_service",
]
