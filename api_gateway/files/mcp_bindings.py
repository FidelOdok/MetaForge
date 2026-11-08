"""The in-process ``files.*`` binding: same shapes as the routes.

Used inside the gateway (its own tool registry) and by the local plugin
(``metaforge-local``: stdio, no gateway). With ``local_root`` set, the local
plugin may also read files from, and save files to, that folder on the
person's own machine (``files.import_local``, ``files.save_local``); nothing
outside it is reachable, and no other binding offers this.
"""

from __future__ import annotations

import base64
import binascii
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from api_gateway.files import links, service
from api_gateway.uploads import service as uploads
from api_gateway.uploads.routes import MAX_INLINE_UPLOAD_BYTES, get_upload_store

#: Folders a local import never walks into.
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".forge"})


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _error(exc: Exception) -> ValueError:
    return ValueError(str(exc))


class LocalFilesService:
    def __init__(self, *, local_root: Path | None = None) -> None:
        self.local_root = local_root.resolve() if local_root else None

    @property
    def supports_local_paths(self) -> bool:
        return self.local_root is not None

    def absolute(self, path: str) -> str:
        base = os.environ.get("METAFORGE_GATEWAY_PUBLIC_URL", "").strip().rstrip("/")
        return f"{base}{path}" if base else path

    async def stage_inline(self, request: dict[str, Any]) -> dict[str, Any]:
        payload: list[tuple[str, bytes]] = []
        wp_types: dict[str, str] = {}
        for f in request.get("files") or []:
            name = str(f.get("name") or "")
            text, b64 = f.get("text"), f.get("content_base64")
            if (text is None) == (b64 is None):
                raise ValueError(f"{name}: send exactly one of text or content_base64")
            try:
                data = (
                    str(text).encode("utf-8")
                    if text is not None
                    else base64.b64decode(str(b64), validate=True)
                )
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"{name}: not valid base64") from exc
            payload.append((name, data))
            if f.get("wp_type"):
                wp_types[name] = str(f["wp_type"])
        if sum(len(d) for _, d in payload) > MAX_INLINE_UPLOAD_BYTES:
            raise ValueError(
                f"over the {MAX_INLINE_UPLOAD_BYTES}-byte inline limit; "
                "ask for an upload link instead"
            )
        return self._stage(payload, request, "inline", wp_types)

    def _stage(
        self,
        payload: list[tuple[str, bytes]],
        request: dict[str, Any],
        source: uploads.UploadSource,
        wp_types: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            upload = uploads.stage(
                get_upload_store(),
                payload,
                project_id=request.get("project_id"),
                source=source,
                requested_by=request.get("requested_by"),
                agent=request.get("agent"),
                description=str(request.get("description") or ""),
                wp_types=wp_types,
            )
        except uploads.UploadError as exc:
            raise _error(exc) from exc
        return upload.model_dump()

    async def request_upload(self, request: dict[str, Any]) -> dict[str, Any]:
        lifetime = links.lifetime_for(
            int(request.get("expected_bytes") or 0), request.get("lifetime_s")
        )
        try:
            upload = uploads.open_upload(
                get_upload_store(),
                project_id=request.get("project_id"),
                source="link",
                requested_by=request.get("requested_by"),
                agent=request.get("agent"),
                description=str(request.get("description") or ""),
                expires_in=timedelta(seconds=lifetime),
            )
        except uploads.UploadError as exc:
            raise _error(exc) from exc
        token, link = links.sign("upload", upload.id, lifetime_s=lifetime)
        return {
            "upload_id": upload.id,
            "approval_id": f"upload:{upload.id}",
            "path": f"/v1/links/{token}",
            "expires_at": link.expires_at,
            "lifetime_s": lifetime,
        }

    async def decide_upload(
        self, upload_id: str, decision: str, reason: str, agent: str, on_behalf_of: str
    ) -> dict[str, Any]:
        """Decide through the approvals service as the agent, for the person.

        The same call ``/v1/approvals`` makes, with surface ``agent``: it is
        refused (403) unless this process delegates human authority.
        """
        from fastapi import HTTPException

        from api_gateway.approvals import service as approvals
        from mcp_core.guardrails import Approver

        try:
            item = await approvals.decide(
                None,
                f"upload:{upload_id}",
                decision=decision,
                reason=reason,
                to_phase="",
                approver=Approver(actor_id=on_behalf_of, verified=False),
                surface="agent",
                on_behalf_of=on_behalf_of,
                agent=agent,
            )
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        return item.model_dump()

    async def upload_status(self, upload_id: str) -> dict[str, Any]:
        try:
            return get_upload_store().get(upload_id).model_dump()
        except KeyError as exc:
            raise ValueError(f"upload '{upload_id}' not found") from exc

    async def list_files(self, work_product_id: str) -> dict[str, Any]:
        try:
            wp = await service._work_product(_twin(), work_product_id)  # noqa: SLF001
        except service.FileError as exc:
            raise _error(exc) from exc
        return {"work_product_id": work_product_id, "files": service.file_names(wp)}

    async def read(self, work_product_id: str, path: str, max_bytes: int) -> dict[str, Any]:
        try:
            return await service.read(_twin(), work_product_id, path, max_bytes)
        except service.FileError as exc:
            raise _error(exc) from exc

    async def link(self, work_product_id: str, path: str, lifetime_s: int | None) -> dict[str, Any]:
        try:
            return await service.download_link(
                _twin(), work_product_id, path, lifetime_s=lifetime_s
            )
        except service.FileError as exc:
            raise _error(exc) from exc

    # ── local plugin only ────────────────────────────────────────────────

    def _inside_root(self, raw: str) -> Path:
        if self.local_root is None:
            raise ValueError("local paths are read only by the local plugin (metaforge-local)")
        path = Path(raw)
        resolved = (path if path.is_absolute() else self.local_root / path).resolve()
        if resolved != self.local_root and self.local_root not in resolved.parents:
            raise ValueError(f"'{raw}' is outside the project folder {self.local_root}")
        return resolved

    async def stage_local(self, request: dict[str, Any]) -> dict[str, Any]:
        """Stage files and folders from the project folder on this machine."""
        if self.local_root is None:
            raise ValueError("local paths are read only by the local plugin (metaforge-local)")
        payload: list[tuple[str, bytes]] = []
        for raw in request.get("paths") or []:
            target = self._inside_root(str(raw))
            if target.is_dir():
                for item in sorted(target.rglob("*")):
                    rel = item.relative_to(target)
                    if item.is_file() and not set(rel.parts) & _SKIP_DIRS:
                        payload.append((f"{target.name}/{rel.as_posix()}", item.read_bytes()))
            elif target.is_file():
                payload.append((target.name, target.read_bytes()))
            else:
                raise ValueError(f"'{raw}' does not exist")
        return self._stage(payload, request, "local")

    async def save_local(self, request: dict[str, Any]) -> dict[str, Any]:
        """Write a work product's file into the project folder on this machine."""
        try:
            content, filename, _wp = await service.resolve(
                _twin(), str(request.get("work_product_id") or ""), str(request.get("path") or "")
            )
        except service.FileError as exc:
            raise _error(exc) from exc
        dest = self._inside_root(str(request.get("destination") or filename.rsplit("/", 1)[-1]))
        if dest.is_dir():
            dest = dest / filename.rsplit("/", 1)[-1]
            dest = self._inside_root(str(dest))
        if dest.exists() and not request.get("overwrite"):
            raise ValueError(f"{dest} exists; pass overwrite to replace it")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return {"saved_to": str(dest), "size": len(content), "name": filename}


def make_files_service(*, local_root: Path | None = None) -> LocalFilesService:
    return LocalFilesService(local_root=local_root)
