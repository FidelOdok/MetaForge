"""Tests for the manufacture-release gateway route (FORGE-294).

Follows the same lightweight-FastAPI-app + fake-callable pattern
test_robot_loads_routes.py established, adapted for
api_gateway/manufacture/routes.py's injected-closure seam (init_manufacture_
release), the same shape api_gateway/dfm/routes.py uses.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.manufacture.routes import init_manufacture_release, router


class _FakeReleaser:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def __call__(self, *, work_product_id: str, process: str) -> dict[str, Any]:
        self.calls.append({"work_product_id": work_product_id, "process": process})
        if self.fail is not None:
            raise self.fail
        fmt = "stl" if process == "3d_print" else "step"
        return {
            "work_product_id": work_product_id,
            "process": process,
            "format": fmt,
            "filename": f"release.{fmt}",
            "file_size_bytes": 42,
            "content_base64": "c29tZSBieXRlcw==",
        }


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestManufactureReleaseRoute:
    def test_success_3d_print(self, client: TestClient) -> None:
        releaser = _FakeReleaser()
        init_manufacture_release(releaser)

        response = client.get(
            "/v1/manufacture/release",
            params={"work_product_id": "wp-1", "process": "3d_print"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "stl"
        assert data["filename"] == "release.stl"
        assert data["content_base64"] == "c29tZSBieXRlcw=="
        assert releaser.calls == [{"work_product_id": "wp-1", "process": "3d_print"}]

    def test_success_cnc(self, client: TestClient) -> None:
        releaser = _FakeReleaser()
        init_manufacture_release(releaser)

        response = client.get(
            "/v1/manufacture/release",
            params={"work_product_id": "wp-1", "process": "cnc"},
        )

        assert response.status_code == 200
        assert response.json()["format"] == "step"

    def test_value_error_returns_400(self, client: TestClient) -> None:
        init_manufacture_release(_FakeReleaser(fail=ValueError("bad input")))

        response = client.get(
            "/v1/manufacture/release",
            params={"work_product_id": "wp-1", "process": "3d_print"},
        )

        assert response.status_code == 400
        assert "bad input" in response.json()["detail"]

    def test_unexpected_failure_returns_502(self, client: TestClient) -> None:
        init_manufacture_release(_FakeReleaser(fail=RuntimeError("boom")))

        response = client.get(
            "/v1/manufacture/release",
            params={"work_product_id": "wp-1", "process": "3d_print"},
        )

        assert response.status_code == 502

    def test_not_configured_returns_503(self, client: TestClient) -> None:
        import api_gateway.manufacture.routes as routes_module

        routes_module._release = None  # noqa: SLF001 -- reset the module-level seam

        response = client.get(
            "/v1/manufacture/release",
            params={"work_product_id": "wp-1", "process": "3d_print"},
        )

        assert response.status_code == 503

    def test_missing_query_params_returns_422(self, client: TestClient) -> None:
        init_manufacture_release(_FakeReleaser())
        response = client.get("/v1/manufacture/release")
        assert response.status_code == 422
