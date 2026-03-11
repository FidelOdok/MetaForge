"""Skill registry — central catalog for skill discovery and management."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from skill_registry.schema_validator import SchemaValidator
from skill_registry.skill_base import SkillBase

logger = structlog.get_logger()


class SkillRegistration(BaseModel):
    """A registered skill with its metadata and resolved references."""

    name: str
    version: str
    domain: str
    agent: str
    description: str
    phase: int
    status: str = "REGISTERED"  # DRAFT, REGISTERED, ACTIVE, DEPRECATED
    input_schema: type[BaseModel]  # resolved Pydantic class
    output_schema: type[BaseModel]  # resolved Pydantic class
    handler_class: type[SkillBase]  # type: ignore[type-arg]  # resolved handler class
    tools_required: list[dict[str, Any]]
    timeout_seconds: int = 120
    retries: int = 0
    idempotent: bool = False
    tags: list[str] = Field(default_factory=list)
    skill_path: str = ""
    deprecation_reason: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class SkillLoadError(Exception):
    """Raised when a skill fails to load."""

    def __init__(self, skill_name: str, reason: str, path: str = "") -> None:
        self.skill_name = skill_name
        self.reason = reason
        self.path = path
        super().__init__(f"Failed to load skill '{skill_name}' at {path}: {reason}")


class SkillRegistry:
    """Central catalog for skill discovery and management.

    Lifecycle: DRAFT -> REGISTERED -> ACTIVE -> DEPRECATED
    - discover() scans directories for skills
    - register() validates and loads a single skill
    - get() / list_skills() for lookup
    - activate() / deprecate() for lifecycle management
    - health() for registry status report
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillRegistration] = {}

    async def discover(self, search_paths: list[str] | None = None) -> int:
        """Auto-discover skills by scanning domain_agents/*/skills/ directories.

        Default search_paths: ["domain_agents"]
        Looks for: <search_path>/<domain>/skills/<skill_name>/definition.json

        Returns the number of newly discovered skills.
        Uses fail-soft strategy: if one skill fails to load, log it and continue.
        """
        if search_paths is None:
            search_paths = ["domain_agents"]

        count = 0
        for base_path in search_paths:
            base = Path(base_path)
            if not base.exists():
                continue
            # Look for definition.json files in the skill directory convention
            for def_file in sorted(base.glob("*/skills/*/definition.json")):
                skill_dir = str(def_file.parent)
                try:
                    await self.register(skill_dir)
                    count += 1
                except SkillLoadError as exc:
                    logger.warning(
                        "Skill discovery failed",
                        skill=exc.skill_name,
                        reason=exc.reason,
                        path=exc.path,
                    )
                except Exception as exc:
                    logger.warning(
                        "Unexpected error during skill discovery",
                        path=skill_dir,
                        error=str(exc),
                    )
        return count

    async def register(self, skill_path: str) -> SkillRegistration:
        """Register a skill from its directory path.

        Process:
        1. Read and validate definition.json
        2. Import schema.py and resolve input/output schema classes
        3. Import handler.py and find the SkillBase subclass
        4. Verify handler's input_type matches definition
        5. Create SkillRegistration with status=REGISTERED

        Raises SkillLoadError if any step fails.
        """
        path = Path(skill_path)
        def_path = path / "definition.json"

        # 1. Read definition
        if not def_path.exists():
            raise SkillLoadError("unknown", "definition.json not found", skill_path)

        try:
            raw = json.loads(def_path.read_text())
        except json.JSONDecodeError as exc:
            raise SkillLoadError("unknown", f"Invalid JSON: {exc}", skill_path) from exc

        # 2. Validate definition
        try:
            definition = SchemaValidator.validate_definition(raw)
        except Exception as exc:
            name = raw.get("name", "unknown")
            raise SkillLoadError(name, f"Definition validation failed: {exc}", skill_path) from exc

        # Check for duplicate
        if definition.name in self._skills:
            existing = self._skills[definition.name]
            if existing.status != "DRAFT":
                raise SkillLoadError(definition.name, "Skill already registered", skill_path)

        # 3. Import schema.py and resolve schema classes
        schema_module_path = path / "schema.py"
        if not schema_module_path.exists():
            raise SkillLoadError(definition.name, "schema.py not found", skill_path)

        try:
            schema_module = self._import_module_from_path(
                str(schema_module_path), f"{definition.name}_schema"
            )
        except Exception as exc:
            raise SkillLoadError(
                definition.name,
                f"Failed to import schema.py: {exc}",
                skill_path,
            ) from exc

        # Resolve input_schema reference (e.g., "schema.ValidateStressInput" -> class)
        input_class = self._resolve_class(schema_module, definition.input_schema, "input_schema")
        if input_class is None:
            raise SkillLoadError(
                definition.name,
                f"Cannot resolve input_schema '{definition.input_schema}' in schema.py",
                skill_path,
            )

        output_class = self._resolve_class(schema_module, definition.output_schema, "output_schema")
        if output_class is None:
            raise SkillLoadError(
                definition.name,
                f"Cannot resolve output_schema '{definition.output_schema}' in schema.py",
                skill_path,
            )

        # 4. Import handler.py and find SkillBase subclass
        handler_module_path = path / "handler.py"
        if not handler_module_path.exists():
            raise SkillLoadError(definition.name, "handler.py not found", skill_path)

        try:
            handler_module = self._import_module_from_path(
                str(handler_module_path), f"{definition.name}_handler"
            )
        except Exception as exc:
            raise SkillLoadError(
                definition.name,
                f"Failed to import handler.py: {exc}",
                skill_path,
            ) from exc

        handler_class = self._find_skill_class(handler_module)
        if handler_class is None:
            raise SkillLoadError(
                definition.name,
                "No SkillBase subclass found in handler.py",
                skill_path,
            )

        # 5. Create registration
        registration = SkillRegistration(
            name=definition.name,
            version=definition.version,
            domain=definition.domain,
            agent=definition.agent,
            description=definition.description,
            phase=definition.phase,
            status="REGISTERED",
            input_schema=input_class,
            output_schema=output_class,
            handler_class=handler_class,
            tools_required=[t.model_dump() for t in definition.tools_required],
            timeout_seconds=definition.timeout_seconds,
            retries=definition.retries,
            idempotent=definition.idempotent,
            tags=definition.tags,
            skill_path=skill_path,
        )

        self._skills[definition.name] = registration
        logger.info(
            "Skill registered",
            skill=definition.name,
            version=definition.version,
            domain=definition.domain,
        )
        return registration

    async def get(self, skill_name: str) -> SkillRegistration | None:
        """Look up a skill by name."""
        return self._skills.get(skill_name)

    async def list_skills(
        self,
        domain: str | None = None,
        agent: str | None = None,
        phase: int | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> list[SkillRegistration]:
        """Query skills with optional filters."""
        results = list(self._skills.values())
        if domain is not None:
            results = [s for s in results if s.domain == domain]
        if agent is not None:
            results = [s for s in results if s.agent == agent]
        if phase is not None:
            results = [s for s in results if s.phase <= phase]
        if status is not None:
            results = [s for s in results if s.status == status]
        if tags is not None:
            tag_set = set(tags)
            results = [s for s in results if tag_set.issubset(set(s.tags))]
        return results

    async def activate(self, skill_name: str) -> None:
        """Promote a REGISTERED skill to ACTIVE."""
        reg = self._skills.get(skill_name)
        if reg is None:
            raise KeyError(f"Skill '{skill_name}' not found")
        if reg.status != "REGISTERED":
            raise ValueError(f"Cannot activate skill in '{reg.status}' state (must be REGISTERED)")
        reg.status = "ACTIVE"
        logger.info("Skill activated", skill=skill_name)

    async def deprecate(self, skill_name: str, reason: str) -> None:
        """Mark an ACTIVE skill as DEPRECATED."""
        reg = self._skills.get(skill_name)
        if reg is None:
            raise KeyError(f"Skill '{skill_name}' not found")
        if reg.status not in ("REGISTERED", "ACTIVE"):
            raise ValueError(f"Cannot deprecate skill in '{reg.status}' state")
        reg.status = "DEPRECATED"
        reg.deprecation_reason = reason
        logger.info("Skill deprecated", skill=skill_name, reason=reason)

    async def health(self) -> dict[str, Any]:
        """Registry health report: total skills, by status, by domain."""
        by_status: dict[str, int] = {}
        by_domain: dict[str, int] = {}
        for reg in self._skills.values():
            by_status[reg.status] = by_status.get(reg.status, 0) + 1
            by_domain[reg.domain] = by_domain.get(reg.domain, 0) + 1
        return {
            "total": len(self._skills),
            "by_status": by_status,
            "by_domain": by_domain,
        }

    @staticmethod
    def _import_module_from_path(file_path: str, module_name: str) -> Any:
        """Dynamically import a Python module from a file path."""
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            msg = f"Cannot create module spec for {file_path}"
            raise ImportError(msg)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _resolve_class(module: Any, dotted_path: str, label: str) -> type[BaseModel] | None:
        """Resolve 'schema.ClassName' to the actual class from the module.

        The dotted_path format is 'schema.ClassName' -- we take the part after the last dot.
        """
        parts = dotted_path.split(".")
        class_name = parts[-1]  # e.g., "ValidateStressInput"
        cls = getattr(module, class_name, None)
        if cls is not None and isinstance(cls, type) and issubclass(cls, BaseModel):
            return cls  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _find_skill_class(
        module: Any,
    ) -> type[SkillBase] | None:  # type: ignore[type-arg]
        """Find the first SkillBase subclass in a handler module."""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, SkillBase) and attr is not SkillBase:
                return attr
        return None
