"""YAML-based constraint rule loader for the Digital Twin constraint engine.

Parses YAML rule files, validates them against a Pydantic schema, and converts
them into Constraint objects compatible with the existing InMemoryConstraintEngine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator

from observability.tracing import get_tracer
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.constraint_engine.yaml_loader")


# ---------------------------------------------------------------------------
# Pydantic models for YAML rule schema
# ---------------------------------------------------------------------------


class YamlRule(BaseModel):
    """A single constraint rule defined in YAML."""

    name: str
    description: str
    domain: str = ""
    severity: Literal["critical", "warning", "info"]
    condition: str
    message_template: str = ""
    remediation_hint: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Rule name must not be empty")
        return v

    @field_validator("condition")
    @classmethod
    def _validate_condition(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Rule condition must not be empty")
        # Validate that the expression compiles
        try:
            compile(v, "<yaml-rule>", "eval")
        except SyntaxError as exc:
            raise ValueError(f"Invalid expression syntax: {exc}") from exc
        return v


class YamlRuleSet(BaseModel):
    """A collection of constraint rules for a specific domain."""

    domain: str
    version: str = "1.0"
    rules: list[YamlRule] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Domain must not be empty")
        return v


# ---------------------------------------------------------------------------
# Severity mapping: YAML uses "critical" while the engine uses "error"
# ---------------------------------------------------------------------------

_SEVERITY_MAP: dict[str, ConstraintSeverity] = {
    "critical": ConstraintSeverity.ERROR,
    "warning": ConstraintSeverity.WARNING,
    "info": ConstraintSeverity.INFO,
}


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------


class YamlRuleLoadError(Exception):
    """Raised when a YAML rule file cannot be loaded or validated."""


def load_rules_from_file(path: str | Path) -> YamlRuleSet:
    """Parse and validate a single YAML rule file.

    Args:
        path: Path to a ``.yaml`` or ``.yml`` file.

    Returns:
        A validated ``YamlRuleSet``.

    Raises:
        YamlRuleLoadError: If the file cannot be read, parsed, or validated.
    """
    path = Path(path)

    with tracer.start_as_current_span("yaml_loader.load_file") as span:
        span.set_attribute("file.path", str(path))

        if not path.exists():
            msg = f"Rule file not found: {path}"
            logger.error("yaml_rule_file_not_found", path=str(path))
            raise YamlRuleLoadError(msg)

        if not path.suffix.lower() in (".yaml", ".yml"):
            msg = f"Expected .yaml or .yml file, got: {path.suffix}"
            logger.error("yaml_rule_invalid_extension", path=str(path))
            raise YamlRuleLoadError(msg)

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"Cannot read rule file {path}: {exc}"
            logger.error("yaml_rule_read_error", path=str(path), error=str(exc))
            span.record_exception(exc)
            raise YamlRuleLoadError(msg) from exc

        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            msg = f"Invalid YAML in {path}: {exc}"
            logger.error("yaml_rule_parse_error", path=str(path), error=str(exc))
            span.record_exception(exc)
            raise YamlRuleLoadError(msg) from exc

        if not isinstance(data, dict):
            msg = f"Expected a YAML mapping at top level in {path}, got {type(data).__name__}"
            logger.error("yaml_rule_schema_error", path=str(path))
            raise YamlRuleLoadError(msg)

        try:
            ruleset = YamlRuleSet.model_validate(data)
        except Exception as exc:
            msg = f"Schema validation failed for {path}: {exc}"
            logger.error("yaml_rule_validation_error", path=str(path), error=str(exc))
            span.record_exception(exc)
            raise YamlRuleLoadError(msg) from exc

        # Inherit domain from ruleset into rules that don't specify one
        for rule in ruleset.rules:
            if not rule.domain:
                rule.domain = ruleset.domain

        logger.info(
            "yaml_rules_loaded",
            path=str(path),
            domain=ruleset.domain,
            rule_count=len(ruleset.rules),
        )
        span.set_attribute("rules.count", len(ruleset.rules))
        span.set_attribute("rules.domain", ruleset.domain)

        return ruleset


def load_rules_from_directory(dir_path: str | Path) -> list[YamlRuleSet]:
    """Auto-discover and load all ``.yaml`` / ``.yml`` rule files from a directory.

    Args:
        dir_path: Directory to scan (non-recursive).

    Returns:
        List of validated ``YamlRuleSet`` objects. Files that fail validation
        are logged and skipped (not raised).
    """
    dir_path = Path(dir_path)

    with tracer.start_as_current_span("yaml_loader.load_directory") as span:
        span.set_attribute("dir.path", str(dir_path))

        if not dir_path.is_dir():
            logger.warning("yaml_rule_dir_not_found", path=str(dir_path))
            return []

        yaml_files = sorted(
            p for p in dir_path.iterdir()
            if p.is_file() and p.suffix.lower() in (".yaml", ".yml")
        )

        if not yaml_files:
            logger.info("yaml_rule_dir_empty", path=str(dir_path))
            return []

        rulesets: list[YamlRuleSet] = []
        for yaml_file in yaml_files:
            try:
                rs = load_rules_from_file(yaml_file)
                rulesets.append(rs)
            except YamlRuleLoadError as exc:
                logger.warning(
                    "yaml_rule_file_skipped",
                    path=str(yaml_file),
                    reason=str(exc),
                )

        span.set_attribute("rulesets.loaded", len(rulesets))
        span.set_attribute("files.total", len(yaml_files))

        logger.info(
            "yaml_rules_directory_loaded",
            path=str(dir_path),
            loaded=len(rulesets),
            total_files=len(yaml_files),
        )

        return rulesets


# ---------------------------------------------------------------------------
# Conversion to Constraint objects
# ---------------------------------------------------------------------------


def convert_to_constraints(ruleset: YamlRuleSet) -> list[Constraint]:
    """Convert a ``YamlRuleSet`` into a list of ``Constraint`` objects.

    Each YAML rule is mapped to a ``Constraint`` node compatible with the
    existing ``InMemoryConstraintEngine``.

    Args:
        ruleset: A validated YAML rule set.

    Returns:
        List of ``Constraint`` objects ready for use with the constraint engine.
    """
    with tracer.start_as_current_span("yaml_loader.convert_to_constraints") as span:
        span.set_attribute("rules.domain", ruleset.domain)
        span.set_attribute("rules.count", len(ruleset.rules))

        constraints: list[Constraint] = []
        for rule in ruleset.rules:
            severity = _SEVERITY_MAP[rule.severity]
            domain = rule.domain or ruleset.domain
            is_cross_domain = domain == "cross_domain"

            # Build the message from template or description
            message = rule.message_template or rule.description

            constraint = Constraint(
                name=rule.name,
                expression=rule.condition,
                severity=severity,
                domain=domain,
                cross_domain=is_cross_domain,
                source=f"yaml:{ruleset.domain}/v{ruleset.version}",
                message=message,
                metadata={
                    "description": rule.description,
                    "remediation_hint": rule.remediation_hint,
                    "tags": rule.tags,
                    "yaml_version": ruleset.version,
                },
            )
            constraints.append(constraint)

        logger.info(
            "yaml_rules_converted",
            domain=ruleset.domain,
            constraint_count=len(constraints),
        )

        return constraints


def load_and_convert_directory(dir_path: str | Path) -> list[Constraint]:
    """Convenience: load all YAML rule files from a directory and convert them.

    Args:
        dir_path: Directory containing ``.yaml`` rule files.

    Returns:
        Flat list of ``Constraint`` objects from all rule sets.
    """
    rulesets = load_rules_from_directory(dir_path)
    all_constraints: list[Constraint] = []
    for rs in rulesets:
        all_constraints.extend(convert_to_constraints(rs))
    return all_constraints
