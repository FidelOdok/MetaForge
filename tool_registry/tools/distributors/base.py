"""Abstract base class and shared models for distributor API adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("distributors.base")


class LifecycleStatus(StrEnum):
    """Part lifecycle status across distributors."""

    ACTIVE = "ACTIVE"
    NRND = "NRND"  # Not Recommended for New Designs
    EOL = "EOL"  # End of Life
    OBSOLETE = "OBSOLETE"
    UNKNOWN = "UNKNOWN"


class PricingBreak(BaseModel):
    """Price break for a quantity tier."""

    quantity: int = Field(ge=1, description="Minimum quantity for this price tier")
    unit_price: float = Field(ge=0, description="Unit price at this quantity")
    currency: str = Field(default="USD", description="ISO 4217 currency code")


class PartSearchResult(BaseModel):
    """Basic part search result from a distributor."""

    mpn: str = Field(description="Manufacturer Part Number")
    manufacturer: str = Field(default="", description="Manufacturer name")
    description: str = Field(default="", description="Part description")
    distributor: str = Field(description="Distributor name")
    distributor_pn: str = Field(default="", description="Distributor-specific part number")
    stock_qty: int = Field(default=0, ge=0, description="Quantity in stock")
    lead_time_days: int | None = Field(default=None, description="Lead time in days")
    lifecycle_status: LifecycleStatus = Field(default=LifecycleStatus.UNKNOWN)
    datasheet_url: str | None = Field(default=None, description="URL to datasheet")


class PartDetail(PartSearchResult):
    """Extended part details including specs."""

    specs: dict[str, Any] = Field(default_factory=dict, description="Technical specifications")
    package: str = Field(default="", description="Package type (e.g. QFP-48)")
    category: str = Field(default="", description="Part category")


class AvailabilityInfo(BaseModel):
    """Availability information for a part."""

    stock_qty: int = Field(default=0, ge=0, description="Quantity in stock")
    lead_time_days: int | None = Field(default=None, description="Lead time in days")
    minimum_order_qty: int = Field(default=1, ge=1, description="Minimum order quantity")
    factory_stock: int | None = Field(default=None, description="Stock at factory (if reported)")
    on_order_qty: int | None = Field(default=None, description="Quantity on order (if reported)")


class DistributorAdapter(ABC):
    """Abstract base class for distributor API adapters.

    All distributor adapters implement this interface so the BOM risk
    scoring pipeline can query parts uniformly across Digi-Key, Mouser,
    and Nexar/Octopart.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the distributor name."""

    @abstractmethod
    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        """Search for parts by keyword query.

        Returns an empty list if the API is unavailable or no results found.
        """

    @abstractmethod
    async def get_part_details(self, mpn: str) -> PartDetail | None:
        """Get detailed part information by MPN.

        Returns None if the part is not found or the API is unavailable.
        """

    @abstractmethod
    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        """Get pricing breaks for a part by MPN.

        Returns an empty list if the part is not found or the API is unavailable.
        """

    @abstractmethod
    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        """Get availability info for a part by MPN.

        Returns None if the part is not found or the API is unavailable.
        """

    async def close(self) -> None:
        """Clean up resources (e.g. HTTP client). Override if needed."""
