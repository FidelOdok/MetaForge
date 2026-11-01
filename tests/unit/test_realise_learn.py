"""Unit tests for FORGE-321 (Realise + Learn): DeviceInstance CRUD, the
calibration band model, device registration, measurement recording, and
the calibrated-band override in twin.evaluate_metric."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin.calibration import make_calibrated_band_lookup, make_calibration_recorder
from api_gateway.twin.device_instance_recorder import make_device_instance_registrar
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.measurement_recorder import make_measurement_recorder
from api_gateway.twin.metric_evaluator import make_metric_evaluator
from digital_twin.calibration.store import (
    MIN_SAMPLES_FOR_CALIBRATION,
    compute_calibrated_band,
)
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.device_instance import DeviceInstance
from twin_core.models.enums import EdgeType, NodeType, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _seed_cad(twin, project_id, name="upper_arm") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            project_id=project_id,
            metadata={
                "geometry_features": {
                    "properties": {
                        "bounding_box": {
                            "min_x": -180,
                            "max_x": 180,
                            "min_y": -20,
                            "max_y": 20,
                            "min_z": -30,
                            "max_z": 30,
                        }
                    }
                }
            },
        )
    )


async def _seed_system_architecture(
    twin, project_id, *, predicted: dict | None = None, name="Arm structural interfaces"
) -> WorkProduct:
    quantity = {
        "metric": "tip_deflection",
        "unit": "mm",
        "limit": 0.5,
        "op": "<=",
        "owner": "mechanical",
        "discipline": "mechanical",
        "predicted": predicted,
        "measured": [],
    }
    interfaces = [
        {
            "from": "upper_arm",
            "to": "shoulder",
            "interface_type": "mechanical",
            "description": "",
            "quantities": [quantity],
        }
    ]
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.SYSTEM_ARCHITECTURE,
            domain="systems",
            file_path="",
            content_hash="deadbeef",
            format="md",
            created_by="test",
            project_id=project_id,
            metadata={"interfaces": interfaces},
        )
    )


class TestDeviceInstanceCrud:
    async def test_create_and_get_round_trips(self, twin, project_id):
        instance = DeviceInstance(
            serial_number="SN-001",
            product_id="arm-v1",
            project_id=project_id,
            global_asset_id="urn:metaforge:device:SN-001",
        )
        created = await twin.create_device_instance(instance)
        fetched = await twin.get_device_instance(created.id)
        assert fetched is not None
        assert fetched.node_type == NodeType.DEVICE_INSTANCE
        assert fetched.serial_number == "SN-001"
        assert fetched.product_id == "arm-v1"

    async def test_get_unknown_returns_none(self, twin):
        assert await twin.get_device_instance(uuid4()) is None

    async def test_list_filters_by_project_and_product(self, twin, project_id):
        other_project = uuid4()
        await twin.create_device_instance(
            DeviceInstance(serial_number="A", product_id="arm-v1", project_id=project_id)
        )
        await twin.create_device_instance(
            DeviceInstance(serial_number="B", product_id="arm-v2", project_id=project_id)
        )
        await twin.create_device_instance(
            DeviceInstance(serial_number="C", product_id="arm-v1", project_id=other_project)
        )

        scoped = await twin.list_device_instances(project_id=project_id)
        assert {d.serial_number for d in scoped} == {"A", "B"}

        by_product = await twin.list_device_instances(project_id=project_id, product_id="arm-v1")
        assert {d.serial_number for d in by_product} == {"A"}


class TestCalibratedBandMath:
    def test_too_few_samples_returns_none(self):
        residuals = [0.01] * (MIN_SAMPLES_FOR_CALIBRATION - 1)
        assert compute_calibrated_band(residuals, metric="tip_deflection", tier=0) is None

    def test_enough_samples_returns_real_band(self):
        residuals = [0.01, -0.02, 0.015, -0.01, 0.02]
        band = compute_calibrated_band(residuals, metric="tip_deflection", tier=0)
        assert band is not None
        assert band.sample_count == 5
        assert band.band > 0

    def test_consistent_residuals_produce_a_very_tight_band(self):
        residuals = [0.05, 0.05, 0.05]
        band = compute_calibrated_band(residuals, metric="tip_deflection", tier=0)
        assert band is not None
        assert band.stddev_abs_residual == pytest.approx(0.0, abs=1e-9)
        assert band.band == pytest.approx(0.0, abs=1e-9)

    def test_more_samples_can_narrow_the_band(self):
        noisy = compute_calibrated_band([0.1, -0.3, 0.25, -0.15], metric="m", tier=0)
        tight = compute_calibrated_band([0.01, -0.02, 0.015, -0.01], metric="m", tier=0)
        assert noisy is not None
        assert tight is not None
        assert tight.band < noisy.band


class TestCalibrationRecorderAndLookup:
    async def test_record_and_lookup_roundtrip(self, twin, project_id):
        evidence_recorder = make_evidence_recorder(twin)
        record_residual = make_calibration_recorder(twin, evidence_recorder=evidence_recorder)
        lookup = make_calibrated_band_lookup(twin)

        assert await lookup(metric="tip_deflection", tier=0) is None

        for predicted, measured in [(0.4, 0.41), (0.4, 0.39), (0.4, 0.42), (0.4, 0.38)]:
            out = await record_residual(
                metric="tip_deflection",
                tier=0,
                predicted=predicted,
                measured=measured,
                project_id=str(project_id),
            )
            assert "evidence_node_id" in out

        band = await lookup(metric="tip_deflection", tier=0)
        assert band is not None
        assert band.sample_count == 4

    async def test_lookup_ignores_unrelated_metrics(self, twin):
        evidence_recorder = make_evidence_recorder(twin)
        record_residual = make_calibration_recorder(twin, evidence_recorder=evidence_recorder)
        for _ in range(5):
            await record_residual(metric="mass", tier=0, predicted=1.0, measured=1.01)

        lookup = make_calibrated_band_lookup(twin)
        assert await lookup(metric="tip_deflection", tier=0) is None

    async def test_lookup_is_cross_project(self, twin, project_id):
        """Calibration is a prior for the NEXT project, not a per-project cache."""
        evidence_recorder = make_evidence_recorder(twin)
        record_residual = make_calibration_recorder(twin, evidence_recorder=evidence_recorder)
        for predicted, measured in [(0.4, 0.41), (0.4, 0.39), (0.4, 0.42)]:
            await record_residual(
                metric="tip_deflection",
                tier=0,
                predicted=predicted,
                measured=measured,
                project_id=str(project_id),
            )

        lookup = make_calibrated_band_lookup(twin)
        other_project_band = await lookup(metric="tip_deflection", tier=0)
        assert other_project_band is not None


class TestDeviceInstanceRegistrar:
    async def test_registers_without_design_revision(self, twin, project_id):
        register = make_device_instance_registrar(twin)
        out = await register(
            serial_number="arm-unit-001", product_id="arm-v1", project_id=str(project_id)
        )
        assert out["serial_number"] == "arm-unit-001"
        assert out["global_asset_id"] == "urn:metaforge:device:arm-unit-001"
        assert out["design_revision_id"] is None

        stored = await twin.get_device_instance(UUID(out["node_id"]))
        assert stored is not None

    async def test_registers_with_design_revision_ref_creates_instance_of_edge(
        self, twin, project_id
    ):
        cad = await _seed_cad(twin, project_id)
        register = make_device_instance_registrar(twin)
        out = await register(
            serial_number="arm-unit-002",
            product_id="arm-v1",
            design_revision_ref=str(cad.id),
            project_id=str(project_id),
        )
        assert out["design_revision_id"] == str(cad.id)

        edges = await twin.graph.get_edges(UUID(out["node_id"]), direction="outgoing")
        instance_of_edges = [e for e in edges if e.edge_type == EdgeType.INSTANCE_OF]
        assert len(instance_of_edges) == 1
        assert instance_of_edges[0].target_id == cad.id

    async def test_requires_serial_number_and_product_id(self, twin):
        register = make_device_instance_registrar(twin)
        with pytest.raises(ValueError, match="serial_number"):
            await register(serial_number="", product_id="arm-v1")
        with pytest.raises(ValueError, match="product_id"):
            await register(serial_number="SN-1", product_id="")


class TestMeasurementRecorder:
    async def test_records_measurement_and_computes_residual(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="arm-unit-001", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)

        evidence_recorder = make_evidence_recorder(twin)
        calibration_recorder = make_calibration_recorder(twin, evidence_recorder=evidence_recorder)
        record = make_measurement_recorder(twin, calibration_recorder=calibration_recorder)

        out = await record(
            device_instance_id=device["node_id"],
            from_component="upper_arm",
            to_component="shoulder",
            metric="tip_deflection",
            value=0.45,
            work_product_id=str(wp.id),
            predicted_value=0.499,
        )
        assert out["value"] == 0.45
        assert out["residual"] == pytest.approx(0.499 - 0.45)
        assert "evidence_node_id" in out

        updated_wp = await twin.get_work_product(wp.id)
        quantity = updated_wp.metadata["interfaces"][0]["quantities"][0]
        assert len(quantity["measured"]) == 1
        assert quantity["measured"][0]["value"] == 0.45
        # First real prediction backfilled since none existed before.
        assert quantity["predicted"]["value"] == 0.499

    async def test_resolves_system_architecture_wp_by_project_id(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)

        record = make_measurement_recorder(twin)
        out = await record(
            device_instance_id=device["node_id"],
            from_component="upper_arm",
            to_component="shoulder",
            metric="tip_deflection",
            value=0.4,
            project_id=str(project_id),
        )
        assert out["work_product_id"] == str(wp.id)

    async def test_no_predicted_value_skips_residual(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)
        record = make_measurement_recorder(twin)

        out = await record(
            device_instance_id=device["node_id"],
            from_component="upper_arm",
            to_component="shoulder",
            metric="tip_deflection",
            value=0.4,
            work_product_id=str(wp.id),
        )
        assert "residual" not in out
        assert "evidence_node_id" not in out

    async def test_does_not_overwrite_existing_predicted_value(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(
            twin, project_id, predicted={"value": 0.5, "band": 0.1, "tier": "0", "evidence": ""}
        )
        record = make_measurement_recorder(twin)

        await record(
            device_instance_id=device["node_id"],
            from_component="upper_arm",
            to_component="shoulder",
            metric="tip_deflection",
            value=0.4,
            work_product_id=str(wp.id),
            predicted_value=0.55,  # a fresh prediction, should NOT overwrite the stored one
        )
        updated = await twin.get_work_product(wp.id)
        assert updated.metadata["interfaces"][0]["quantities"][0]["predicted"]["value"] == 0.5

    async def test_creates_measured_by_edge(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)
        record = make_measurement_recorder(twin)

        await record(
            device_instance_id=device["node_id"],
            from_component="upper_arm",
            to_component="shoulder",
            metric="tip_deflection",
            value=0.4,
            work_product_id=str(wp.id),
        )
        edges = await twin.graph.get_edges(UUID(device["node_id"]), direction="outgoing")
        measured_by = [e for e in edges if e.edge_type == EdgeType.MEASURED_BY]
        assert len(measured_by) == 1
        assert measured_by[0].target_id == wp.id

    async def test_unknown_device_instance_raises(self, twin, project_id):
        wp = await _seed_system_architecture(twin, project_id)
        record = make_measurement_recorder(twin)
        with pytest.raises(ValueError, match="no device_instance"):
            await record(
                device_instance_id=str(uuid4()),
                from_component="upper_arm",
                to_component="shoulder",
                metric="tip_deflection",
                value=0.4,
                work_product_id=str(wp.id),
            )

    async def test_unknown_interface_raises(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)
        record = make_measurement_recorder(twin)
        with pytest.raises(ValueError, match="no interface"):
            await record(
                device_instance_id=device["node_id"],
                from_component="bogus",
                to_component="shoulder",
                metric="tip_deflection",
                value=0.4,
                work_product_id=str(wp.id),
            )

    async def test_unknown_metric_raises(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)
        record = make_measurement_recorder(twin)
        with pytest.raises(ValueError, match="no quantity"):
            await record(
                device_instance_id=device["node_id"],
                from_component="upper_arm",
                to_component="shoulder",
                metric="bogus_metric",
                value=0.4,
                work_product_id=str(wp.id),
            )

    async def test_ambiguous_system_architecture_requires_explicit_wp_id(self, twin, project_id):
        device_registrar = make_device_instance_registrar(twin)
        device = await device_registrar(serial_number="SN-1", product_id="arm-v1")
        await _seed_system_architecture(twin, project_id, name="doc-1")
        await _seed_system_architecture(twin, project_id, name="doc-2")
        record = make_measurement_recorder(twin)
        with pytest.raises(ValueError, match="disambiguate"):
            await record(
                device_instance_id=device["node_id"],
                from_component="upper_arm",
                to_component="shoulder",
                metric="tip_deflection",
                value=0.4,
                project_id=str(project_id),
            )


class TestCalibratedBandOverridesEvaluator:
    async def test_falls_back_to_fixed_prior_with_no_calibration_history(self, twin, project_id):
        wp = await _seed_cad(twin, project_id)
        calibrated_band_lookup = make_calibrated_band_lookup(twin)
        evaluate = make_metric_evaluator(twin, calibrated_band_lookup=calibrated_band_lookup)

        out = await evaluate(
            work_product_id=str(wp.id), load_n=20.0, youngs_modulus_mpa=68900.0, limit_mm=0.5
        )
        assert out["band_source"] == "fixed_prior"
        assert out["calibration_sample_count"] is None
        assert out["band_mm"] == pytest.approx(0.2 * 0.5)  # default band_fraction

    async def test_uses_calibrated_band_once_enough_history_exists(self, twin, project_id):
        wp = await _seed_cad(twin, project_id)
        evidence_recorder = make_evidence_recorder(twin)
        record_residual = make_calibration_recorder(twin, evidence_recorder=evidence_recorder)
        for predicted, measured in [(0.4, 0.41), (0.4, 0.405), (0.4, 0.395)]:
            await record_residual(
                metric="tip_deflection", tier=0, predicted=predicted, measured=measured
            )

        calibrated_band_lookup = make_calibrated_band_lookup(twin)
        evaluate = make_metric_evaluator(twin, calibrated_band_lookup=calibrated_band_lookup)
        out = await evaluate(
            work_product_id=str(wp.id), load_n=20.0, youngs_modulus_mpa=68900.0, limit_mm=0.5
        )
        assert out["band_source"] == "calibrated"
        assert out["calibration_sample_count"] == 3
        assert out["band_mm"] != pytest.approx(0.2 * 0.5)

    async def test_no_lookup_wired_in_is_unchanged(self, twin, project_id):
        wp = await _seed_cad(twin, project_id)
        evaluate = make_metric_evaluator(twin)
        out = await evaluate(
            work_product_id=str(wp.id), load_n=20.0, youngs_modulus_mpa=68900.0, limit_mm=0.5
        )
        assert out["band_source"] == "fixed_prior"
        assert out["band_mm"] == pytest.approx(0.2 * 0.5)


class TestRealiseLearnAdapter:
    async def test_tools_registered_when_wired(self, twin):
        registrar = make_device_instance_registrar(twin)
        recorder = make_measurement_recorder(twin)
        server = TwinServer(
            twin=twin, device_instance_registrar=registrar, measurement_recorder=recorder
        )
        assert "twin.register_device_instance" in server.tool_ids
        assert "twin.record_measurement" in server.tool_ids

    async def test_not_registered_when_no_deps_supplied(self, twin):
        server = TwinServer(twin=twin)
        assert "twin.register_device_instance" not in server.tool_ids
        assert "twin.record_measurement" not in server.tool_ids

    async def test_register_device_instance_via_adapter(self, twin):
        registrar = make_device_instance_registrar(twin)
        server = TwinServer(twin=twin, device_instance_registrar=registrar)
        out = await server.register_device_instance(
            {"serial_number": "SN-1", "product_id": "arm-v1"}
        )
        assert out["serial_number"] == "SN-1"

    async def test_register_device_instance_missing_fields_rejected(self, twin):
        registrar = make_device_instance_registrar(twin)
        server = TwinServer(twin=twin, device_instance_registrar=registrar)
        with pytest.raises(ValueError, match="serial_number"):
            await server.register_device_instance({"product_id": "arm-v1"})

    async def test_record_measurement_via_adapter(self, twin, project_id):
        registrar = make_device_instance_registrar(twin)
        device = await registrar(serial_number="SN-1", product_id="arm-v1")
        wp = await _seed_system_architecture(twin, project_id)
        recorder = make_measurement_recorder(twin)
        server = TwinServer(twin=twin, measurement_recorder=recorder)

        out = await server.record_measurement(
            {
                "device_instance_id": device["node_id"],
                "from_component": "upper_arm",
                "to_component": "shoulder",
                "metric": "tip_deflection",
                "value": 0.4,
                "work_product_id": str(wp.id),
            }
        )
        assert out["value"] == 0.4

    async def test_record_measurement_missing_value_rejected(self, twin):
        recorder = make_measurement_recorder(twin)
        server = TwinServer(twin=twin, measurement_recorder=recorder)
        with pytest.raises(ValueError, match="value"):
            await server.record_measurement(
                {
                    "device_instance_id": str(uuid4()),
                    "from_component": "a",
                    "to_component": "b",
                    "metric": "m",
                }
            )
