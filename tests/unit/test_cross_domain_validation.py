"""Tests for cross-domain constraint validation (MET-35)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.constraint_engine.cross_domain import (
    CheckStatus,
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
            status=CheckStatus.PASS,
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
                status=CheckStatus.FAIL,
                message="msg",
                severity=sev,
            )
            assert check.severity == sev

    def test_details_dict(self):
        check = CrossDomainCheck(
            name="test",
            domain_a="a",
            domain_b="b",
            status=CheckStatus.PASS,
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
                status=CheckStatus.PASS,
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
        """A project with nothing in it reports four checks that could not run."""
        results = await validator.validate_all(uuid4())
        assert len(results) == 4
        # FORGE-361: this used to assert four passes. An empty project
        # scoring a clean cross-domain sweep is the exact thing F3 forbids —
        # missing data shown as "pass".
        assert all(r.status is CheckStatus.NO_DATA for r in results)
        assert not any(r.passed for r in results)


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
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
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
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
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
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
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
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
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
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
        assert result.severity == "info"

    async def test_mounting_holes_no_artifacts(self, twin, validator):
        result = await validator.check_mounting_hole_alignment(uuid4(), "main")
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
        assert result.severity == "info"

    async def test_thermal_zones_no_artifacts(self, twin, validator):
        result = await validator.check_thermal_zones(uuid4(), "main")
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
        assert result.severity == "info"

    async def test_connector_clearances_no_artifacts(self, twin, validator):
        result = await validator.check_connector_clearances(uuid4(), "main")
        # FORGE-361: the docstring always said 'skip'. It now says so in
        # the type instead of borrowing 'pass' to mean it.
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
        assert result.severity == "info"


class TestNoDataIsNotAPass:
    """F3, the cases that computed a verdict from absence (FORGE-361).

    A skip that reports success is the failure mode: a gate reading
    ``passed`` cannot tell "this check was satisfied" from "this check never
    ran", and the project with nothing recorded is the one that looks
    cleanest.
    """

    @pytest.mark.asyncio
    async def test_a_pcb_with_no_dimensions_does_not_fit_everything(self, twin, validator):
        # Was: width defaulted to 0.0, so a dimensionless PCB fitted inside
        # any enclosure and the message read "PCB (0.0x0.0mm) fits".
        pcb = _make_pcb()
        pcb.metadata["dimensions"] = {}
        enc = _make_enclosure(width=60.0, height=40.0)
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(pcb.id, "main")
        assert result.status is CheckStatus.NO_DATA
        assert result.passed is False
        assert "pcb.width" in result.details["missing"]

    @pytest.mark.asyncio
    async def test_a_missing_dimension_reads_the_same_on_either_side(self, twin, validator):
        # The asymmetry this replaces: a missing PCB dimension passed
        # (0 <= available) while a missing enclosure dimension failed
        # (x <= -0.0). The same absence, opposite verdicts, depending only
        # on which artifact it was missing from.
        pcb_blank = _make_pcb()
        pcb_blank.metadata["dimensions"] = {}
        enc_ok = _make_enclosure(width=60.0, height=40.0)
        await twin.create_work_product(pcb_blank)
        await twin.create_work_product(enc_ok)
        missing_pcb = await validator.check_pcb_enclosure_fit(pcb_blank.id, "main")

        twin2_pcb = _make_pcb(width=50.0, height=30.0)
        enc_blank = _make_enclosure()
        enc_blank.metadata["dimensions"] = {}
        await twin.create_work_product(twin2_pcb)
        await twin.create_work_product(enc_blank)
        missing_enc = await validator.check_pcb_enclosure_fit(twin2_pcb.id, "main")

        assert missing_pcb.status is missing_enc.status is CheckStatus.NO_DATA

    @pytest.mark.asyncio
    async def test_the_missing_fields_are_named(self, twin, validator):
        # "no data" is only useful if it says which data.
        pcb = _make_pcb()
        pcb.metadata["dimensions"] = {"width": 50.0}
        enc = _make_enclosure(width=60.0, height=40.0)
        await twin.create_work_product(pcb)
        await twin.create_work_product(enc)

        result = await validator.check_pcb_enclosure_fit(pcb.id, "main")
        assert result.details["missing"] == ["pcb.height"]
        assert "pcb.height" in result.message


class TestPassedProperty:
    def test_only_a_real_pass_is_passed(self):
        def _check(status):
            return CrossDomainCheck(
                name="x", domain_a="a", domain_b="b", status=status, message="m"
            )

        assert _check(CheckStatus.PASS).passed is True
        assert _check(CheckStatus.FAIL).passed is False
        # The whole point: an existing `if not check.passed` caller treats
        # no-data as not-satisfied rather than as success.
        assert _check(CheckStatus.NO_DATA).passed is False

    def test_ran_separates_could_not_from_did_not(self):
        def _check(status):
            return CrossDomainCheck(
                name="x", domain_a="a", domain_b="b", status=status, message="m"
            )

        assert _check(CheckStatus.FAIL).ran is True
        assert _check(CheckStatus.NO_DATA).ran is False
