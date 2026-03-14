"""Tests for cross-domain constraint validation (MET-35)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.constraint_engine.cross_domain import (
    CrossDomainCheck,
    CrossDomainValidator,
)
from twin_core.models import WorkProduct, WorkProductType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pcb(
    width: float = 50.0,
    height: float = 30.0,
    mounting_holes: list | None = None,
    thermal_zones: list | None = None,
    connectors: list | None = None,
) -> WorkProduct:
    """Create a PCB work_product with dimensional metadata."""
    meta: dict = {
        "subtype": "pcb",
        "dimensions": {"width": width, "height": height},
    }
    if mounting_holes is not None:
        meta["mounting_holes"] = mounting_holes
    if thermal_zones is not None:
        meta["thermal_zones"] = thermal_zones
    if connectors is not None:
        meta["connectors"] = connectors
    return WorkProduct(
        name="main_pcb",
        type=WorkProductType.PCB_LAYOUT,
        domain="electronics",
        file_path="eda/kicad/main.kicad_pcb",
        content_hash="pcbhash",
        format="kicad_pcb",
        created_by="human",
        metadata=meta,
    )


def _make_enclosure(
    width: float = 60.0,
    height: float = 40.0,
    internal_clearance: float = 2.0,
    mounting_standoffs: list | None = None,
    thermal_restricted_zones: list | None = None,
    cutouts: list | None = None,
    mounting_tolerance: float = 0.5,
    min_connector_clearance: float = 0.5,
) -> WorkProduct:
    """Create an enclosure work_product with dimensional metadata."""
    meta: dict = {
        "subtype": "enclosure",
        "dimensions": {"width": width, "height": height},
        "internal_clearance": internal_clearance,
        "mounting_tolerance": mounting_tolerance,
        "min_connector_clearance": min_connector_clearance,
    }
    if mounting_standoffs is not None:
        meta["mounting_standoffs"] = mounting_standoffs
    if thermal_restricted_zones is not None:
        meta["thermal_restricted_zones"] = thermal_restricted_zones
    if cutouts is not None:
        meta["cutouts"] = cutouts
    return WorkProduct(
        name="enclosure",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="cad/enclosure.step",
        content_hash="enchash",
        format="step",
        created_by="human",
        metadata=meta,
    )


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def validator(twin):
    return CrossDomainValidator(twin)


# ---------------------------------------------------------------------------
# CrossDomainCheck model tests
# ---------------------------------------------------------------------------


class TestCrossDomainCheck:
    def test_creation(self):
        check = CrossDomainCheck(
            name="test_check",
            domain_a="mechanical",
            domain_b="electronics",
            passed=True,
            message="All good",
        )
        assert check.name == "test_check"
        assert check.domain_a == "mechanical"
        assert check.domain_b == "electronics"
        assert check.passed is True
        assert check.severity == "error"  # default
        assert check.details == {}

    def test_severity_levels(self):
        for sev in ["error", "warning", "info"]:
            check = CrossDomainCheck(
                name="test",
                domain_a="a",
                domain_b="b",
                passed=False,
                message="msg",
                severity=sev,
            )
            assert check.severity == sev

    def test_details_dict(self):
        check = CrossDomainCheck(
            name="test",
            domain_a="a",
            domain_b="b",
            passed=True,
            message="ok",
            details={"key": "value", "num": 42},
        )
        assert check.details["key"] == "value"
        assert check.details["num"] == 42


# ---------------------------------------------------------------------------
# CrossDomainValidator initialization tests
# ---------------------------------------------------------------------------


class TestCrossDomainValidatorInit:
    def test_default_checks_registered(self, validator):
        """Validator should have 4 default checks."""
        assert len(validator._checks) == 4

    def test_register_custom_check(self, validator):
        """Custom check should be added to the list."""

        async def custom_check(work_product_id, branch):
            return CrossDomainCheck(
                name="custom",
                domain_a="a",
                domain_b="b",
                passed=True,
                message="custom ok",
            )

        validator.register_check(custom_check)
        assert len(validator._checks) == 5
        assert validator._checks[-1] is custom_check


# ---------------------------------------------------------------------------
# validate_all tests
# ---------------------------------------------------------------------------


class TestValidateAll:
    async def test_runs_all_checks(self, twin, validator):
        """validate_all should return results from all registered checks."""
        pcb = _make_pcb()
        enclosure = _make_enclosure()
        await twin.create_work_product(pcb)
        await twin.create_work_product(enclosure)

        results = await validator.validate_all(pcb.id)
        assert len(results) == 4
        assert all(isinstance(r, CrossDomainCheck) for r in results)

    async def test_handles_check_exception(self, twin, validator):
        """If a check raises, it should be caught and reported as failed."""

        async def broken_check(work_product_id, branch):
            raise ValueError("Something went wrong")

        validator._checks = [broken_check]
        results = await validator.validate_all(uuid4())
        assert len(results) == 1
        assert results[0].passed is False
        assert "Something went wrong" in results[0].message
        assert results[0].domain_a == "unknown"

    async def test_with_no_artifacts(self, twin, validator):
        """Checks should pass (skip) when no work_products exist."""
        results = await validator.validate_all(uuid4())
        assert len(results) == 4
        # All should pass with "skipping" messages since no work_products found
        assert all(r.passed is True for r in results)


# ---------------------------------------------------------------------------
# PCB Enclosure Fit tests
# ---------------------------------------------------------------------------


class TestPcbEnclosureFit:
    async def test_pcb_fits(self, twin, validator):
        """PCB smaller than enclosure interior should pass."""
        pcb = _make_pcb(width=50.0, height=30.0)
        enc = _make_enclosure(width=60.0, height=40.0, internal_clearance=2.0)
        # Available: 56x36 — PCB 50x30 fits
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(pcb.id, "main")
        assert result.passed is True
        assert result.name == "check_pcb_enclosure_fit"
        assert result.domain_a == "electronics"
        assert result.domain_b == "mechanical"

    async def test_pcb_too_wide(self, twin, validator):
        """PCB wider than enclosure interior should fail."""
        pcb = _make_pcb(width=60.0, height=30.0)
        enc = _make_enclosure(width=60.0, height=40.0, internal_clearance=2.0)
        # Available: 56x36 — PCB width 60 > 56
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(pcb.id, "main")
        assert result.passed is False
        assert "width" in result.message.lower()
        assert result.severity == "error"

    async def test_pcb_too_tall(self, twin, validator):
        """PCB taller than enclosure interior should fail."""
        pcb = _make_pcb(width=40.0, height=50.0)
        enc = _make_enclosure(width=60.0, height=40.0, internal_clearance=2.0)
        # Available: 56x36 — PCB height 50 > 36
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(pcb.id, "main")
        assert result.passed is False
        assert "height" in result.message.lower()

    async def test_missing_pcb_artifact(self, twin, validator):
        """Missing PCB should skip with info severity."""
        enc = _make_enclosure()
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(uuid4(), "main")
        assert result.passed is True
        assert result.severity == "info"
        assert "skipping" in result.message.lower()

    async def test_exact_fit(self, twin, validator):
        """PCB exactly matching available space should pass."""
        pcb = _make_pcb(width=56.0, height=36.0)
        enc = _make_enclosure(width=60.0, height=40.0, internal_clearance=2.0)
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(pcb.id, "main")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Mounting Hole Alignment tests
# ---------------------------------------------------------------------------


class TestMountingHoleAlignment:
    async def test_aligned_holes(self, twin, validator):
        """Holes matching standoff positions should pass."""
        pcb = _make_pcb(
            mounting_holes=[
                {"x": 5.0, "y": 5.0},
                {"x": 45.0, "y": 5.0},
                {"x": 5.0, "y": 25.0},
                {"x": 45.0, "y": 25.0},
            ]
        )
        enc = _make_enclosure(
            mounting_standoffs=[
                {"x": 5.0, "y": 5.0},
                {"x": 45.0, "y": 5.0},
                {"x": 5.0, "y": 25.0},
                {"x": 45.0, "y": 25.0},
            ]
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_mounting_hole_alignment(pcb.id, "main")
        assert result.passed is True
        assert result.details["matched"] == 4

    async def test_misaligned_holes(self, twin, validator):
        """Holes not matching any standoff should fail."""
        pcb = _make_pcb(
            mounting_holes=[
                {"x": 5.0, "y": 5.0},
                {"x": 50.0, "y": 50.0},  # no standoff nearby
            ]
        )
        enc = _make_enclosure(
            mounting_standoffs=[
                {"x": 5.0, "y": 5.0},
                {"x": 45.0, "y": 25.0},
            ]
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_mounting_hole_alignment(pcb.id, "main")
        assert result.passed is False
        assert len(result.details["misaligned"]) == 1
        assert result.severity == "error"

    async def test_no_mounting_holes(self, twin, validator):
        """No holes defined should skip check."""
        pcb = _make_pcb(mounting_holes=[])
        enc = _make_enclosure(mounting_standoffs=[{"x": 5.0, "y": 5.0}])
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_mounting_hole_alignment(pcb.id, "main")
        assert result.passed is True
        assert result.severity == "info"

    async def test_holes_within_tolerance(self, twin, validator):
        """Holes slightly off but within tolerance should pass."""
        pcb = _make_pcb(mounting_holes=[{"x": 5.3, "y": 5.2}])
        enc = _make_enclosure(
            mounting_standoffs=[{"x": 5.0, "y": 5.0}],
            mounting_tolerance=0.5,
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_mounting_hole_alignment(pcb.id, "main")
        assert result.passed is True

    async def test_holes_outside_tolerance(self, twin, validator):
        """Holes just outside tolerance should fail."""
        pcb = _make_pcb(mounting_holes=[{"x": 6.0, "y": 5.0}])
        enc = _make_enclosure(
            mounting_standoffs=[{"x": 5.0, "y": 5.0}],
            mounting_tolerance=0.5,
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_mounting_hole_alignment(pcb.id, "main")
        assert result.passed is False


# ---------------------------------------------------------------------------
# Thermal Zone tests
# ---------------------------------------------------------------------------


class TestThermalZones:
    async def test_no_conflicts(self, twin, validator):
        """Non-overlapping zones should pass."""
        pcb = _make_pcb(
            thermal_zones=[
                {"name": "vreg", "x": 10.0, "y": 10.0, "radius": 5.0, "max_temperature": 85.0}
            ]
        )
        enc = _make_enclosure(
            thermal_restricted_zones=[
                {
                    "name": "battery_bay",
                    "x": 40.0,
                    "y": 30.0,
                    "radius": 5.0,
                    "max_allowed_temperature": 45.0,
                }
            ]
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_thermal_zones(pcb.id, "main")
        assert result.passed is True

    async def test_overlapping_hot_zone_conflict(self, twin, validator):
        """Overlapping hot zone exceeding restricted temp should fail."""
        pcb = _make_pcb(
            thermal_zones=[
                {"name": "vreg", "x": 10.0, "y": 10.0, "radius": 8.0, "max_temperature": 95.0}
            ]
        )
        enc = _make_enclosure(
            thermal_restricted_zones=[
                {
                    "name": "plastic_wall",
                    "x": 15.0,
                    "y": 10.0,
                    "radius": 5.0,
                    "max_allowed_temperature": 60.0,
                }
            ]
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_thermal_zones(pcb.id, "main")
        assert result.passed is False
        assert result.severity == "warning"
        assert len(result.details["conflicts"]) == 1

    async def test_overlapping_but_within_temp_limit(self, twin, validator):
        """Overlapping zones where temp is within limit should pass."""
        pcb = _make_pcb(
            thermal_zones=[
                {"name": "low_power", "x": 10.0, "y": 10.0, "radius": 8.0, "max_temperature": 40.0}
            ]
        )
        enc = _make_enclosure(
            thermal_restricted_zones=[
                {
                    "name": "plastic_wall",
                    "x": 15.0,
                    "y": 10.0,
                    "radius": 5.0,
                    "max_allowed_temperature": 60.0,
                }
            ]
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_thermal_zones(pcb.id, "main")
        assert result.passed is True

    async def test_no_thermal_zones_defined(self, twin, validator):
        """No thermal zones should skip check."""
        pcb = _make_pcb()
        enc = _make_enclosure()
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_thermal_zones(pcb.id, "main")
        assert result.passed is True
        assert result.severity == "info"


# ---------------------------------------------------------------------------
# Connector Clearance tests
# ---------------------------------------------------------------------------


class TestConnectorClearances:
    async def test_adequate_clearance(self, twin, validator):
        """Connectors with sufficient cutout clearance should pass."""
        pcb = _make_pcb(
            connectors=[{"name": "usb_c", "x": 25.0, "y": 0.0, "width": 9.0, "height": 3.2}]
        )
        enc = _make_enclosure(
            cutouts=[{"connector_name": "usb_c", "width": 12.0, "height": 5.0}],
            min_connector_clearance=0.5,
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_connector_clearances(pcb.id, "main")
        assert result.passed is True

    async def test_insufficient_clearance(self, twin, validator):
        """Connector cutout too tight should fail."""
        pcb = _make_pcb(
            connectors=[{"name": "usb_c", "x": 25.0, "y": 0.0, "width": 9.0, "height": 3.2}]
        )
        enc = _make_enclosure(
            cutouts=[{"connector_name": "usb_c", "width": 9.5, "height": 3.5}],
            min_connector_clearance=0.5,
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_connector_clearances(pcb.id, "main")
        assert result.passed is False
        assert result.severity == "error"

    async def test_missing_cutout(self, twin, validator):
        """Connector without matching cutout should fail."""
        pcb = _make_pcb(
            connectors=[{"name": "hdmi", "x": 10.0, "y": 0.0, "width": 15.0, "height": 5.5}]
        )
        enc = _make_enclosure(
            cutouts=[{"connector_name": "usb_c", "width": 12.0, "height": 5.0}],
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_connector_clearances(pcb.id, "main")
        assert result.passed is False
        assert any(i["issue"] == "no_cutout" for i in result.details["issues"])

    async def test_no_connectors(self, twin, validator):
        """No connectors defined should skip check."""
        pcb = _make_pcb(connectors=[])
        enc = _make_enclosure()
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_connector_clearances(pcb.id, "main")
        assert result.passed is True
        assert result.severity == "info"

    async def test_multiple_connectors_mixed(self, twin, validator):
        """Mix of passing and failing connectors should fail overall."""
        pcb = _make_pcb(
            connectors=[
                {"name": "usb_c", "x": 25.0, "y": 0.0, "width": 9.0, "height": 3.2},
                {"name": "jtag", "x": 40.0, "y": 0.0, "width": 10.0, "height": 4.0},
            ]
        )
        enc = _make_enclosure(
            cutouts=[
                {"connector_name": "usb_c", "width": 12.0, "height": 5.0},  # good
                {"connector_name": "jtag", "width": 10.2, "height": 4.2},  # too tight
            ],
            min_connector_clearance=0.5,
        )
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_connector_clearances(pcb.id, "main")
        assert result.passed is False


# ---------------------------------------------------------------------------
# Missing work_products tests
# ---------------------------------------------------------------------------


class TestMissingArtifacts:
    async def test_pcb_enclosure_fit_no_artifacts(self, twin, validator):
        result = await validator.check_pcb_enclosure_fit(uuid4(), "main")
        assert result.passed is True
        assert result.severity == "info"

    async def test_mounting_holes_no_artifacts(self, twin, validator):
        result = await validator.check_mounting_hole_alignment(uuid4(), "main")
        assert result.passed is True
        assert result.severity == "info"

    async def test_thermal_zones_no_artifacts(self, twin, validator):
        result = await validator.check_thermal_zones(uuid4(), "main")
        assert result.passed is True
        assert result.severity == "info"

    async def test_connector_clearances_no_artifacts(self, twin, validator):
        result = await validator.check_connector_clearances(uuid4(), "main")
        assert result.passed is True
        assert result.severity == "info"
