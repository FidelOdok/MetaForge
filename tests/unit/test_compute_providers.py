"""Unit tests for ephemeral compute provider runtimes (MET-564)."""

from __future__ import annotations

import json

import httpx
import pytest

from tool_registry.compute_providers import (
    ComputeProviderProfile,
    RemoteVolumesUnsupportedError,
    RunPodRuntime,
    UnknownComputeProviderError,
    VastAIRuntime,
    available_providers,
    get_profile,
    resolve_runtime,
)
from tool_registry.container_runtime import ContainerConfig, DockerRuntime

# ======================================================================
# Registry
# ======================================================================


class TestRegistry:
    def test_available_providers(self):
        assert available_providers() == ["docker", "runpod", "vast_ai"]

    def test_get_profile_by_id(self):
        profile = get_profile("runpod")
        assert isinstance(profile, ComputeProviderProfile)
        assert profile.runtime_cls is RunPodRuntime
        assert profile.supports_gpu is True

    def test_get_profile_by_alias(self):
        assert get_profile("vastai").id == "vast_ai"
        assert get_profile("vast.ai").id == "vast_ai"
        assert get_profile("local").id == "docker"

    def test_unknown_provider_raises(self):
        with pytest.raises(UnknownComputeProviderError):
            get_profile("aws-batch")

    def test_resolve_runtime_defaults_to_docker(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_COMPUTE_PROVIDER", raising=False)
        runtime = resolve_runtime()
        assert isinstance(runtime, DockerRuntime)

    def test_resolve_runtime_from_env(self, monkeypatch):
        monkeypatch.setenv("METAFORGE_COMPUTE_PROVIDER", "runpod")
        runtime = resolve_runtime()
        assert isinstance(runtime, RunPodRuntime)

    def test_resolve_runtime_explicit_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("METAFORGE_COMPUTE_PROVIDER", "runpod")
        runtime = resolve_runtime("vast_ai")
        assert isinstance(runtime, VastAIRuntime)


# ======================================================================
# RunPodRuntime
# ======================================================================


def _runpod_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestRunPodRuntime:
    async def test_is_available_requires_api_key(self):
        assert await RunPodRuntime(api_key="").is_available() is False
        assert await RunPodRuntime(api_key="rp_test").is_available() is True

    async def test_run_completed(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path.endswith("/run"):
                assert request.headers["Authorization"] == "Bearer rp_test"
                body = json.loads(request.content)
                assert body["input"]["command"] == ["run_fea", "--mesh", "part.inp"]
                return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})
            return httpx.Response(
                200, json={"id": "job-1", "status": "COMPLETED", "output": {"ok": True}}
            )

        runtime = RunPodRuntime(api_key="rp_test", client=_runpod_client(handler))
        config = ContainerConfig(image="endpoint-abc123", timeout_seconds=30)

        result = await runtime.run(config, ["run_fea", "--mesh", "part.inp"])

        assert result.success is True
        assert result.exit_code == 0
        assert "ok" in result.stdout
        assert any(c.endswith("/endpoint-abc123/run") for c in calls)
        assert any("/status/job-1" in c for c in calls)

    async def test_run_failed_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/run"):
                return httpx.Response(200, json={"id": "job-2", "status": "IN_QUEUE"})
            return httpx.Response(
                200, json={"id": "job-2", "status": "FAILED", "error": "worker crashed"}
            )

        runtime = RunPodRuntime(api_key="rp_test", client=_runpod_client(handler))
        config = ContainerConfig(image="endpoint-abc123", timeout_seconds=30)

        result = await runtime.run(config, ["run_fea"])

        assert result.success is False
        assert result.exit_code == -1
        assert "worker crashed" in result.stderr

    async def test_run_http_error_returns_failed_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

        runtime = RunPodRuntime(api_key="rp_test", client=_runpod_client(handler))
        config = ContainerConfig(image="endpoint-abc123", timeout_seconds=30)

        result = await runtime.run(config, ["run_fea"])

        assert result.success is False
        assert result.exit_code == -1

    async def test_run_rejects_volumes(self):
        client = _runpod_client(lambda r: httpx.Response(200))
        runtime = RunPodRuntime(api_key="rp_test", client=client)
        config = ContainerConfig(
            image="endpoint-abc123", volumes={"/host/models": "/workspace/models"}
        )

        with pytest.raises(RemoteVolumesUnsupportedError):
            await runtime.run(config, ["run_fea"])

    async def test_cleanup_is_a_noop(self):
        client = _runpod_client(lambda r: httpx.Response(200))
        runtime = RunPodRuntime(api_key="rp_test", client=client)
        await runtime.cleanup("job-1")  # must not raise


