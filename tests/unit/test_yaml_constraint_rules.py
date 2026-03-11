"""Unit tests for YAML constraint rule loader and domain rule files."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from twin_core.constraint_engine.yaml_loader import (
    YamlRule,
    YamlRuleLoadError,
    YamlRuleSet,
    convert_to_constraints,
    load_and_convert_directory,
    load_rules_from_directory,
    load_rules_from_file,
)
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, ConstraintStatus

# ---------------------------------------------------------------------------
# Path to the bundled rule files
# ---------------------------------------------------------------------------

_RULES_DIR = Path(__file__).resolve().parents[2] / "twin_core" / "constraint_engine" / "rules"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(dedent(content), encoding="utf-8")
    return p


def _eval_simple(expression: str, variables: dict) -> tuple[ConstraintStatus, str]:
    """Evaluate a YAML rule condition with flat variable bindings."""
    # The existing engine evaluates against a ConstraintContext (ctx).
    # For YAML rules that use simple variable names (not ctx.*), we compile
    # the expression and evaluate with the variables as globals.
    from twin_core.constraint_engine.validator import _SAFE_BUILTINS

    try:
        code = compile(expression, "<test>", "eval")
    except SyntaxError as exc:
        return ConstraintStatus.SKIPPED, f"Syntax error: {exc}"

    restricted_globals: dict = {"__builtins__": _SAFE_BUILTINS}
    restricted_globals.update(variables)
    try:
        result = eval(code, restricted_globals)  # noqa: S307
    except Exception as exc:
        return ConstraintStatus.SKIPPED, f"Runtime error: {exc}"

    if result:
        return ConstraintStatus.PASS, ""
    return ConstraintStatus.FAIL, f"Expression evaluated to {result!r}"


# ===========================================================================
# YAML Parsing Tests
# ===========================================================================


class TestYamlParsing:
    def test_load_valid_file(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "test.yaml",
            """\
            domain: mechanical
            version: "1.0"
            rules:
              - name: test_rule
                description: "A test rule"
                severity: critical
                condition: "x > 0"
        """,
        )
        rs = load_rules_from_file(path)
        assert rs.domain == "mechanical"
        assert rs.version == "1.0"
        assert len(rs.rules) == 1
        assert rs.rules[0].name == "test_rule"
        assert rs.rules[0].severity == "critical"

    def test_load_file_inherits_domain(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "test.yaml",
            """\
            domain: electrical
            version: "2.0"
            rules:
              - name: rule_no_domain
                description: "Rule without explicit domain"
                severity: warning
                condition: "v < 5"
        """,
        )
        rs = load_rules_from_file(path)
        assert rs.rules[0].domain == "electrical"

    def test_load_file_with_tags_and_hint(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "test.yaml",
            """\
            domain: firmware
            version: "1.0"
            rules:
              - name: flash_check
                description: "Flash check"
                severity: warning
                condition: "used < total"
                message_template: "Flash full"
                remediation_hint: "Free up flash"
                tags: [memory, flash]
        """,
        )
        rs = load_rules_from_file(path)
        rule = rs.rules[0]
        assert rule.remediation_hint == "Free up flash"
        assert rule.tags == ["memory", "flash"]
        assert rule.message_template == "Flash full"

    def test_load_multiple_rules(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "test.yaml",
            """\
            domain: mechanical
            version: "1.0"
            rules:
              - name: r1
                description: "Rule 1"
                severity: critical
                condition: "a > 0"
              - name: r2
                description: "Rule 2"
                severity: warning
                condition: "b > 0"
              - name: r3
                description: "Rule 3"
                severity: info
                condition: "c > 0"
        """,
        )
        rs = load_rules_from_file(path)
        assert len(rs.rules) == 3
        assert [r.severity for r in rs.rules] == ["critical", "warning", "info"]


class TestYamlParsingErrors:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(YamlRuleLoadError, match="not found"):
            load_rules_from_file(tmp_path / "nonexistent.yaml")

    def test_wrong_extension_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "test.json"
        path.write_text("{}")
        with pytest.raises(YamlRuleLoadError, match="Expected .yaml"):
            load_rules_from_file(path)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            domain: test
            rules:
              - name: [invalid
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="Invalid YAML"):
            load_rules_from_file(path)

    def test_missing_domain_raises(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            domain: ""
            version: "1.0"
            rules: []
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="validation failed"):
            load_rules_from_file(path)

    def test_invalid_severity_raises(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            domain: test
            rules:
              - name: bad_rule
                description: "Bad severity"
                severity: fatal
                condition: "True"
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="validation failed"):
            load_rules_from_file(path)

    def test_invalid_expression_raises(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            domain: test
            rules:
              - name: bad_expr
                description: "Invalid expression"
                severity: critical
                condition: "def foo():"
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="validation failed"):
            load_rules_from_file(path)

    def test_empty_name_raises(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            domain: test
            rules:
              - name: ""
                description: "No name"
                severity: critical
                condition: "True"
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="validation failed"):
            load_rules_from_file(path)

    def test_not_a_mapping_raises(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            - item1
            - item2
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="Expected a YAML mapping"):
            load_rules_from_file(path)


# ===========================================================================
# Directory Loading Tests
# ===========================================================================


class TestDirectoryLoading:
    def test_load_directory(self, tmp_path: Path) -> None:
        _write_yaml(
            tmp_path,
            "a.yaml",
            """\
            domain: mechanical
            version: "1.0"
            rules:
              - name: r1
                description: "Rule 1"
                severity: critical
                condition: "True"
        """,
        )
        _write_yaml(
            tmp_path,
            "b.yaml",
            """\
            domain: electrical
            version: "1.0"
            rules:
              - name: r2
                description: "Rule 2"
                severity: warning
                condition: "True"
        """,
        )
        rulesets = load_rules_from_directory(tmp_path)
        assert len(rulesets) == 2
        domains = {rs.domain for rs in rulesets}
        assert domains == {"mechanical", "electrical"}

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        rulesets = load_rules_from_directory(tmp_path / "nope")
        assert rulesets == []

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        rulesets = load_rules_from_directory(tmp_path)
        assert rulesets == []

    def test_skips_invalid_files(self, tmp_path: Path) -> None:
        _write_yaml(
            tmp_path,
            "good.yaml",
            """\
            domain: mechanical
            version: "1.0"
            rules:
              - name: r1
                description: "Good"
                severity: critical
                condition: "True"
        """,
        )
        _write_yaml(
            tmp_path,
            "bad.yaml",
            """\
            - not a mapping
        """,
        )
        rulesets = load_rules_from_directory(tmp_path)
        assert len(rulesets) == 1
        assert rulesets[0].domain == "mechanical"

    def test_ignores_non_yaml_files(self, tmp_path: Path) -> None:
        _write_yaml(
            tmp_path,
            "good.yaml",
            """\
            domain: test
            version: "1.0"
            rules: []
        """,
        )
        (tmp_path / "readme.txt").write_text("not a yaml file")
        (tmp_path / "data.json").write_text("{}")
        rulesets = load_rules_from_directory(tmp_path)
        assert len(rulesets) == 1


# ===========================================================================
# Conversion Tests
# ===========================================================================


class TestConversion:
    def test_converts_to_constraints(self) -> None:
        rs = YamlRuleSet(
            domain="mechanical",
            version="1.0",
            rules=[
                YamlRule(
                    name="stress_check",
                    description="Check stress",
                    severity="critical",
                    condition="stress < 100",
                    message_template="Stress too high",
                    remediation_hint="Add material",
                    tags=["fea"],
                ),
            ],
        )
        constraints = convert_to_constraints(rs)
        assert len(constraints) == 1
        c = constraints[0]
        assert isinstance(c, Constraint)
        assert c.name == "stress_check"
        assert c.expression == "stress < 100"
        assert c.severity == ConstraintSeverity.ERROR  # "critical" -> ERROR
        assert c.domain == "mechanical"
        assert c.cross_domain is False
        assert c.source == "yaml:mechanical/v1.0"
        assert c.message == "Stress too high"
        assert c.metadata["remediation_hint"] == "Add material"
        assert c.metadata["tags"] == ["fea"]

    def test_warning_severity_mapping(self) -> None:
        rs = YamlRuleSet(
            domain="test",
            version="1.0",
            rules=[
                YamlRule(name="w", description="Warn", severity="warning", condition="True"),
            ],
        )
        constraints = convert_to_constraints(rs)
        assert constraints[0].severity == ConstraintSeverity.WARNING

    def test_info_severity_mapping(self) -> None:
        rs = YamlRuleSet(
            domain="test",
            version="1.0",
            rules=[
                YamlRule(name="i", description="Info", severity="info", condition="True"),
            ],
        )
        constraints = convert_to_constraints(rs)
        assert constraints[0].severity == ConstraintSeverity.INFO

    def test_cross_domain_flag(self) -> None:
        rs = YamlRuleSet(
            domain="cross_domain",
            version="1.0",
            rules=[
                YamlRule(
                    name="cd",
                    description="Cross domain",
                    severity="critical",
                    condition="True",
                ),
            ],
        )
        constraints = convert_to_constraints(rs)
        assert constraints[0].cross_domain is True

    def test_message_falls_back_to_description(self) -> None:
        rs = YamlRuleSet(
            domain="test",
            version="1.0",
            rules=[
                YamlRule(
                    name="no_template",
                    description="Fallback description",
                    severity="warning",
                    condition="True",
                ),
            ],
        )
        constraints = convert_to_constraints(rs)
        assert constraints[0].message == "Fallback description"

    def test_load_and_convert_directory(self, tmp_path: Path) -> None:
        _write_yaml(
            tmp_path,
            "a.yaml",
            """\
            domain: mechanical
            version: "1.0"
            rules:
              - name: r1
                description: "Rule 1"
                severity: critical
                condition: "True"
              - name: r2
                description: "Rule 2"
                severity: warning
                condition: "True"
        """,
        )
        _write_yaml(
            tmp_path,
            "b.yaml",
            """\
            domain: electrical
            version: "1.0"
            rules:
              - name: r3
                description: "Rule 3"
                severity: info
                condition: "True"
        """,
        )
        constraints = load_and_convert_directory(tmp_path)
        assert len(constraints) == 3
        names = {c.name for c in constraints}
        assert names == {"r1", "r2", "r3"}


# ===========================================================================
# Rule Evaluation Tests (using simple variable bindings)
# ===========================================================================


class TestMechanicalRuleEvaluation:
    def test_stress_below_yield_pass(self) -> None:
        status, _ = _eval_simple(
            "stress_mpa < yield_strength_mpa * 0.85",
            {"stress_mpa": 45.0, "yield_strength_mpa": 276.0},
        )
        assert status == ConstraintStatus.PASS

    def test_stress_below_yield_fail(self) -> None:
        # 300 MPa exceeds 85% of 276 (= 234.6)
        status, _ = _eval_simple(
            "stress_mpa < yield_strength_mpa * 0.85",
            {"stress_mpa": 300.0, "yield_strength_mpa": 276.0},
        )
        assert status == ConstraintStatus.FAIL

    def test_minimum_wall_thickness_pass(self) -> None:
        status, _ = _eval_simple(
            "wall_thickness_mm >= 1.0",
            {"wall_thickness_mm": 1.5},
        )
        assert status == ConstraintStatus.PASS

    def test_minimum_wall_thickness_fail(self) -> None:
        status, _ = _eval_simple(
            "wall_thickness_mm >= 1.0",
            {"wall_thickness_mm": 0.6},
        )
        assert status == ConstraintStatus.FAIL

    def test_safety_factor_pass(self) -> None:
        status, _ = _eval_simple(
            "safety_factor >= 2.0",
            {"safety_factor": 3.5},
        )
        assert status == ConstraintStatus.PASS

    def test_safety_factor_fail(self) -> None:
        status, _ = _eval_simple(
            "safety_factor >= 2.0",
            {"safety_factor": 1.5},
        )
        assert status == ConstraintStatus.FAIL


class TestElectricalRuleEvaluation:
    def test_power_budget_pass(self) -> None:
        status, _ = _eval_simple(
            "total_power_draw_w <= supply_power_w",
            {"total_power_draw_w": 3.5, "supply_power_w": 5.0},
        )
        assert status == ConstraintStatus.PASS

    def test_power_budget_fail(self) -> None:
        status, _ = _eval_simple(
            "total_power_draw_w <= supply_power_w",
            {"total_power_draw_w": 6.0, "supply_power_w": 5.0},
        )
        assert status == ConstraintStatus.FAIL

    def test_voltage_margin_pass(self) -> None:
        status, _ = _eval_simple(
            "operating_voltage_v <= rated_voltage_v * 0.9",
            {"operating_voltage_v": 4.5, "rated_voltage_v": 5.5},
        )
        assert status == ConstraintStatus.PASS

    def test_voltage_margin_fail(self) -> None:
        status, _ = _eval_simple(
            "operating_voltage_v <= rated_voltage_v * 0.9",
            {"operating_voltage_v": 5.0, "rated_voltage_v": 5.5},
        )
        assert status == ConstraintStatus.FAIL

    def test_current_below_limit_fail(self) -> None:
        status, _ = _eval_simple(
            "operating_current_a <= rated_current_a",
            {"operating_current_a": 2.5, "rated_current_a": 2.0},
        )
        assert status == ConstraintStatus.FAIL


class TestFirmwareRuleEvaluation:
    def test_flash_usage_pass(self) -> None:
        status, _ = _eval_simple(
            "flash_used_bytes < flash_total_bytes * 0.9",
            {"flash_used_bytes": 200_000, "flash_total_bytes": 512_000},
        )
        assert status == ConstraintStatus.PASS

    def test_flash_usage_fail(self) -> None:
        status, _ = _eval_simple(
            "flash_used_bytes < flash_total_bytes * 0.9",
            {"flash_used_bytes": 480_000, "flash_total_bytes": 512_000},
        )
        assert status == ConstraintStatus.FAIL

    def test_ram_usage_pass(self) -> None:
        status, _ = _eval_simple(
            "ram_used_bytes < ram_total_bytes * 0.8",
            {"ram_used_bytes": 40_000, "ram_total_bytes": 128_000},
        )
        assert status == ConstraintStatus.PASS

    def test_stack_size_fail(self) -> None:
        status, _ = _eval_simple(
            "stack_size_bytes >= worst_case_stack_bytes * 1.25",
            {"stack_size_bytes": 2048, "worst_case_stack_bytes": 2000},
        )
        assert status == ConstraintStatus.FAIL


class TestCrossDomainRuleEvaluation:
    def test_pcb_fits_enclosure_pass(self) -> None:
        status, _ = _eval_simple(
            "pcb_width_mm + 2 * clearance_mm <= enclosure_inner_width_mm",
            {"pcb_width_mm": 50.0, "clearance_mm": 2.0, "enclosure_inner_width_mm": 60.0},
        )
        assert status == ConstraintStatus.PASS

    def test_pcb_fits_enclosure_fail(self) -> None:
        status, _ = _eval_simple(
            "pcb_width_mm + 2 * clearance_mm <= enclosure_inner_width_mm",
            {"pcb_width_mm": 58.0, "clearance_mm": 2.0, "enclosure_inner_width_mm": 60.0},
        )
        assert status == ConstraintStatus.FAIL

    def test_thermal_budget_fail(self) -> None:
        status, _ = _eval_simple(
            "total_heat_dissipation_w <= thermal_capacity_w",
            {"total_heat_dissipation_w": 15.0, "thermal_capacity_w": 10.0},
        )
        assert status == ConstraintStatus.FAIL

    def test_weight_budget_pass(self) -> None:
        status, _ = _eval_simple(
            "total_weight_g <= weight_budget_g",
            {"total_weight_g": 150.0, "weight_budget_g": 200.0},
        )
        assert status == ConstraintStatus.PASS


# ===========================================================================
# Invalid YAML -> clear error, not crash
# ===========================================================================


class TestErrorHandling:
    def test_corrupt_yaml_clear_error(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "corrupt.yaml",
            """\
            domain: test
            rules:
              - {name: "r1", description: "d", severity: "critical", condition: "
        """,
        )
        with pytest.raises(YamlRuleLoadError):
            load_rules_from_file(path)

    def test_missing_required_fields_clear_error(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "missing.yaml",
            """\
            domain: test
            rules:
              - name: "r1"
        """,
        )
        with pytest.raises(YamlRuleLoadError, match="validation failed"):
            load_rules_from_file(path)


# ===========================================================================
# Multi-rule aggregation
# ===========================================================================


class TestMultiRuleAggregation:
    def test_multi_rule_report(self) -> None:
        rules = [
            (
                "stress_mpa < yield_strength_mpa * 0.85",
                {"stress_mpa": 45, "yield_strength_mpa": 276},
            ),  # PASS
            ("wall_thickness_mm >= 1.0", {"wall_thickness_mm": 0.5}),  # FAIL
            ("safety_factor >= 2.0", {"safety_factor": 3.0}),  # PASS
            (
                "total_power_draw_w <= supply_power_w",
                {"total_power_draw_w": 6, "supply_power_w": 5},
            ),  # FAIL
        ]
        results = [_eval_simple(expr, vars_) for expr, vars_ in rules]
        pass_count = sum(1 for s, _ in results if s == ConstraintStatus.PASS)
        fail_count = sum(1 for s, _ in results if s == ConstraintStatus.FAIL)
        assert pass_count == 2
        assert fail_count == 2

    def test_all_pass_aggregation(self) -> None:
        rules = [
            ("x > 0", {"x": 1}),
            ("y > 0", {"y": 2}),
            ("z > 0", {"z": 3}),
        ]
        results = [_eval_simple(expr, vars_) for expr, vars_ in rules]
        assert all(s == ConstraintStatus.PASS for s, _ in results)

    def test_all_fail_aggregation(self) -> None:
        rules = [
            ("x > 10", {"x": 1}),
            ("y > 10", {"y": 2}),
        ]
        results = [_eval_simple(expr, vars_) for expr, vars_ in rules]
        assert all(s == ConstraintStatus.FAIL for s, _ in results)


# ===========================================================================
# Bundled Rule Files Validation
# ===========================================================================


class TestBundledRuleFiles:
    """Verify that the shipped YAML rule files parse and convert correctly."""

    def test_mechanical_rules_load(self) -> None:
        rs = load_rules_from_file(_RULES_DIR / "mechanical.yaml")
        assert rs.domain == "mechanical"
        assert len(rs.rules) >= 8
        constraints = convert_to_constraints(rs)
        assert len(constraints) == len(rs.rules)

    def test_electrical_rules_load(self) -> None:
        rs = load_rules_from_file(_RULES_DIR / "electrical.yaml")
        assert rs.domain == "electrical"
        assert len(rs.rules) >= 8
        constraints = convert_to_constraints(rs)
        assert len(constraints) == len(rs.rules)

    def test_firmware_rules_load(self) -> None:
        rs = load_rules_from_file(_RULES_DIR / "firmware.yaml")
        assert rs.domain == "firmware"
        assert len(rs.rules) >= 8
        constraints = convert_to_constraints(rs)
        assert len(constraints) == len(rs.rules)

    def test_cross_domain_rules_load(self) -> None:
        rs = load_rules_from_file(_RULES_DIR / "cross_domain.yaml")
        assert rs.domain == "cross_domain"
        assert len(rs.rules) >= 5
        constraints = convert_to_constraints(rs)
        assert all(c.cross_domain is True for c in constraints)

    def test_all_bundled_rules_directory(self) -> None:
        rulesets = load_rules_from_directory(_RULES_DIR)
        # Should load at least the 4 domain files
        assert len(rulesets) >= 4
        all_constraints = []
        for rs in rulesets:
            all_constraints.extend(convert_to_constraints(rs))
        # At least 29 rules across all files (8+8+8+6 = 30 minimum minus possible)
        assert len(all_constraints) >= 25
