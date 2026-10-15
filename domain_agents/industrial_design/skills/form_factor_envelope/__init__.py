"""form_factor_envelope skill: the outer envelope a product's contents need (FORGE-633)."""

from .handler import FormFactorEnvelopeHandler
from .schema import (
    Component,
    FormFactorEnvelopeInput,
    FormFactorEnvelopeOutput,
    SourcedEnvelope,
    SourcedLength,
)

__all__ = [
    "Component",
    "FormFactorEnvelopeHandler",
    "FormFactorEnvelopeInput",
    "FormFactorEnvelopeOutput",
    "SourcedEnvelope",
    "SourcedLength",
]
