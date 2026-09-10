"""Ephemeral cloud compute provider runtimes (MET-564).

Extends the ``ContainerRuntime`` abstraction (``tool_registry.container_runtime``)
so tool execution can burst onto ephemeral cloud compute instead of only local
Docker. Each provider is a ``ContainerRuntime`` subclass; ``resolve_runtime()``
selects one by provider id (env ``METAFORGE_COMPUTE_PROVIDER``, default
``"docker"`` -- no behavior change for existing deployments).

Scope of this first slice (see MET-564 for what's deliberately deferred to
follow-up issues -- AWS Batch / GCP / Azure / CoreWeave / Lambda / Paperspace
adapters, a GPU field on ``ContainerConfig``, and blob-store-backed volume
I/O):

- ``RunPodRuntime`` targets RunPod Serverless
  (https://docs.runpod.io/serverless/endpoints/send-requests), a
  request/response job model (``POST /run`` then poll ``GET /status/{id}``)
  that maps directly onto ``ContainerRuntime.run()``. A Serverless endpoint
  is pre-built around one worker image, so ``ContainerConfig.image`` is
  interpreted as the RunPod *endpoint id* when this runtime is selected --
  not a Docker image tag the way ``DockerRuntime`` uses it.
- ``VastAIRuntime`` targets Vast.ai's instance-rental API
  (https://docs.vast.ai/api-reference/instances/create-instance). Vast.ai has
  no request/response job primitive: it rents a VM running
  ``config.full_image`` with ``command`` passed as an onstart script. The
  REST API does not expose the instance's process exit code, so ``success``
  is inferred from instance lifecycle state (``actual_status``) rather than a
  verified exit code -- treat ``VastAIRuntime.run()`` results as best-effort
  until a confirmed logs/exit-code endpoint is wired in.
- Neither runtime supports ``ContainerConfig.volumes`` (no host filesystem to
  bind-mount into a remote provider); ``run()`` raises
  ``RemoteVolumesUnsupportedError`` if volumes are set. Real input/output
  data movement needs a blob-store hop (tracked in MET-489) and is a
  deliberate follow-up, not invented here.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.container_runtime import ContainerConfig, ContainerRuntime, ExecutionResult

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.compute_providers")

_POLL_INTERVAL_SECONDS = 2.0
_REQUEST_TIMEOUT_SECONDS = 30.0


class RemoteVolumesUnsupportedError(NotImplementedError):
    """Raised when a remote ContainerRuntime is asked to bind-mount host volumes."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"{provider} does not support ContainerConfig.volumes (no host filesystem "
            "to bind-mount into a remote provider). Blob-store-backed I/O is tracked "
            "in MET-489."
        )


