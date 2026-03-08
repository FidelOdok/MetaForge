"""Constraint engine — cross-domain validation for the Digital Twin."""

from twin_core.constraint_engine.cross_domain import (
    CrossDomainCheck,
    CrossDomainValidator,
)
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)
from twin_core.constraint_engine.validator import (
    ConstraintEngine,
    InMemoryConstraintEngine,
)
from twin_core.constraint_engine.yaml_loader import (
    YamlRule,
    YamlRuleLoadError,
    YamlRuleSet,
    convert_to_constraints,
    load_and_convert_directory,
    load_rules_from_directory,
    load_rules_from_file,
)

__all__ = [
    "ConstraintEngine",
    "InMemoryConstraintEngine",
    "ConstraintEvaluationResult",
    "ConstraintViolation",
    "CrossDomainCheck",
    "CrossDomainValidator",
    "YamlRule",
    "YamlRuleLoadError",
    "YamlRuleSet",
    "convert_to_constraints",
    "load_and_convert_directory",
    "load_rules_from_directory",
    "load_rules_from_file",
]
