"""Dispatching a print and recording its result (FORGE-451 step 4, FORGE-427/440)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from api_gateway.library import routes as library_routes
from api_gateway.library.connectors import ConnectorError, MoonrakerConnector, connector_for
from api_gateway.library.jobs import JobError, dispatch_job, job_status, record_job_result
from api_gateway.twin import routes as twin_routes
from mcp_core.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from twin_core.api import InMemoryTwinAPI
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


class FakeOctoPrint:
    """Enough of OctoPrint to print one file: idle, upload+print, then done."""

    def __init__(self) -> None:
        self.state = "Operational"
        self.flags = {"operational": True, "printing": False}
        self.file: str | None = None
        self.completion: float | None = None
        self.uploads: list[tuple[str, bytes]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/printer":
            return httpx.Response(200, json={"state": {"flags": self.flags}})
        if path == "/api/files/local" and request.method == "POST":
            body = request.content
            name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
            self.uploads.append((name, body))
            self.file, self.completion, self.state = name, 0.0, "Printing"
            self.flags = {"operational": True, "printing": True}
            return httpx.Response(201, json={"files": {"local": {"name": name, "path": name}}})
        if path == "/api/job":
            return httpx.Response(
                200,
                json={
                    "state": self.state,
                    "job": {"file": {"name": self.file}},
                    "progress": {"completion": self.completion},
                },
            )
        return httpx.Response(404, json={})

    def finish(self, completion: float = 100.0) -> None:
        self.state, self.completion = "Operational", completion
        self.flags = {"operational": True, "printing": False}


@pytest.fixture
def library() -> Any:
    lib = SqliteMachineLibrary()
    library_routes.init_machine_library(lib)
    yield lib
    library_routes.init_machine_library(None)


@pytest.fixture
def twin() -> Any:
    previous = twin_routes.get_twin()
    t = InMemoryTwinAPI.create()
    twin_routes.init_twin(t)
    yield t
    twin_routes.init_twin(previous)


def _printer(lib: SqliteMachineLibrary, **extra: Any) -> str:
    data = {
        "layer": "instance",
        "kind": "fdm_printer",
        "name": "Workshop CR-10",
        "processes": ["fdm"],
        "route": "in_house",
        "status": "available",
        "connection": {"protocol": "octoprint", "url": "http://cr10.local"},
        **extra,
    }
    return lib.register(data).id


async def _gcode(twin: Any, tmp_path: Path, name: str = "bracket.gcode") -> str:
    path = tmp_path / name
    path.write_bytes(b"G28\nG1 X10 Y10\n")
    wp = await twin.create_work_product(
        WorkProduct(
            name=name.rsplit(".", 1)[0],
            type=WorkProductType.MANUFACTURING_FILE,
            domain="mechanical",
            file_path=str(path),
            content_hash="h",
            format=name.rsplit(".", 1)[1],
            created_by="user",
            metadata={"original_filename": name},
        )
    )
    return str(wp.id)


def test_annotations_hold_the_print_and_the_record() -> None:
    assert "library.dispatch_job" in DESTRUCTIVE
    assert "library.dispatch_job" not in READ_ONLY
    assert "library.job_status" in READ_ONLY
    assert "library.record_job_result" in ADDITIVE


@pytest.mark.asyncio
async def test_a_print_is_sent_watched_and_recorded_against_its_revision(
    library: SqliteMachineLibrary, twin: Any, tmp_path: Path
) -> None:
    printer = FakeOctoPrint()
    machine = _printer(library)
    wp_id = await _gcode(twin, tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(printer.handler)) as http:
        sent = await dispatch_job(machine, wp_id, by="person", client=http)
        job = sent["job"]
        assert printer.uploads[0][0] == "bracket.gcode"
        assert b"G28" in printer.uploads[0][1]
        assert job["part_revision"] == 0
        assert job["state"] == "printing"

        running = await job_status(job["id"], client=http)
        assert running["job_state"] == "printing" and running["ended"] is False
        with pytest.raises(JobError, match="has not ended"):
            await record_job_result(job["id"], client=http)

        printer.finish()
        done = await job_status(job["id"], client=http)
        assert done["job_state"] == "complete"

        recorded = await record_job_result(job["id"], by="person", client=http)
    evidence_id = recorded["evidence"]["node_id"]
    assert recorded["job"]["evidence_id"] == evidence_id
    assert recorded["evidence"]["valid_against_count"] == 1
    entity = await twin.get_engineering_entity(UUID(evidence_id))
    assert entity.metadata["evidence_type"] == "demonstration"
    assert entity.metadata["result"]["outcome"] == "complete"
    # Recording twice does not make a second piece of evidence.
    again = await record_job_result(job["id"])
    assert again["already_recorded"] is True


@pytest.mark.asyncio
async def test_a_stopped_print_is_recorded_as_not_made(
    library: SqliteMachineLibrary, twin: Any, tmp_path: Path
) -> None:
    printer = FakeOctoPrint()
    machine = _printer(library)
    wp_id = await _gcode(twin, tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(printer.handler)) as http:
        job = (await dispatch_job(machine, wp_id, client=http))["job"]
        printer.finish(completion=37.0)
        recorded = await record_job_result(job["id"], client=http)
    entity = await twin.get_engineering_entity(UUID(recorded["evidence"]["node_id"]))
    assert entity.metadata["result"]["outcome"] == "cancelled"
    assert "not made" in str(entity.model_dump())


@pytest.mark.asyncio
async def test_nothing_is_sent_to_a_busy_vendor_or_unavailable_machine(
    library: SqliteMachineLibrary, twin: Any, tmp_path: Path
) -> None:
    printer = FakeOctoPrint()
    printer.flags = {"operational": True, "printing": True}
    wp_id = await _gcode(twin, tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(printer.handler)) as http:
        with pytest.raises(JobError, match="not idle"):
            await dispatch_job(_printer(library), wp_id, client=http)
        with pytest.raises(JobError, match="vendor"):
            await dispatch_job(_printer(library, route="vendor"), wp_id, client=http)
        with pytest.raises(JobError, match="offline in the library"):
            await dispatch_job(_printer(library, status="offline"), wp_id, client=http)
    assert printer.uploads == []


@pytest.mark.asyncio
async def test_only_gcode_is_sent(library: SqliteMachineLibrary, twin: Any, tmp_path: Path) -> None:
    step = await _gcode(twin, tmp_path, name="bracket.step")
    with pytest.raises(JobError, match="no slicer"):
        await dispatch_job(_printer(library), step)
    with pytest.raises(JobError, match="not found"):
        await dispatch_job(_printer(library), str(uuid4()))


def test_only_octoprint_and_moonraker_take_jobs() -> None:
    with pytest.raises(ConnectorError) as exc:
        connector_for({"protocol": "prusalink", "url": "http://mk4.local"})
    assert exc.value.kind == "config"


@pytest.mark.asyncio
async def test_moonraker_uploads_with_print_and_reads_print_stats() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/server/files/upload":
            return httpx.Response(
                201, json={"result": {"item": {"path": "part.gcode"}, "print_started": True}}
            )
        if request.url.path == "/printer/objects/query":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "status": {
                            "print_stats": {"state": "complete", "filename": "part.gcode"},
                            "virtual_sdcard": {"progress": 1.0},
                        }
                    }
                },
            )
        return httpx.Response(200, json={"result": {"state": "ready"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        conn = MoonrakerConnector({"url": "http://voron.local"}, http)
        started = await conn.start(b"G28\n", "part.gcode")
        status = await conn.status()
        health = await conn.health()
    assert started["remote_path"] == "part.gcode"
    assert b'name="print"' in seen[0].content
    assert status == {
        "state": "complete",
        "filename": "part.gcode",
        "progress": 1.0,
        "raw_state": "complete",
    }
    assert health["state"] == "idle"


@pytest.mark.asyncio
async def test_a_refused_key_is_an_auth_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={}))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ConnectorError) as exc:
            await connector_for({"protocol": "octoprint", "url": "http://x"}, http).health()
    assert exc.value.kind == "auth"
    assert json.dumps({"kind": exc.value.kind}) == '{"kind": "auth"}'
