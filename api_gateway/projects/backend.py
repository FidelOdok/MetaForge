"""Project storage backends — in-memory and PostgreSQL.

Provides a ``ProjectBackend`` protocol that the project routes call.
Two implementations:

- ``InMemoryProjectBackend`` — dict-backed, used when ``DATABASE_URL`` is unset
- ``PgProjectBackend`` — delegates to SQLAlchemy async sessions

The module-level :func:`create_backend` factory selects the right one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import uuid4

import structlog

from api_gateway.projects.schemas import (
    ProjectResponse,
    ProjectWorkProductResponse,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.projects.backend")


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class ProjectBackend(ABC):
    """Async interface for project storage operations."""

    @abstractmethod
    async def list_projects(self) -> list[ProjectResponse]: ...

    @abstractmethod
    async def get_project(self, project_id: str) -> ProjectResponse | None: ...

    @abstractmethod
    async def create_project(
        self,
        *,
        name: str,
        description: str,
        status: str,
    ) -> ProjectResponse: ...

    @abstractmethod
    async def delete_project(self, project_id: str) -> bool: ...

    @abstractmethod
    async def link_work_product(
        self,
        project_id: str,
        wp_id: str,
        wp_name: str,
        wp_type: str,
    ) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryProjectBackend(ProjectBackend):
    """Dict-backed project storage (development / tests)."""

    def __init__(self) -> None:
        self.projects: dict[str, ProjectResponse] = {}

    @classmethod
    def create(cls) -> InMemoryProjectBackend:
        return cls()

    async def list_projects(self) -> list[ProjectResponse]:
        return list(self.projects.values())

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        return self.projects.get(project_id)

    async def create_project(
        self,
        *,
        name: str,
        description: str,
        status: str,
    ) -> ProjectResponse:
        now = datetime.now(UTC).isoformat()
        project_id = str(uuid4())
        project = ProjectResponse(
            id=project_id,
            name=name,
            description=description,
            status=status,
            work_products=[],
            agent_count=0,
            last_updated=now,
            created_at=now,
        )
        self.projects[project_id] = project
        return project

    async def delete_project(self, project_id: str) -> bool:
        if project_id not in self.projects:
            return False
        del self.projects[project_id]
        return True

    async def link_work_product(
        self,
        project_id: str,
        wp_id: str,
        wp_name: str,
        wp_type: str,
    ) -> None:
        project = self.projects.get(project_id)
        if project is None:
            return
        now = datetime.now(UTC).isoformat()
        project.work_products.append(
            ProjectWorkProductResponse(
                id=wp_id,
                name=wp_name,
                type=wp_type,
                status="created",
                updated_at=now,
            )
        )
        project.last_updated = now
        logger.info(
            "work_product_linked_to_project",
            project_id=project_id,
            work_product_id=wp_id,
        )


# ---------------------------------------------------------------------------
# PostgreSQL implementation
# ---------------------------------------------------------------------------


class PgProjectBackend(ProjectBackend):
    """PostgreSQL-backed project storage via SQLAlchemy async sessions."""

    async def list_projects(self) -> list[ProjectResponse]:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            from sqlalchemy import select

            from api_gateway.db.models import ProjectRow, ProjectWorkProductRow

            stmt = select(ProjectRow).order_by(ProjectRow.created_at.desc())
            result = await session.execute(stmt)
            rows = result.scalars().all()

            projects = []
            for row in rows:
                wp_stmt = select(ProjectWorkProductRow).where(
                    ProjectWorkProductRow.project_id == row.id
                )
                wp_result = await session.execute(wp_stmt)
                wp_rows = wp_result.scalars().all()
                projects.append(self._row_to_response(row, wp_rows))
            return projects

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            from sqlalchemy import select

            from api_gateway.db.models import ProjectRow, ProjectWorkProductRow

            row = await session.get(ProjectRow, project_id)
            if row is None:
                return None

            wp_stmt = select(ProjectWorkProductRow).where(
                ProjectWorkProductRow.project_id == project_id
            )
            wp_result = await session.execute(wp_stmt)
            wp_rows = wp_result.scalars().all()
            return self._row_to_response(row, wp_rows)

    async def create_project(
        self,
        *,
        name: str,
        description: str,
        status: str,
    ) -> ProjectResponse:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import ProjectRow

        project_id = str(uuid4())
        now = datetime.now(UTC)

        async with get_session() as session:
            row = ProjectRow(
                id=project_id,
                name=name,
                description=description,
                status=status,
                agent_count=0,
                created_at=now,
                last_updated=now,
            )
            session.add(row)
            await session.flush()
            logger.info("project_created_pg", project_id=project_id, name=name)
            return self._row_to_response(row, [])

    async def delete_project(self, project_id: str) -> bool:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import ProjectRow

        async with get_session() as session:
            row = await session.get(ProjectRow, project_id)
            if row is None:
                return False
            await session.delete(row)
            await session.flush()
            logger.info("project_deleted_pg", project_id=project_id)
            return True

    async def link_work_product(
        self,
        project_id: str,
        wp_id: str,
        wp_name: str,
        wp_type: str,
    ) -> None:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import ProjectRow, ProjectWorkProductRow

        async with get_session() as session:
            row = await session.get(ProjectRow, project_id)
            if row is None:
                return

            now = datetime.now(UTC)
            wp_row = ProjectWorkProductRow(
                id=str(uuid4()),
                project_id=project_id,
                work_product_id=wp_id,
                name=wp_name,
                type=wp_type,
                status="created",
                updated_at=now,
            )
            session.add(wp_row)
            row.last_updated = now
            await session.flush()
            logger.info(
                "work_product_linked_pg",
                project_id=project_id,
                work_product_id=wp_id,
            )

    @staticmethod
    def _row_to_response(row, wp_rows) -> ProjectResponse:  # noqa: ANN001
        return ProjectResponse(
            id=row.id,
            name=row.name,
            description=row.description,
            status=row.status,
            work_products=[
                ProjectWorkProductResponse(
                    id=wp.work_product_id,
                    name=wp.name,
                    type=wp.type,
                    status=wp.status,
                    updated_at=(
                        wp.updated_at.isoformat()
                        if hasattr(wp.updated_at, "isoformat")
                        else str(wp.updated_at)
                    ),
                )
                for wp in wp_rows
            ],
            agent_count=row.agent_count,
            last_updated=(
                row.last_updated.isoformat()
                if hasattr(row.last_updated, "isoformat")
                else str(row.last_updated)
            ),
            created_at=(
                row.created_at.isoformat()
                if hasattr(row.created_at, "isoformat")
                else str(row.created_at)
            ),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def create_project_backend() -> ProjectBackend:
    """Create the appropriate project backend based on environment."""
    try:
        from api_gateway.db import HAS_SQLALCHEMY
        from api_gateway.db.engine import get_engine

        if HAS_SQLALCHEMY and get_engine() is not None:
            logger.info("project_backend_pg_initialized")
            return PgProjectBackend()
    except Exception as exc:
        logger.warning("project_backend_pg_failed_fallback", error=str(exc))

    logger.info("project_backend_in_memory_initialized")
    return InMemoryProjectBackend.create()