class RunPodRuntime(ContainerRuntime):
    """RunPod Serverless-backed ``ContainerRuntime``.

    ``ContainerConfig.image`` is interpreted as the RunPod *endpoint id* --
    see the module docstring for why a Serverless endpoint can't take an
    arbitrary Docker image per request.
    """

    _BASE_URL = "https://api.runpod.ai/v2"
    _TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("RUNPOD_API_KEY", "")
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def is_available(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def run(
        self,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        if config.volumes:
            raise RemoteVolumesUnsupportedError("RunPod")

        endpoint_id = config.image
        payload = {"input": {"command": command, **(input_data or {})}}

        with tracer.start_as_current_span("runpod.run") as span:
            span.set_attribute("runpod.endpoint_id", endpoint_id)
            start = time.monotonic()
            try:
                submit = await self._client.post(
                    f"{self._BASE_URL}/{endpoint_id}/run",
                    headers=self._headers(),
                    json=payload,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                submit.raise_for_status()
                job_id = submit.json()["id"]
                span.set_attribute("runpod.job_id", job_id)

                deadline = start + config.timeout_seconds
                status_payload: dict[str, Any] = {}
                status = None
                while time.monotonic() < deadline:
                    poll = await self._client.get(
                        f"{self._BASE_URL}/{endpoint_id}/status/{job_id}",
                        headers=self._headers(),
                        timeout=_REQUEST_TIMEOUT_SECONDS,
                    )
                    poll.raise_for_status()
                    status_payload = poll.json()
                    status = status_payload.get("status")
                    if status in self._TERMINAL_STATUSES:
                        break
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)

                elapsed = time.monotonic() - start
                success = status == "COMPLETED"
                span.set_attribute("runpod.status", status or "CLIENT_TIMEOUT")

                logger.info(
                    "runpod_job_complete",
                    endpoint_id=endpoint_id,
                    job_id=job_id,
                    status=status,
                    duration_s=round(elapsed, 3),
                )

                return ExecutionResult(
                    success=success,
                    exit_code=0 if success else -1,
                    stdout=str(status_payload.get("output", "")) if success else "",
                    stderr=(
                        ""
                        if success
                        else str(
                            status_payload.get("error", f"job status: {status or 'CLIENT_TIMEOUT'}")
                        )
                    ),
                    duration_seconds=round(elapsed, 3),
                )
            except Exception as exc:
                elapsed = time.monotonic() - start
                span.record_exception(exc)
                logger.error("runpod_run_failed", endpoint_id=endpoint_id, error=str(exc))
                return ExecutionResult(
                    success=False,
                    exit_code=-1,
                    stderr=str(exc),
                    duration_seconds=round(elapsed, 3),
                )

    async def cleanup(self, container_id: str) -> None:
        # Serverless jobs are billed per-execution and torn down server-side
        # by RunPod; there is nothing for the client to release.
        return None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class VastAIRuntime(ContainerRuntime):
    """Vast.ai instance-rental-backed ``ContainerRuntime``.

    See the module docstring: exit codes are not exposed over Vast.ai's REST
    API in this integration, so ``success`` is inferred from instance
    lifecycle state rather than a verified exit code.
    """

    _BASE_URL = "https://console.vast.ai/api/v0"
    _DEAD_STATUSES = ("exited", "offline", "unknown")

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("VAST_API_KEY", "")
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def is_available(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _cheapest_offer_id(self) -> int:
        resp = await self._client.get(
            f"{self._BASE_URL}/bundles/",
            headers=self._headers(),
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        offers = resp.json().get("offers", [])
        if not offers:
            raise RuntimeError("No Vast.ai offers available")
        cheapest = min(offers, key=lambda o: o.get("dph_total", float("inf")))
        return int(cheapest["id"])

    async def run(
        self,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        if config.volumes:
            raise RemoteVolumesUnsupportedError("Vast.ai")

        start = time.monotonic()
        instance_id: int | None = None
        onstart = " ".join(shlex.quote(part) for part in command)
        env = " ".join(f"-e {key}={shlex.quote(value)}" for key, value in config.env.items())

        with tracer.start_as_current_span("vastai.run") as span:
            try:
                ask_id = await self._cheapest_offer_id()
                create = await self._client.put(
                    f"{self._BASE_URL}/asks/{ask_id}/",
                    headers=self._headers(),
                    json={"image": config.full_image, "onstart": onstart, "env": env},
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                create.raise_for_status()
                body = create.json()
                if not body.get("success"):
                    raise RuntimeError(f"Vast.ai instance creation failed: {body}")
                instance_id = int(body["new_contract"])
                span.set_attribute("vastai.instance_id", instance_id)

                deadline = start + config.timeout_seconds
                final_status = "unknown"
                while time.monotonic() < deadline:
                    listing = await self._client.get(
                        f"{self._BASE_URL}/instances/",
                        headers=self._headers(),
                        params={"owner": "me"},
                        timeout=_REQUEST_TIMEOUT_SECONDS,
                    )
                    listing.raise_for_status()
                    instances = listing.json().get("instances", [])
                    match = next(
                        (i for i in instances if int(i.get("id", -1)) == instance_id), None
                    )
                    if match is None:
                        break
                    final_status = match.get("actual_status", "unknown")
                    if final_status in self._DEAD_STATUSES:
                        break
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)

                elapsed = time.monotonic() - start
                success = final_status == "exited"
                span.set_attribute("vastai.final_status", final_status)

                logger.info(
                    "vastai_run_complete",
                    instance_id=instance_id,
                    final_status=final_status,
                    duration_s=round(elapsed, 3),
                )

                return ExecutionResult(
                    success=success,
                    exit_code=0 if success else -1,
                    stdout="",
                    stderr=(
                        ""
                        if success
                        else (
                            f"Vast.ai instance {instance_id} ended in state "
                            f"'{final_status}' (exit code not available via REST API "
                            "in this integration)"
                        )
                    ),
                    duration_seconds=round(elapsed, 3),
                )
            except Exception as exc:
                elapsed = time.monotonic() - start
                span.record_exception(exc)
                logger.error("vastai_run_failed", instance_id=instance_id, error=str(exc))
                return ExecutionResult(
                    success=False,
                    exit_code=-1,
                    stderr=str(exc),
                    duration_seconds=round(elapsed, 3),
                )
            finally:
                if instance_id is not None:
                    await self.cleanup(str(instance_id))

    async def cleanup(self, container_id: str) -> None:
        try:
            resp = await self._client.delete(
                f"{self._BASE_URL}/instances/{container_id}",
                headers=self._headers(),
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("vastai_cleanup_failed", instance_id=container_id, error=str(exc))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass(frozen=True)
class ComputeProviderProfile:
    """How to reach one ephemeral compute provider."""

    id: str
    runtime_cls: type[ContainerRuntime]
    credential_env: tuple[str, ...]
    supports_gpu: bool
    aliases: tuple[str, ...] = ()


def _local_runtime_cls() -> type[ContainerRuntime]:
    from tool_registry.container_runtime import DockerRuntime

    return DockerRuntime


_PROFILES: tuple[ComputeProviderProfile, ...] = (
    ComputeProviderProfile(
        id="docker",
        runtime_cls=_local_runtime_cls(),
        credential_env=(),
        supports_gpu=False,
        aliases=("local",),
    ),
    ComputeProviderProfile(
        id="runpod",
        runtime_cls=RunPodRuntime,
        credential_env=("RUNPOD_API_KEY",),
        supports_gpu=True,
    ),
    ComputeProviderProfile(
        id="vast_ai",
        runtime_cls=VastAIRuntime,
        credential_env=("VAST_API_KEY",),
        supports_gpu=True,
        aliases=("vastai", "vast.ai"),
    ),
)

_BY_ID: dict[str, ComputeProviderProfile] = {}
for _profile in _PROFILES:
    _BY_ID[_profile.id] = _profile
    for _alias in _profile.aliases:
        _BY_ID[_alias] = _profile


class UnknownComputeProviderError(KeyError):
    """No registered compute provider with the given id or alias."""


def available_providers() -> list[str]:
    """Canonical compute provider ids (not aliases), sorted."""
    return sorted({p.id for p in _PROFILES})


def get_profile(provider_id: str) -> ComputeProviderProfile:
    try:
        return _BY_ID[provider_id.strip().lower()]
    except KeyError as exc:
        raise UnknownComputeProviderError(provider_id) from exc


def resolve_runtime(provider_id: str | None = None) -> ContainerRuntime:
    """Instantiate the ``ContainerRuntime`` for ``provider_id``.

    ``provider_id`` defaults to the ``METAFORGE_COMPUTE_PROVIDER`` env var
    (default ``"docker"``), so unconfigured deployments keep running local
    Docker exactly as before this module was added.
    """
    resolved_id = provider_id or os.environ.get("METAFORGE_COMPUTE_PROVIDER", "docker")
    profile = get_profile(resolved_id)
    logger.info("compute_provider_resolved", provider=profile.id, supports_gpu=profile.supports_gpu)
    return profile.runtime_cls()
