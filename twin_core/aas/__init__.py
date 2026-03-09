"""AAS (Asset Administration Shell) export adapter for the Digital Twin.

Serializes graph subsets as IDTA-compliant AASX packages containing
JSON submodels (DigitalNameplate, BOM, TechnicalData, Documentation).
"""

from twin_core.aas.exporter import AASExporter
from twin_core.aas.mapper import AASMapper
from twin_core.aas.models import (
    AssetAdministrationShell,
    AssetInformation,
    AssetKind,
    DataTypeDefXsd,
    ModellingKind,
    Property,
    Submodel,
    SubmodelElement,
    SubmodelElementCollection,
)
from twin_core.aas.packager import AASXPackager

__all__ = [
    "AASExporter",
    "AASMapper",
    "AASXPackager",
    "AssetAdministrationShell",
    "AssetInformation",
    "AssetKind",
    "DataTypeDefXsd",
    "ModellingKind",
    "Property",
    "Submodel",
    "SubmodelElement",
    "SubmodelElementCollection",
]
