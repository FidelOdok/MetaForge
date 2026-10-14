"""The in-process ``library.*`` binding (FORGE-451).

Same shapes as the ``/v1/library/machines`` routes, so a tool answers the same
whether the gateway hosts it or the sidecar reaches it over HTTP
(``metaforge.mcp.remote_library``).
"""

from __future__ import annotations

from typing import Any

from api_gateway.library.routes import get_machine_library
from twin_core.library.machines import MachineLibraryError, MachineNotFoundError


class LocalMachineLibrary:
    """``library.*`` against this process's store."""

    async def list_machines(self, layer: str | None, kind: str | None) -> dict[str, Any]:
        machines = get_machine_library().list(layer=layer, kind=kind)
        return {"machines": [m.as_dict() for m in machines]}

    async def get_machine(self, machine_id: str) -> dict[str, Any]:
        library = get_machine_library()
        try:
            machine = library.get(machine_id)
        except MachineNotFoundError as exc:
            raise ValueError(f"machine '{machine_id}' not found") from exc
        return {"machine": machine.as_dict(), "history": library.history(machine_id)}

    async def register_machine(self, data: dict[str, Any], by: str) -> dict[str, Any]:
        return {"machine": get_machine_library().register(data, by=by).as_dict()}

    async def update_machine(
        self, machine_id: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]:
        try:
            machine = get_machine_library().update(machine_id, changes, by=by)
        except MachineNotFoundError as exc:
            raise ValueError(f"machine '{machine_id}' not found") from exc
        return {"machine": machine.as_dict()}

    async def machine_limits(self, machine_id: str) -> dict[str, Any]:
        try:
            return get_machine_library().limits(machine_id)
        except MachineNotFoundError as exc:
            raise ValueError(f"machine '{machine_id}' not found") from exc

    async def discover_machine(self, connection: dict[str, Any], name: str) -> dict[str, Any]:
        from api_gateway.library.discovery import DiscoveryError, discover_machine

        try:
            return await discover_machine(connection, name=name)
        except DiscoveryError as exc:
            raise ValueError(str(exc)) from exc

    async def dispatch_job(self, machine_id: str, work_product_id: str, by: str) -> dict[str, Any]:
        from api_gateway.library.jobs import JobError, dispatch_job

        try:
            return await dispatch_job(machine_id, work_product_id, by=by)
        except JobError as exc:
            raise ValueError(str(exc)) from exc

    async def job_status(self, job_id: str) -> dict[str, Any]:
        from api_gateway.library.jobs import JobError, job_status

        try:
            return await job_status(job_id)
        except JobError as exc:
            raise ValueError(str(exc)) from exc

    async def record_job_result(self, job_id: str, by: str) -> dict[str, Any]:
        from api_gateway.library.jobs import JobError, record_job_result

        try:
            return await record_job_result(job_id, by=by)
        except JobError as exc:
            raise ValueError(str(exc)) from exc

    # FORGE-451 / FORGE-446: processes and materials.

    async def list_entries(self, kind: str, family: str | None) -> dict[str, Any]:
        from api_gateway.library.catalog_routes import get_catalog

        return {"entries": [e.as_dict() for e in get_catalog().list(kind, family=family)]}

    async def get_entry(self, kind: str, ref: str) -> dict[str, Any]:
        from api_gateway.library.catalog_routes import get_catalog
        from twin_core.library.catalog import CatalogNotFoundError

        catalog = get_catalog()
        try:
            entry = catalog.get(ref, kind)
        except CatalogNotFoundError as exc:
            raise ValueError(f"no {kind} '{ref}'") from exc
        return {"entry": entry.as_dict(), "history": catalog.history(entry.id)}

    async def register_entry(self, kind: str, data: dict[str, Any], by: str) -> dict[str, Any]:
        from api_gateway.library.catalog_routes import get_catalog

        return {"entry": get_catalog().register(kind, data, by=by).as_dict()}

    async def update_entry(
        self, kind: str, ref: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]:
        from api_gateway.library.catalog_routes import get_catalog
        from twin_core.library.catalog import CatalogNotFoundError

        catalog = get_catalog()
        try:
            current = catalog.get(ref, kind)
        except CatalogNotFoundError as exc:
            raise ValueError(f"no {kind} '{ref}'") from exc
        return {"entry": catalog.update(current.id, changes, by=by).as_dict()}

    async def process_limits(self, ref: str, material: str | None) -> dict[str, Any]:
        from api_gateway.library.catalog_routes import get_catalog
        from twin_core.library.catalog import CatalogNotFoundError

        try:
            return get_catalog().process_limits(ref, material)
        except CatalogNotFoundError as exc:
            raise ValueError(f"no entry '{exc.args[0]}'") from exc


def make_machine_library() -> LocalMachineLibrary:
    return LocalMachineLibrary()


__all__ = ["LocalMachineLibrary", "MachineLibraryError", "make_machine_library"]
