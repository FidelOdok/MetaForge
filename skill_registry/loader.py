"""Skill loader — dynamic import and validation of skill modules."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from skill_registry.schema_validator import SchemaValidator, SkillDefinition
from skill_registry.skill_base import SkillBase

logger = structlog.get_logger()


class SkillLoadError(Exception):
    """Raised when a skill fails to load."""

    def __init__(self, skill_name: str, reason: str, path: str = "") -> None:
        self.skill_name = skill_name
        self.reason = reason
        self.path = path
        super().__init__(f"Failed to load skill '{skill_name}' at {path}: {reason}")


class LoadedSkill:
    """Result of successfully loading a skill — holds all resolved references."""

    def __init__(
        self,
        definition: SkillDefinition,
        input_schema: type[BaseModel],
        output_schema: type[BaseModel],
        handler_class: type[SkillBase[Any, Any]],
        skill_path: str,
    ) -> None:
        self.definition = definition
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.handler_class = handler_class
        self.skill_path = skill_path


class SkillLoader:
    """Dynamically loads skill modules from the filesystem.

    Loading process:
    1. Read definition.json
    2. Validate against SkillDefinition schema
    3. Import schema.py module (importlib)
    4. Resolve input_schema and output_schema references
    5. Import handler.py module
    6. Verify handler subclasses SkillBase
    7. Verify handler.input_type matches definition's input_schema class
    8. Return LoadedSkill

    Follows fail-soft strategy: individual skill failures don't block others.
    """

    def load(self, skill_path: str) -> LoadedSkill:
        """Load a single skill from its directory path.

        Args:
            skill_path: Path to the skill directory containing
                definition.json, schema.py, handler.py

        Returns:
            LoadedSkill with all resolved references

        Raises:
            SkillLoadError: If any validation or import step fails
        """
        path = Path(skill_path)

        # Step 1: Read definition.json
        definition = self._load_definition(path)

        # Step 2-4: Import schema.py and resolve classes
        input_class, output_class = self._load_schemas(path, definition)

        # Step 5-7: Import handler.py, find SkillBase subclass, verify types
        handler_class = self._load_handler(path, definition, input_class)

        logger.info("Skill loaded", skill=definition.name, version=definition.version)
        return LoadedSkill(
            definition=definition,
            input_schema=input_class,
            output_schema=output_class,
            handler_class=handler_class,
            skill_path=skill_path,
        )

    def load_all(
        self, search_paths: list[str] | None = None
    ) -> tuple[list[LoadedSkill], list[SkillLoadError]]:
        """Load all skills found in search paths.

        Follows fail-soft strategy: if a skill fails to load, record the
        error and continue.

        Args:
            search_paths: Directories to scan. Default: ["domain_agents"]

        Returns:
            Tuple of (successfully loaded skills, list of errors for failed skills)
        """
        if search_paths is None:
            search_paths = ["domain_agents"]

        loaded: list[LoadedSkill] = []
        errors: list[SkillLoadError] = []

        for base_path in search_paths:
            base = Path(base_path)
            if not base.exists():
                continue

            for def_file in sorted(base.glob("*/skills/*/definition.json")):
                skill_dir = str(def_file.parent)
                try:
                    skill = self.load(skill_dir)
                    loaded.append(skill)
                except SkillLoadError as exc:
                    errors.append(exc)
                    logger.warning("Skill load failed", skill=exc.skill_name, reason=exc.reason)
                except Exception as exc:
                    err = SkillLoadError("unknown", str(exc), skill_dir)
                    errors.append(err)
                    logger.warning("Unexpected error loading skill", path=skill_dir, error=str(exc))

        return loaded, errors

    def validate_skill_directory(self, skill_path: str) -> list[str]:
        """Validate a skill directory without loading it.

        Returns a list of error messages (empty if valid).
        Checks:
        1. definition.json exists and is valid
        2. schema.py exists
        3. handler.py exists
        4. tests.py exists
        5. SKILL.md exists
        """
        issues: list[str] = []
        path = Path(skill_path)

        # Check required files
        required_files = [
            "definition.json",
            "schema.py",
            "handler.py",
            "tests.py",
            "SKILL.md",
        ]
        for filename in required_files:
            if not (path / filename).exists():
                issues.append(f"Missing required file: {filename}")

        # Validate definition.json if present
        def_path = path / "definition.json"
        if def_path.exists():
            try:
                raw = json.loads(def_path.read_text())
                SchemaValidator.validate_definition(raw)
            except json.JSONDecodeError as exc:
                issues.append(f"Invalid JSON in definition.json: {exc}")
            except Exception as exc:
                issues.append(f"Definition validation error: {exc}")

        return issues

    def _load_definition(self, path: Path) -> SkillDefinition:
        """Read and validate definition.json."""
        def_path = path / "definition.json"
        if not def_path.exists():
            raise SkillLoadError("unknown", "definition.json not found", str(path))

        try:
            raw = json.loads(def_path.read_text())
        except json.JSONDecodeError as exc:
            raise SkillLoadError(
                "unknown", f"Invalid JSON in definition.json: {exc}", str(path)
            ) from exc

        try:
            return SchemaValidator.validate_definition(raw)
        except Exception as exc:
            name: str = raw.get("name", "unknown")
            raise SkillLoadError(name, f"Definition validation failed: {exc}", str(path)) from exc

    def _load_schemas(
        self, path: Path, definition: SkillDefinition
    ) -> tuple[type[BaseModel], type[BaseModel]]:
        """Import schema.py and resolve input/output schema classes."""
        schema_path = path / "schema.py"
        if not schema_path.exists():
            raise SkillLoadError(definition.name, "schema.py not found", str(path))

        try:
            module = self._import_module(str(schema_path), f"_skill_{definition.name}_schema")
        except Exception as exc:
            raise SkillLoadError(
                definition.name, f"Failed to import schema.py: {exc}", str(path)
            ) from exc

        # Resolve "schema.ClassName" -> ClassName from the module
        input_class = self._resolve_schema_class(module, definition.input_schema)
        if input_class is None:
            raise SkillLoadError(
                definition.name,
                f"Cannot resolve input_schema '{definition.input_schema}' from schema.py",
                str(path),
            )

        output_class = self._resolve_schema_class(module, definition.output_schema)
        if output_class is None:
            raise SkillLoadError(
                definition.name,
                f"Cannot resolve output_schema '{definition.output_schema}' from schema.py",
                str(path),
            )

        return input_class, output_class

    def _load_handler(
        self,
        path: Path,
        definition: SkillDefinition,
        input_class: type[BaseModel],
    ) -> type[SkillBase[Any, Any]]:
        """Import handler.py, find SkillBase subclass, verify input_type matches."""
        handler_path = path / "handler.py"
        if not handler_path.exists():
            raise SkillLoadError(definition.name, "handler.py not found", str(path))

        try:
            module = self._import_module(str(handler_path), f"_skill_{definition.name}_handler")
        except Exception as exc:
            raise SkillLoadError(
                definition.name, f"Failed to import handler.py: {exc}", str(path)
            ) from exc

        # Find SkillBase subclass
        handler_class = self._find_handler_class(module)
        if handler_class is None:
            raise SkillLoadError(
                definition.name,
                "No SkillBase subclass found in handler.py",
                str(path),
            )

        # Verify handler's input_type matches (if set)
        handler_input: type[BaseModel] | None = getattr(handler_class, "input_type", None)
        if handler_input is not None and handler_input is not input_class:
            # Check by name as a fallback — dynamic imports may create separate
            # class objects
            if handler_input.__name__ != input_class.__name__:
                raise SkillLoadError(
                    definition.name,
                    f"Handler input_type ({handler_input.__name__}) doesn't match "
                    f"definition input_schema ({input_class.__name__})",
                    str(path),
                )

        return handler_class

    @staticmethod
    def _import_module(file_path: str, module_name: str) -> Any:
        """Dynamically import a Python module from a file path using importlib."""
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {file_path}")
        module = importlib.util.module_from_spec(spec)
        # Add to sys.modules temporarily for cross-imports within the skill
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module

    @staticmethod
    def _resolve_schema_class(module: Any, dotted_path: str) -> type[BaseModel] | None:
        """Resolve 'schema.ClassName' to the actual class from the imported module.

        Takes the part after the last dot as the class name.
        """
        class_name = dotted_path.split(".")[-1]
        cls: object = getattr(module, class_name, None)
        if cls is not None and isinstance(cls, type) and issubclass(cls, BaseModel):
            return cls
        return None

    @staticmethod
    def _find_handler_class(module: Any) -> type[SkillBase[Any, Any]] | None:
        """Find the first concrete SkillBase subclass in a handler module."""
        for attr_name in dir(module):
            attr: object = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, SkillBase) and attr is not SkillBase:
                return attr
        return None
