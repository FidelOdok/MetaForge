"""Golden-path example routes (the desk lamp, step 1).

- ``GET /v1/examples``: the examples this gateway can seed.
- ``POST /v1/examples/desk-lamp/seed``: create the desk lamp project, or fill
  in what an earlier seed left missing. Idempotent; never overwrites, and
  refuses (409) a same-named project it did not create.
- ``GET /v1/examples/desk-lamp/projects/{project_id}/manifest``: every
  golden-path step as produced (with refs, record ids, run ids, links) or
  missing (with the step that would produce it), and each contract's status.

The logic lives in :mod:`api_gateway.golden_path.desk_lamp`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.golden_path.routes")

router = APIRouter(prefix="/v1/examples", tags=["examples"])


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    twin = get_twin()
    if twin is None:
        raise HTTPException(status_code=503, detail="The digital twin is not available")
    return twin


def _http(exc: Exception) -> HTTPException:
    from api_gateway.golden_path.desk_lamp import GoldenPathError
    from api_gateway.twin.interface_contracts import ContractError
    from api_gateway.twin.manufacturing_context import ManufacturingContextError

    if isinstance(exc, GoldenPathError | ContractError | ManufacturingContextError):
        return HTTPException(status_code=exc.status, detail=str(exc))
    return HTTPException(status_code=502, detail="The desk lamp example could not be seeded")


@router.get("")
async def list_examples() -> dict[str, Any]:
    """The golden-path examples this gateway can seed."""
    from api_gateway.golden_path.desk_lamp import EXAMPLE_ID, definition_path

    path = definition_path()
    return {
        "examples": [
            {
                "id": EXAMPLE_ID,
                "title": "Desk lamp (golden path)",
                "installed": path.exists(),
                "seed": f"/v1/examples/{EXAMPLE_ID}/seed",
            }
        ]
    }


@router.post("/desk-lamp/seed")
async def seed_desk_lamp_route(request: Request) -> dict[str, Any]:
    """Create the desk lamp project, or complete it. Never overwrites."""
    from api_gateway.auth.approver import approver_from_request
    from api_gateway.golden_path.desk_lamp import seed_desk_lamp
    from api_gateway.projects.routes import get_project_backend

    twin = _twin()
    actor = approver_from_request(request).label
    with tracer.start_as_current_span("golden_path.routes.seed") as span:
        try:
            result = await seed_desk_lamp(twin, get_project_backend(), actor=actor)
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("golden_path_seed_failed", error=str(exc))
            raise _http(exc) from exc
        return result.model_dump(mode="json")


@router.get("/desk-lamp/projects/{project_id}/manifest")
async def desk_lamp_manifest(project_id: str) -> dict[str, Any]:
    """What the golden path has produced so far, and what is still missing."""
    from api_gateway.golden_path.desk_lamp import build_manifest

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None
    twin = _twin()
    with tracer.start_as_current_span("golden_path.routes.manifest") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await build_manifest(twin, project_id)
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("golden_path_manifest_failed", project_id=project_id, error=str(exc))
            raise _http(exc) from exc
