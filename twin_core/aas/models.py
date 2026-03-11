"""AAS Pydantic v2 models aligned with IDTA-02001 metamodel.

Covers the core AAS types needed for AASX export: AssetAdministrationShell,
AssetInformation, Submodel, SubmodelElement (Property, SubmodelElementCollection),
and Reference/Key structures.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag

# ---------------------------------------------------------------------------
# AAS Enumerations
# ---------------------------------------------------------------------------


class AssetKind(StrEnum):
    """Whether the asset is a type template or a concrete instance."""

    TYPE = "Type"
    INSTANCE = "Instance"
    NOT_APPLICABLE = "NotApplicable"


class ModellingKind(StrEnum):
    """Whether the submodel is a template or an instance."""

    TEMPLATE = "Template"
    INSTANCE = "Instance"


class DataTypeDefXsd(StrEnum):
    """XSD data types for AAS Property values."""

    STRING = "xs:string"
    BOOLEAN = "xs:boolean"
    INT = "xs:int"
    LONG = "xs:long"
    FLOAT = "xs:float"
    DOUBLE = "xs:double"
    DECIMAL = "xs:decimal"
    DATE = "xs:date"
    DATE_TIME = "xs:dateTime"
    ANY_URI = "xs:anyURI"


class KeyType(StrEnum):
    """AAS key types for references."""

    ASSET_ADMINISTRATION_SHELL = "AssetAdministrationShell"
    SUBMODEL = "Submodel"
    SUBMODEL_ELEMENT = "SubmodelElement"
    GLOBAL_REFERENCE = "GlobalReference"


# ---------------------------------------------------------------------------
# AAS Reference / Key
# ---------------------------------------------------------------------------


class Key(BaseModel):
    """A single key in an AAS Reference chain."""

    type: KeyType
    value: str


class Reference(BaseModel):
    """An AAS Reference pointing to an identifiable or referable element."""

    type: str = "ModelReference"
    keys: list[Key] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AAS Submodel Elements
# ---------------------------------------------------------------------------


class SubmodelElement(BaseModel):
    """Base class for all AAS submodel elements."""

    model_type: Literal["SubmodelElement"] = Field(alias="modelType", default="SubmodelElement")
    id_short: str = Field(alias="idShort")
    description: list[dict[str, str]] | None = None
    semantic_id: Reference | None = Field(default=None, alias="semanticId")

    model_config = {"populate_by_name": True}


class Property(SubmodelElement):
    """An AAS Property — a typed key-value pair."""

    model_type: Literal["Property"] = Field(alias="modelType", default="Property")
    value_type: DataTypeDefXsd = Field(alias="valueType", default=DataTypeDefXsd.STRING)
    value: str | None = None


def _get_model_type_discriminator(v: Any) -> str:
    """Extract modelType for discriminated union."""
    if isinstance(v, dict):
        return v.get("modelType", v.get("model_type", "SubmodelElement"))
    return getattr(v, "model_type", "SubmodelElement")


# Forward-declare the union type for nested elements
AnySubmodelElement = Annotated[
    Annotated[Property, Tag("Property")]
    | Annotated["SubmodelElementCollection", Tag("SubmodelElementCollection")]
    | Annotated[SubmodelElement, Tag("SubmodelElement")],
    Discriminator(_get_model_type_discriminator),
]


class SubmodelElementCollection(SubmodelElement):
    """A collection of submodel elements (used for BOM line items, etc.)."""

    model_type: Literal["SubmodelElementCollection"] = Field(
        alias="modelType", default="SubmodelElementCollection"
    )
    value: list[AnySubmodelElement] = Field(default_factory=list)


# Rebuild to resolve forward refs
SubmodelElementCollection.model_rebuild()


# ---------------------------------------------------------------------------
# AAS Submodel
# ---------------------------------------------------------------------------


class Submodel(BaseModel):
    """An AAS Submodel — a structured container of SubmodelElements."""

    id: str
    id_short: str = Field(alias="idShort")
    semantic_id: Reference | None = Field(default=None, alias="semanticId")
    kind: ModellingKind = ModellingKind.INSTANCE
    submodel_elements: list[AnySubmodelElement] = Field(
        default_factory=list, alias="submodelElements"
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# AAS Asset Information
# ---------------------------------------------------------------------------


class AssetInformation(BaseModel):
    """Identifies the asset represented by the AAS."""

    asset_kind: AssetKind = Field(alias="assetKind", default=AssetKind.INSTANCE)
    global_asset_id: str = Field(alias="globalAssetId")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# AAS Shell
# ---------------------------------------------------------------------------


class AssetAdministrationShell(BaseModel):
    """Top-level AAS envelope referencing asset info and submodels."""

    id: str
    id_short: str = Field(alias="idShort")
    asset_information: AssetInformation = Field(alias="assetInformation")
    submodels: list[Reference] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# AAS Environment (top-level container for AASX serialization)
# ---------------------------------------------------------------------------


class AASEnvironment(BaseModel):
    """Top-level container holding shells, submodels, and concept descriptions."""

    asset_administration_shells: list[AssetAdministrationShell] = Field(
        default_factory=list, alias="assetAdministrationShells"
    )
    submodels: list[Submodel] = Field(default_factory=list)
    concept_descriptions: list[dict[str, Any]] = Field(
        default_factory=list, alias="conceptDescriptions"
    )

    model_config = {"populate_by_name": True}