# ======================================================================
# VastAIRuntime
# ======================================================================


def _vastai_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestVastAIRuntime:
    async def test_is_available_requires_api_key(self):
        assert await VastAIRuntime(api_key="").is_available() is False
        assert await VastAIRuntime(api_key="va_test").is_available() is True

    async def test_run_reaches_exited_state(self):
        state = {"polls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/bundles/"):
                return httpx.Response(
                    200,
                    json={"offers": [{"id": 42, "dph_total": 0.10}, {"id": 7, "dph_total": 0.05}]},
                )
            if path.endswith("/asks/7/"):
                assert request.method == "PUT"
                assert json.loads(request.content)["image"] == "metaforge/calculix:latest"
                return httpx.Response(200, json={"success": True, "new_contract": 999})
            if path.endswith("/instances/"):
                state["polls"] += 1
                status = "loading" if state["polls"] < 2 else "exited"
                return httpx.Response(
                    200, json={"instances": [{"id": 999, "actual_status": status}]}
                )
            if path.endswith("/instances/999"):
                return httpx.Response(200, json={"success": True})
            raise AssertionError(f"unexpected request: {request.method} {path}")

        runtime = VastAIRuntime(api_key="va_test", client=_vastai_client(handler))
        config = ContainerConfig(image="metaforge/calculix", timeout_seconds=30)

        result = await runtime.run(config, ["run_fea"])

        assert result.success is True
        assert result.exit_code == 0

    async def test_run_offline_state_is_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/bundles/"):
                return httpx.Response(200, json={"offers": [{"id": 1, "dph_total": 0.05}]})
            if path.endswith("/asks/1/"):
                return httpx.Response(200, json={"success": True, "new_contract": 500})
            if path.endswith("/instances/"):
                return httpx.Response(
                    200, json={"instances": [{"id": 500, "actual_status": "offline"}]}
                )
            if path.endswith("/instances/500"):
                return httpx.Response(200, json={"success": True})
            raise AssertionError(f"unexpected request: {request.method} {path}")

        runtime = VastAIRuntime(api_key="va_test", client=_vastai_client(handler))
        config = ContainerConfig(image="metaforge/calculix", timeout_seconds=30)

        result = await runtime.run(config, ["run_fea"])

        assert result.success is False
        assert "offline" in result.stderr

    async def test_run_no_offers_returns_failed_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/bundles/"):
                return httpx.Response(200, json={"offers": []})
            raise AssertionError("should not reach instance creation")

        runtime = VastAIRuntime(api_key="va_test", client=_vastai_client(handler))
        config = ContainerConfig(image="metaforge/calculix", timeout_seconds=30)

        result = await runtime.run(config, ["run_fea"])

        assert result.success is False
        assert "No Vast.ai offers" in result.stderr

    async def test_run_rejects_volumes(self):
        client = _vastai_client(lambda r: httpx.Response(200))
        runtime = VastAIRuntime(api_key="va_test", client=client)
        config = ContainerConfig(
            image="metaforge/calculix", volumes={"/host/models": "/workspace/models"}
        )

        with pytest.raises(RemoteVolumesUnsupportedError):
            await runtime.run(config, ["run_fea"])

    async def test_cleanup_swallows_errors(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"success": False, "msg": "not found"})

        runtime = VastAIRuntime(api_key="va_test", client=_vastai_client(handler))
        await runtime.cleanup("999")  # must not raise
