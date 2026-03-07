"""REST endpoints for CAD file conversion."""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse

from api_gateway.convert.schemas import ConversionResult
from api_gateway.convert.service import ConversionService
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.convert.routes")

router = APIRouter(prefix="/api/v1/convert", tags=["convert"])

# Module-level service instance (initialised lazily so tests can replace it).
_service: ConversionService | None = None

ALLOWED_EXTENSIONS = {".step", ".stp", ".iges", ".igs"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def get_service() -> ConversionService:
    global _service
    if _service is None:
        _service = ConversionService()
    return _service


@router.post("", response_model=ConversionResult)
async def upload_and_convert(
    file: UploadFile = File(..., description="STEP or IGES CAD file"),
    quality: str = Query("standard", pattern="^(preview|standard|fine)$"),
) -> ConversionResult:
    """Upload a STEP/IGES file and convert to GLB.

    Returns the conversion result with a URL to the GLB file and metadata.
    Results are cached by content hash — re-uploading the same file is instant.
    """
    with tracer.start_as_current_span("upload_and_convert") as span:
        # Validate extension
        filename = file.filename or "upload.step"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        # Read file content
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large (max 100 MB)")
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        span.set_attribute("file.name", filename)
        span.set_attribute("file.size", len(content))

        service = get_service()
        try:
            result = service.convert(content, filename, quality)
        except Exception as exc:
            logger.error("conversion_failed", filename=filename, error=str(exc))
            span.record_exception(exc)
            raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}") from exc

        return ConversionResult(**result)


@router.get("/{file_hash}", response_model=ConversionResult)
async def get_conversion(
    file_hash: str,
    quality: str = Query("standard", pattern="^(preview|standard|fine)$"),
) -> ConversionResult:
    """Retrieve a cached conversion result by content hash."""
    service = get_service()
    metadata = service.get_metadata(file_hash, quality)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Conversion not found")

    return ConversionResult(
        hash=file_hash,
        glb_url=f"/api/v1/convert/{file_hash}/glb?quality={quality}",
        metadata=metadata,
        cached=True,
    )


@router.get("/{file_hash}/glb")
async def get_glb(
    file_hash: str,
    quality: str = Query("standard", pattern="^(preview|standard|fine)$"),
) -> FileResponse:
    """Download the converted GLB file."""
    service = get_service()
    glb_path = service.get_glb_path(file_hash, quality)
    if glb_path is None:
        raise HTTPException(status_code=404, detail="GLB file not found")

    return FileResponse(
        path=str(glb_path),
        media_type="model/gltf-binary",
        filename="model.glb",
    )


@router.get("/{file_hash}/metadata")
async def get_metadata(
    file_hash: str,
    quality: str = Query("standard", pattern="^(preview|standard|fine)$"),
) -> dict:
    """Retrieve conversion metadata (part tree, stats, materials)."""
    service = get_service()
    metadata = service.get_metadata(file_hash, quality)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Metadata not found")
    return metadata
