"""Runs REST endpoints for the MetaForge Gateway (MET-547, Phase 1).

The OpenAI-compatible Runs API surface over the harness run lifecycle:

* ``POST   /v1/runs``               create a run (optionally start it)
* ``GET    /v1/runs``               list runs
* ``GET    /v1/runs/{id}``          fetch one run
* ``POST   /v1/runs/{id}/approval`` approve or reject a paused run

The run store is process-local for now (mirrors the chat backend pattern);
persistence lands in Phase 4. Domain errors map to clean HTTP status:
:class:`RunNotFoundError` -> 404, :class:`InvalidTransition` -> 409.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.runs.schemas import (
    ApprovalRequest,
    CreateRunRequest,
    RunListResponse,
    RunResponse,
)
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    RunNotFoundError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/runs", tags=["runs"])

# Process-local store (mirrors the chat backend pattern). Swappable via
# init_run_store() at app startup; reset_run_store() is for tests.
_store = InMemoryRunStore()


def get_run_store() -> InMemoryRunStore:
    return _store


def init_run_store(store: InMemoryRunStore) -> None:
    global _store
    _store = store


def reset_run_store() -> None:
    global _store
    _store = InMemoryRunStore()


@router.post("", response_model=RunResponse, status_code=201)
def create_run(body: CreateRunRequest) -> RunResponse:
    run = _store.create(body.request)
    if body.start:
        run = _store.start(run.id)
    logger.info("run_api_created", run_id=run.id, started=body.start)
    return RunResponse.from_run(run)


@router.get("", response_model=RunListResponse)
def list_runs() -> RunListResponse:
    return RunListResponse(runs=[RunResponse.from_run(r) for r in _store.list()])


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> RunResponse:
    try:
        return RunResponse.from_run(_store.get(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc


@router.post("/{run_id}/approval", response_model=RunResponse)
def submit_approval(run_id: str, body: ApprovalRequest) -> RunResponse:
    try:
        run = _store.submit_approval(run_id, ApprovalDecision(body.decision))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("run_api_approval", run_id=run_id, decision=body.decision)
    return RunResponse.from_run(run)
