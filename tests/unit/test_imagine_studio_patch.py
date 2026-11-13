"""Writing the studio as a change, not the whole config (MetaForge, 2026-11-11).

Every studio write used to resend the whole config, 6.5k to 8k output tokens even to move
one part, and a long one was cut off at the output cap. ``twin.imagine_patch_studio`` takes
the revision the changes were made against and a list of changes (JSON-Patch style:
``add``, ``replace``, ``remove``, with a list item addressed by its ``id``). The changed
config goes through the same checks as a full write and becomes the next revision.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest

from api_gateway.imagine import routes as imagine_routes
from api_gateway.imagine.service import ImagineError, ImagineService
from api_gateway.imagine.studio_patch import PatchError, apply_changes
from api_gateway.operations import publisher as ops
from tests.unit.imagine_fixtures import full_config
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"


def _cfg() -> dict[str, Any]:
    return {
        "form": {
            "params": {"case_d": 40, "wall_t": 2},
            "authored": {
                "parts": [
                    {"id": "case", "name": "Case", "code": "return [];"},
                    {"id": "screen", "name": "Screen", "code": "return [];"},
                ],
                "behaviours": [{"id": "glow", "name": "Glow"}],
            },
        },
        "product": {"name": "Watch"},
        "a/b": {"c~d": 1},
    }


# -- the changes on their own ------------------------------------------------------------


def test_replace_sets_a_value_and_leaves_the_input_alone() -> None:
    before = _cfg()
    out, applied = apply_changes(
        before, [{"op": "replace", "path": "/form/params/case_d", "value": 44}]
    )
    assert out["form"]["params"]["case_d"] == 44
    assert before["form"]["params"]["case_d"] == 40
    assert applied == ["replace /form/params/case_d"]


def test_a_list_item_is_addressed_by_its_id() -> None:
    code = "return [g.cylinder(19.25, 5, 64)];"
    out, _ = apply_changes(
        _cfg(), [{"op": "replace", "path": "/form/authored/parts/screen/code", "value": code}]
    )
    assert out["form"]["authored"]["parts"][1]["code"] == code


def test_a_list_item_is_also_addressed_by_its_index() -> None:
    out, _ = apply_changes(
        _cfg(), [{"op": "replace", "path": "/form/authored/parts/0/name", "value": "Round case"}]
    )
    assert out["form"]["authored"]["parts"][0]["name"] == "Round case"


def test_add_appends_to_a_list_with_a_dash_and_sets_a_new_key() -> None:
    part = {"id": "tab", "name": "Tab", "code": "return [];"}
    out, _ = apply_changes(
        _cfg(),
        [
            {"op": "add", "path": "/form/authored/parts/-", "value": part},
            {"op": "add", "path": "/form/authored/joins", "value": []},
            {"op": "add", "path": "/form/params/btn_y", "value": 6},
        ],
    )
    assert [p["id"] for p in out["form"]["authored"]["parts"]] == ["case", "screen", "tab"]
    assert out["form"]["authored"]["joins"] == [] and out["form"]["params"]["btn_y"] == 6


def test_remove_takes_a_list_item_by_id_and_a_key() -> None:
    out, _ = apply_changes(
        _cfg(),
        [
            {"op": "remove", "path": "/form/authored/behaviours/glow"},
            {"op": "remove", "path": "/form/params/wall_t"},
        ],
    )
    assert out["form"]["authored"]["behaviours"] == []
    assert "wall_t" not in out["form"]["params"]


def test_json_pointer_escapes_are_honoured() -> None:
    out, _ = apply_changes(_cfg(), [{"op": "replace", "path": "/a~1b/c~0d", "value": 2}])
    assert out["a/b"]["c~d"] == 2


def test_the_changes_apply_in_order() -> None:
    out, _ = apply_changes(
        _cfg(),
        [
            {"op": "add", "path": "/form/params/x", "value": 1},
            {"op": "replace", "path": "/form/params/x", "value": 2},
        ],
    )
    assert out["form"]["params"]["x"] == 2


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"op": "replace", "path": "/form/params/nope", "value": 1}, "nope"),
        ({"op": "replace", "path": "/form/authored/parts/wing/code", "value": ""}, "wing"),
        ({"op": "remove", "path": "/form/authored/parts/9"}, "9"),
        ({"op": "move", "path": "/form/params/case_d"}, "op"),
        ({"op": "replace", "path": "form/params/case_d", "value": 1}, "/"),
        ({"op": "replace", "path": "/form/params/case_d"}, "value"),
        ({"op": "replace", "path": "", "value": {}}, "whole"),
        ({"op": "add", "path": "/product/name/x", "value": 1}, "not an object or a list"),
    ],
)
def test_a_bad_change_is_refused_naming_it(change: dict[str, Any], word: str) -> None:
    with pytest.raises(PatchError) as err:
        apply_changes(_cfg(), [{"op": "add", "path": "/form/params/ok", "value": 1}, change])
    assert "change 2" in str(err.value) and word in str(err.value), str(err.value)


def test_no_changes_is_refused() -> None:
    with pytest.raises(PatchError, match="at least one"):
        apply_changes(_cfg(), [])


# -- through the service -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _publisher() -> Iterator[None]:
    ops.init_operations(None)
    yield
    ops.init_operations(None)


@pytest.fixture
def service() -> Iterator[ImagineService]:
    svc = ImagineService(InMemoryTwinAPI.create())
    imagine_routes.set_service(svc)
    yield svc
    imagine_routes.set_service(None)


async def test_a_patch_is_the_next_revision_with_only_that_change(service: ImagineService) -> None:
    first = await service.put_studio(PROJECT, full_config(), actor="test")
    assert first.config is not None
    out = await service.patch_studio(
        PROJECT,
        first.revision,
        [{"op": "replace", "path": "/form/params/arm_len", "value": 300}],
        actor="test",
    )
    assert out.revision == first.revision + 1
    assert out.config is not None and out.config["form"]["params"]["arm_len"] == 300
    rest = copy.deepcopy(out.config)
    rest["form"]["params"]["arm_len"] = first.config["form"]["params"]["arm_len"]
    assert rest == first.config


async def test_a_patch_against_an_old_revision_is_refused(service: ImagineService) -> None:
    first = await service.put_studio(PROJECT, full_config(), actor="test")
    await service.put_studio(PROJECT, full_config(), actor="test")
    with pytest.raises(ImagineError) as err:
        await service.patch_studio(
            PROJECT,
            first.revision,
            [{"op": "replace", "path": "/form/params/arm_len", "value": 300}],
            actor="test",
        )
    assert err.value.status == 409 and "revision 2" in err.value.message


async def test_a_patch_that_breaks_the_checks_is_refused_and_writes_nothing(
    service: ImagineService,
) -> None:
    first = await service.put_studio(PROJECT, full_config(), actor="test")
    with pytest.raises(ImagineError) as err:
        await service.patch_studio(
            PROJECT,
            first.revision,
            [{"op": "replace", "path": "/process/material", "value": "unobtainium"}],
            actor="test",
        )
    assert err.value.status == 422 and "unobtainium" in str(err.value.detail)
    assert (await service.get_studio(PROJECT)).revision == first.revision


async def test_a_bad_change_is_a_400_and_writes_nothing(service: ImagineService) -> None:
    first = await service.put_studio(PROJECT, full_config(), actor="test")
    with pytest.raises(ImagineError) as err:
        await service.patch_studio(
            PROJECT,
            first.revision,
            [{"op": "replace", "path": "/form/params/nope", "value": 1}],
            actor="test",
        )
    assert err.value.status == 400 and "nope" in err.value.message
    assert (await service.get_studio(PROJECT)).revision == first.revision


async def test_there_is_nothing_to_patch_before_the_first_write(service: ImagineService) -> None:
    with pytest.raises(ImagineError) as err:
        await service.patch_studio(
            PROJECT, 0, [{"op": "add", "path": "/x", "value": 1}], actor="test"
        )
    assert err.value.status == 404 and "imagine_put_studio" in err.value.message


# -- over MCP ----------------------------------------------------------------------------


async def test_an_agent_patches_over_mcp_and_gets_a_short_answer(service: ImagineService) -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    srv = TwinServer(twin=service.twin, imagine_studio=service)
    assert "twin.imagine_patch_studio" in srv.tool_ids
    put = await srv.imagine_put_studio({"project_id": PROJECT, "config": full_config()})
    out = await srv.imagine_patch_studio(
        {
            "project_id": PROJECT,
            "base_revision": put["revision"],
            "changes": [{"op": "replace", "path": "/form/params/arm_len", "value": 300}],
        }
    )
    assert out["revision"] == put["revision"] + 1 and out["config_ref"].endswith(
        f"@{out['revision']}"
    )
    assert out["applied"] == ["replace /form/params/arm_len"]
    assert "config" not in out and "collections" not in out
    with pytest.raises(ValueError, match="nope"):
        await srv.imagine_patch_studio(
            {
                "project_id": PROJECT,
                "base_revision": out["revision"],
                "changes": [{"op": "replace", "path": "/form/params/nope", "value": 1}],
            }
        )
    with pytest.raises(ValueError, match="base_revision"):
        await srv.imagine_patch_studio(
            {"project_id": PROJECT, "changes": [{"op": "add", "path": "/x", "value": 1}]}
        )


def test_the_patch_is_imagine_work_and_additive() -> None:
    from mcp_core.annotations import ADDITIVE
    from mcp_core.profiles import CREATE_STAGE_TOOLS

    assert "twin.imagine_patch_studio" in ADDITIVE
    assert "twin.imagine_patch_studio" in CREATE_STAGE_TOOLS["imagine"]
    for stage in ("develop", "check", "make"):
        assert "twin.imagine_patch_studio" not in CREATE_STAGE_TOOLS[stage]


# -- over REST: PATCH .../imagine/studio --------------------------------------------------


@pytest.fixture
async def client(service: ImagineService) -> Any:
    import httpx
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(imagine_routes.router)
    original = imagine_routes.get_service
    imagine_routes.get_service = lambda: service  # type: ignore[assignment]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c
    imagine_routes.get_service = original  # type: ignore[assignment]


URL = f"/v1/twin/projects/{PROJECT}/imagine/studio"


async def test_the_patch_route_writes_the_next_revision(
    client: Any, service: ImagineService
) -> None:
    first = await service.put_studio(PROJECT, full_config(), actor="test")
    r = await client.patch(
        URL,
        json={
            "base_revision": first.revision,
            "changes": [{"op": "replace", "path": "/form/params/arm_len", "value": 300}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == first.revision + 1
    assert body["config"]["form"]["params"]["arm_len"] == 300


@pytest.mark.parametrize(
    ("base", "change", "status"),
    [
        (2, {"op": "replace", "path": "/form/params/arm_len", "value": 300}, 409),
        (1, {"op": "replace", "path": "/form/params/nope", "value": 1}, 400),
        (1, {"op": "replace", "path": "/process/material", "value": "unobtainium"}, 422),
    ],
)
async def test_the_patch_route_refuses_with_the_reason(
    client: Any, service: ImagineService, base: int, change: dict[str, Any], status: int
) -> None:
    await service.put_studio(PROJECT, full_config(), actor="test")
    r = await client.patch(URL, json={"base_revision": base, "changes": [change]})
    assert r.status_code == status, r.text
    assert (await service.get_studio(PROJECT)).revision == 1


async def test_the_patch_route_takes_only_its_two_fields(
    client: Any, service: ImagineService
) -> None:
    await service.put_studio(PROJECT, full_config(), actor="test")
    r = await client.patch(URL, json={"base_revision": 1, "changes": [], "config": {}})
    assert r.status_code == 422
