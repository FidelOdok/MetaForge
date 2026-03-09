"""Tests for the AAS export adapter (MET-164).

Covers:
  - AAS Pydantic model serialization
  - Graph-to-AAS mapping (Nameplate, BOM, TechnicalData, Documentation)
  - AASX packaging (OPC ZIP structure verification)
  - Round-trip: create graph -> export AASX -> verify contents
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from uuid import uuid4

import pytest

from twin_core.aas.mapper import AASMapper
from twin_core.aas.models import (
    AASEnvironment,
    AssetAdministrationShell,
    AssetInformation,
    AssetKind,
    DataTypeDefXsd,
    Key,
    KeyType,
    ModellingKind,
    Property,
    Reference,
    Submodel,
    SubmodelElementCollection,
)
from twin_core.aas.packager import AASXPackager
from twin_core.aas.exporter import AASExporter
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models.artifact import Artifact
from twin_core.models.base import EdgeBase
from twin_core.models.component import Component
from twin_core.models.constraint import Constraint
from twin_core.models.enums import (
    ArtifactType,
    ComponentLifecycle,
    ConstraintSeverity,
    ConstraintStatus,
    EdgeType,
    NodeType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_artifact() -> Artifact:
    return Artifact(
        name="Main Schematic",
        type=ArtifactType.SCHEMATIC,
        domain="electronics",
        file_path="eda/kicad/main.kicad_sch",
        content_hash="abc123",
        format="kicad_sch",
        created_by="test",
    )


@pytest.fixture
def sample_doc_artifact() -> Artifact:
    return Artifact(
        name="Assembly Guide",
        type=ArtifactType.DOCUMENTATION,
        domain="manufacturing",
        file_path="docs/assembly_guide.pdf",
        content_hash="doc456",
        format="pdf",
        created_by="test",
    )


@pytest.fixture
def sample_component() -> Component:
    return Component(
        part_number="STM32F405RGT6",
        manufacturer="STMicroelectronics",
        description="ARM Cortex-M4 MCU",
        package="LQFP-64",
        lifecycle=ComponentLifecycle.ACTIVE,
        unit_cost=8.50,
        quantity=1,
    )


@pytest.fixture
def sample_constraint() -> Constraint:
    return Constraint(
        name="Max Operating Temperature",
        expression="temperature <= 85",
        severity=ConstraintSeverity.ERROR,
        status=ConstraintStatus.PASS,
        domain="thermal",
        source="datasheet",
        message="Component rated to 85C max",
    )


@pytest.fixture
def asset_id() -> str:
    return "urn:metaforge:asset:drone-fc-v1"


@pytest.fixture
def asset_name() -> str:
    return "DroneFCv1"


# ---------------------------------------------------------------------------
# Model Serialization Tests
# ---------------------------------------------------------------------------


class TestAASModels:
    """Verify AAS Pydantic models serialize correctly with aliases."""

    def test_property_serialization(self) -> None:
        prop = Property(
            idShort="TestProp",
            valueType=DataTypeDefXsd.STRING,
            value="hello",
        )
        data = prop.model_dump(by_alias=True, exclude_none=True)
        assert data["idShort"] == "TestProp"
        assert data["modelType"] == "Property"
        assert data["valueType"] == "xs:string"
        assert data["value"] == "hello"

    def test_property_int_type(self) -> None:
        prop = Property(
            idShort="Count",
            valueType=DataTypeDefXsd.INT,
            value="42",
        )
        data = prop.model_dump(by_alias=True, exclude_none=True)
        assert data["valueType"] == "xs:int"

    def test_submodel_element_collection(self) -> None:
        col = SubmodelElementCollection(
            idShort="MyCollection",
            value=[
                Property(idShort="A", value="1"),
                Property(idShort="B", value="2"),
            ],
        )
        data = col.model_dump(by_alias=True, exclude_none=True)
        assert data["modelType"] == "SubmodelElementCollection"
        assert len(data["value"]) == 2
        assert data["value"][0]["idShort"] == "A"

    def test_submodel_serialization(self) -> None:
        sm = Submodel(
            id="urn:test:sm:1",
            idShort="TestSubmodel",
            kind=ModellingKind.INSTANCE,
            submodelElements=[
                Property(idShort="Prop1", value="val1"),
            ],
        )
        data = sm.model_dump(by_alias=True, exclude_none=True)
        assert data["id"] == "urn:test:sm:1"
        assert data["idShort"] == "TestSubmodel"
        assert data["kind"] == "Instance"
        assert len(data["submodelElements"]) == 1

    def test_asset_information(self) -> None:
        ai = AssetInformation(
            assetKind=AssetKind.INSTANCE,
            globalAssetId="urn:test:asset:1",
        )
        data = ai.model_dump(by_alias=True, exclude_none=True)
        assert data["assetKind"] == "Instance"
        assert data["globalAssetId"] == "urn:test:asset:1"

    def test_shell_serialization(self) -> None:
        shell = AssetAdministrationShell(
            id="urn:test:aas:1",
            idShort="TestShell",
            assetInformation=AssetInformation(
                assetKind=AssetKind.INSTANCE,
                globalAssetId="urn:test:asset:1",
            ),
            submodels=[
                Reference(
                    type="ModelReference",
                    keys=[Key(type=KeyType.SUBMODEL, value="urn:test:sm:1")],
                )
            ],
        )
        data = shell.model_dump(by_alias=True, exclude_none=True)
        assert data["id"] == "urn:test:aas:1"
        assert data["assetInformation"]["globalAssetId"] == "urn:test:asset:1"
        assert len(data["submodels"]) == 1

    def test_environment_serialization(self) -> None:
        env = AASEnvironment(
            assetAdministrationShells=[],
            submodels=[],
        )
        data = env.model_dump(by_alias=True, exclude_none=True)
        assert "assetAdministrationShells" in data
        assert "submodels" in data
        assert "conceptDescriptions" in data

    def test_reference_with_keys(self) -> None:
        ref = Reference(
            type="ExternalReference",
            keys=[Key(type=KeyType.GLOBAL_REFERENCE, value="urn:semantic:id:1")],
        )
        data = ref.model_dump(by_alias=True, exclude_none=True)
        assert data["type"] == "ExternalReference"
        assert data["keys"][0]["value"] == "urn:semantic:id:1"


# ---------------------------------------------------------------------------
# Mapper Tests
# ---------------------------------------------------------------------------


class TestAASMapper:
    """Verify graph-to-AAS mapping logic."""

    def test_map_empty_subgraph(self, asset_id: str, asset_name: str) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(nodes=[], edges=[], root_id=uuid4(), depth=2)

        env = mapper.map_subgraph(subgraph)

        assert len(env.asset_administration_shells) == 1
        assert len(env.submodels) == 4  # Nameplate, BOM, TechData, Docs

        shell = env.asset_administration_shells[0]
        assert shell.asset_information.global_asset_id == asset_id
        assert shell.id_short == asset_name

    def test_nameplate_submodel_from_artifacts(
        self,
        asset_id: str,
        asset_name: str,
        sample_artifact: Artifact,
    ) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(
            nodes=[sample_artifact],
            edges=[],
            root_id=sample_artifact.id,
            depth=2,
        )

        env = mapper.map_subgraph(subgraph)

        # Find nameplate submodel
        nameplate = next(
            sm for sm in env.submodels if sm.id_short == "Nameplate"
        )
        assert nameplate is not None

        # Check elements
        elements_by_id = {
            el.id_short: el for el in nameplate.submodel_elements
        }
        assert "ManufacturerName" in elements_by_id
        assert "ArtifactCount" in elements_by_id
        assert elements_by_id["ArtifactCount"].value == "1"
        assert "EngineeringDomains" in elements_by_id
        assert elements_by_id["EngineeringDomains"].value == "electronics"

    def test_bom_submodel_from_components(
        self,
        asset_id: str,
        asset_name: str,
        sample_component: Component,
    ) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(
            nodes=[sample_component],
            edges=[],
            root_id=sample_component.id,
            depth=2,
        )

        env = mapper.map_subgraph(subgraph)

        bom = next(
            sm for sm in env.submodels if sm.id_short == "BillOfMaterials"
        )
        assert len(bom.submodel_elements) == 1

        line_item = bom.submodel_elements[0]
        assert isinstance(line_item, SubmodelElementCollection)
        props = {el.id_short: el for el in line_item.value}
        assert props["PartNumber"].value == "STM32F405RGT6"
        assert props["Manufacturer"].value == "STMicroelectronics"
        assert props["Package"].value == "LQFP-64"
        assert props["UnitCost"].value == "8.5"

    def test_technical_data_from_constraints(
        self,
        asset_id: str,
        asset_name: str,
        sample_constraint: Constraint,
    ) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(
            nodes=[sample_constraint],
            edges=[],
            root_id=sample_constraint.id,
            depth=2,
        )

        env = mapper.map_subgraph(subgraph)

        tech = next(
            sm for sm in env.submodels if sm.id_short == "TechnicalData"
        )
        assert len(tech.submodel_elements) == 1

        constraint_el = tech.submodel_elements[0]
        assert isinstance(constraint_el, SubmodelElementCollection)
        props = {el.id_short: el for el in constraint_el.value}
        assert props["Name"].value == "Max Operating Temperature"
        assert props["Expression"].value == "temperature <= 85"
        assert props["Severity"].value == "error"
        assert props["Status"].value == "pass"
        assert props["Message"].value == "Component rated to 85C max"

    def test_documentation_submodel(
        self,
        asset_id: str,
        asset_name: str,
        sample_doc_artifact: Artifact,
    ) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(
            nodes=[sample_doc_artifact],
            edges=[],
            root_id=sample_doc_artifact.id,
            depth=2,
        )

        env = mapper.map_subgraph(subgraph)

        docs = next(
            sm for sm in env.submodels if sm.id_short == "Documentation"
        )
        assert len(docs.submodel_elements) == 1

        doc_el = docs.submodel_elements[0]
        assert isinstance(doc_el, SubmodelElementCollection)
        props = {el.id_short: el for el in doc_el.value}
        assert props["Title"].value == "Assembly Guide"
        assert props["Format"].value == "pdf"

    def test_mixed_node_types(
        self,
        asset_id: str,
        asset_name: str,
        sample_artifact: Artifact,
        sample_component: Component,
        sample_constraint: Constraint,
        sample_doc_artifact: Artifact,
    ) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(
            nodes=[
                sample_artifact,
                sample_component,
                sample_constraint,
                sample_doc_artifact,
            ],
            edges=[],
            root_id=sample_artifact.id,
            depth=2,
        )

        env = mapper.map_subgraph(subgraph)

        assert len(env.submodels) == 4
        bom = next(sm for sm in env.submodels if sm.id_short == "BillOfMaterials")
        assert len(bom.submodel_elements) == 1

        tech = next(sm for sm in env.submodels if sm.id_short == "TechnicalData")
        assert len(tech.submodel_elements) == 1

        docs = next(sm for sm in env.submodels if sm.id_short == "Documentation")
        assert len(docs.submodel_elements) == 1

    def test_shell_references_all_submodels(
        self, asset_id: str, asset_name: str
    ) -> None:
        from twin_core.models.relationship import SubGraph

        mapper = AASMapper(asset_id=asset_id, asset_name=asset_name)
        subgraph = SubGraph(nodes=[], edges=[], root_id=uuid4(), depth=2)

        env = mapper.map_subgraph(subgraph)
        shell = env.asset_administration_shells[0]

        # Shell should reference all 4 submodels
        assert len(shell.submodels) == 4

        # Each reference should point to a real submodel ID
        sm_ids = {sm.id for sm in env.submodels}
        for ref in shell.submodels:
            ref_target = ref.keys[0].value
            assert ref_target in sm_ids


# ---------------------------------------------------------------------------
# Packager Tests
# ---------------------------------------------------------------------------


class TestAASXPackager:
    """Verify AASX ZIP packaging structure."""

    def _make_minimal_env(self) -> AASEnvironment:
        return AASEnvironment(
            assetAdministrationShells=[
                AssetAdministrationShell(
                    id="urn:test:aas:1",
                    idShort="TestShell",
                    assetInformation=AssetInformation(
                        assetKind=AssetKind.INSTANCE,
                        globalAssetId="urn:test:asset:1",
                    ),
                )
            ],
            submodels=[
                Submodel(
                    id="urn:test:sm:1",
                    idShort="TestSubmodel",
                    submodelElements=[
                        Property(idShort="Prop1", value="val1"),
                    ],
                ),
            ],
        )

    def test_package_produces_valid_zip(self) -> None:
        packager = AASXPackager()
        env = self._make_minimal_env()
        data = packager.package_to_bytes(env)

        assert len(data) > 0
        # Verify it's a valid ZIP
        buf = BytesIO(data)
        assert zipfile.is_zipfile(buf)

    def test_opc_structure(self) -> None:
        packager = AASXPackager()
        env = self._make_minimal_env()
        data = packager.package_to_bytes(env)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            names = zf.namelist()

            # OPC boilerplate
            assert "[Content_Types].xml" in names
            assert "_rels/.rels" in names

            # AAS environment
            assert "aasx/aas/aas_env.json" in names

    def test_content_types_xml(self) -> None:
        packager = AASXPackager()
        env = self._make_minimal_env()
        data = packager.package_to_bytes(env)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            content_types = zf.read("[Content_Types].xml").decode("utf-8")
            assert "application/json" in content_types
            assert "application/xml" in content_types

    def test_rels_file(self) -> None:
        packager = AASXPackager()
        env = self._make_minimal_env()
        data = packager.package_to_bytes(env)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            rels = zf.read("_rels/.rels").decode("utf-8")
            assert "aas-spec" in rels
            assert "aas_env.json" in rels

    def test_environment_json_content(self) -> None:
        packager = AASXPackager()
        env = self._make_minimal_env()
        data = packager.package_to_bytes(env)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            env_raw = zf.read("aasx/aas/aas_env.json").decode("utf-8")
            env_data = json.loads(env_raw)

            assert "assetAdministrationShells" in env_data
            assert len(env_data["assetAdministrationShells"]) == 1
            assert env_data["assetAdministrationShells"][0]["id"] == "urn:test:aas:1"

            assert "submodels" in env_data
            assert len(env_data["submodels"]) == 1

    def test_individual_submodel_files(self) -> None:
        packager = AASXPackager()
        env = self._make_minimal_env()
        data = packager.package_to_bytes(env)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            names = zf.namelist()
            assert "aasx/aas/testsubmodel.json" in names

            sm_raw = zf.read("aasx/aas/testsubmodel.json").decode("utf-8")
            sm_data = json.loads(sm_raw)
            assert sm_data["idShort"] == "TestSubmodel"

    def test_package_to_file(self, tmp_path: str) -> None:
        import tempfile
        from pathlib import Path

        packager = AASXPackager()
        env = self._make_minimal_env()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "test.aasx"
            result = packager.package_to_file(env, out_path)

            assert result.exists()
            assert result.stat().st_size > 0
            assert zipfile.is_zipfile(result)


# ---------------------------------------------------------------------------
# Round-trip / Integration Tests (graph -> export -> verify)
# ---------------------------------------------------------------------------


class TestAASExporterRoundTrip:
    """End-to-end tests: populate graph -> export AASX -> verify contents."""

    @pytest.fixture
    def graph(self) -> InMemoryGraphEngine:
        return InMemoryGraphEngine()

    @pytest.mark.asyncio
    async def test_export_single_artifact(self, graph: InMemoryGraphEngine) -> None:
        artifact = Artifact(
            name="PCB Layout",
            type=ArtifactType.PCB_LAYOUT,
            domain="electronics",
            file_path="eda/kicad/main.kicad_pcb",
            content_hash="pcb789",
            format="kicad_pcb",
            created_by="test",
        )
        await graph.add_node(artifact)

        exporter = AASExporter(
            graph=graph,
            asset_id="urn:metaforge:asset:test-1",
            asset_name="TestProduct",
        )

        data = await exporter.export_to_bytes(root_id=artifact.id, depth=1)

        # Verify it's a valid AASX
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            env_raw = zf.read("aasx/aas/aas_env.json").decode("utf-8")
            env_data = json.loads(env_raw)

            assert len(env_data["assetAdministrationShells"]) == 1
            shell = env_data["assetAdministrationShells"][0]
            assert shell["assetInformation"]["globalAssetId"] == "urn:metaforge:asset:test-1"

    @pytest.mark.asyncio
    async def test_export_with_components_and_constraints(
        self, graph: InMemoryGraphEngine
    ) -> None:
        # Create root artifact
        root = Artifact(
            name="Main Schematic",
            type=ArtifactType.SCHEMATIC,
            domain="electronics",
            file_path="eda/main.kicad_sch",
            content_hash="sch001",
            format="kicad_sch",
            created_by="test",
        )
        await graph.add_node(root)

        # Create component linked to root
        comp = Component(
            part_number="LM7805",
            manufacturer="Texas Instruments",
            description="5V Voltage Regulator",
            package="TO-220",
            unit_cost=0.75,
            quantity=2,
        )
        await graph.add_node(comp)
        await graph.add_edge(
            EdgeBase(
                source_id=root.id,
                target_id=comp.id,
                edge_type=EdgeType.USES_COMPONENT,
            )
        )

        # Create constraint linked to root
        constraint = Constraint(
            name="Input Voltage Range",
            expression="7 <= vin <= 25",
            severity=ConstraintSeverity.ERROR,
            domain="power",
            source="datasheet",
            message="LM7805 requires 7-25V input",
        )
        await graph.add_node(constraint)
        await graph.add_edge(
            EdgeBase(
                source_id=root.id,
                target_id=constraint.id,
                edge_type=EdgeType.CONSTRAINED_BY,
            )
        )

        exporter = AASExporter(
            graph=graph,
            asset_id="urn:metaforge:asset:power-supply",
            asset_name="PowerSupply",
        )

        data = await exporter.export_to_bytes(root_id=root.id, depth=2)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            env_raw = zf.read("aasx/aas/aas_env.json").decode("utf-8")
            env_data = json.loads(env_raw)

            submodels = {sm["idShort"]: sm for sm in env_data["submodels"]}

            # BOM should have the component
            bom = submodels["BillOfMaterials"]
            assert len(bom["submodelElements"]) == 1
            bom_props = {
                el["idShort"]: el
                for el in bom["submodelElements"][0]["value"]
            }
            assert bom_props["PartNumber"]["value"] == "LM7805"
            assert bom_props["Quantity"]["value"] == "2"

            # TechnicalData should have the constraint
            tech = submodels["TechnicalData"]
            assert len(tech["submodelElements"]) == 1
            constraint_props = {
                el["idShort"]: el
                for el in tech["submodelElements"][0]["value"]
            }
            assert constraint_props["Name"]["value"] == "Input Voltage Range"

    @pytest.mark.asyncio
    async def test_export_environment_without_packaging(
        self, graph: InMemoryGraphEngine
    ) -> None:
        artifact = Artifact(
            name="Firmware",
            type=ArtifactType.FIRMWARE_SOURCE,
            domain="firmware",
            file_path="firmware/src/main.c",
            content_hash="fw001",
            format="c",
            created_by="test",
        )
        await graph.add_node(artifact)

        exporter = AASExporter(
            graph=graph,
            asset_id="urn:metaforge:asset:fw-test",
            asset_name="FWTest",
        )

        env = await exporter.export_environment(root_id=artifact.id, depth=1)

        assert isinstance(env, AASEnvironment)
        assert len(env.asset_administration_shells) == 1
        assert len(env.submodels) == 4

    @pytest.mark.asyncio
    async def test_export_preserves_semantic_ids(
        self, graph: InMemoryGraphEngine
    ) -> None:
        artifact = Artifact(
            name="Test",
            type=ArtifactType.BOM,
            domain="supply-chain",
            file_path="bom/bom.csv",
            content_hash="bom001",
            format="csv",
            created_by="test",
        )
        await graph.add_node(artifact)

        exporter = AASExporter(
            graph=graph,
            asset_id="urn:test:asset:sem",
            asset_name="SemTest",
        )

        env = await exporter.export_environment(root_id=artifact.id, depth=1)

        # All submodels should have semantic IDs set
        for sm in env.submodels:
            assert sm.semantic_id is not None
            assert len(sm.semantic_id.keys) == 1
            assert sm.semantic_id.keys[0].type == KeyType.GLOBAL_REFERENCE

    @pytest.mark.asyncio
    async def test_export_multiple_components(
        self, graph: InMemoryGraphEngine
    ) -> None:
        root = Artifact(
            name="BOM Artifact",
            type=ArtifactType.BOM,
            domain="supply-chain",
            file_path="bom/main.csv",
            content_hash="bom999",
            format="csv",
            created_by="test",
        )
        await graph.add_node(root)

        components = []
        for i, (pn, mfr) in enumerate([
            ("RC0402JR-07100KL", "Yageo"),
            ("CC0402KRX7R7BB104", "Yageo"),
            ("CRCW040210K0FKED", "Vishay"),
        ]):
            comp = Component(
                part_number=pn,
                manufacturer=mfr,
                description=f"Component {i}",
                quantity=i + 1,
            )
            await graph.add_node(comp)
            await graph.add_edge(
                EdgeBase(
                    source_id=root.id,
                    target_id=comp.id,
                    edge_type=EdgeType.USES_COMPONENT,
                )
            )
            components.append(comp)

        exporter = AASExporter(
            graph=graph,
            asset_id="urn:test:multi-bom",
            asset_name="MultiBOM",
        )

        data = await exporter.export_to_bytes(root_id=root.id, depth=2)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            env_raw = zf.read("aasx/aas/aas_env.json").decode("utf-8")
            env_data = json.loads(env_raw)

            bom = next(
                sm for sm in env_data["submodels"]
                if sm["idShort"] == "BillOfMaterials"
            )
            assert len(bom["submodelElements"]) == 3

    @pytest.mark.asyncio
    async def test_aasx_zip_is_valid_and_complete(
        self, graph: InMemoryGraphEngine
    ) -> None:
        """Verify the full OPC structure of the exported AASX."""
        root = Artifact(
            name="Complete Test",
            type=ArtifactType.SCHEMATIC,
            domain="electronics",
            file_path="test.kicad_sch",
            content_hash="full001",
            format="kicad_sch",
            created_by="test",
        )
        await graph.add_node(root)

        exporter = AASExporter(
            graph=graph,
            asset_id="urn:test:full",
            asset_name="FullTest",
        )

        data = await exporter.export_to_bytes(root_id=root.id, depth=1)

        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            names = zf.namelist()

            # Required OPC files
            assert "[Content_Types].xml" in names
            assert "_rels/.rels" in names
            assert "aasx/aas/aas_env.json" in names

            # Individual submodel files
            assert "aasx/aas/nameplate.json" in names
            assert "aasx/aas/billofmaterials.json" in names
            assert "aasx/aas/technicaldata.json" in names
            assert "aasx/aas/documentation.json" in names

            # Verify each JSON file is valid JSON
            for name in names:
                if name.endswith(".json"):
                    content = zf.read(name).decode("utf-8")
                    parsed = json.loads(content)
                    assert isinstance(parsed, dict)

            # Verify no errors in ZIP
            test_result = zf.testzip()
            assert test_result is None  # None means no errors
