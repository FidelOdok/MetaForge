"""Comprehensive tests for distributor API adapters (MET-174, MET-175, MET-176)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    LifecycleStatus,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter
from tool_registry.tools.digikey.adapter import DigiKeyAdapter
from tool_registry.tools.mouser.adapter import MouserAdapter
from tool_registry.tools.nexar.adapter import NexarAdapter


# ======================================================================
# Fixtures — mock httpx responses
# ======================================================================

DIGIKEY_TOKEN_RESPONSE = {
    "access_token": "dk_test_token",
    "expires_in": 3600,
    "token_type": "Bearer",
}

DIGIKEY_SEARCH_RESPONSE = {
    "Products": [
        {
            "ManufacturerPartNumber": "STM32F405RGT6",
            "Manufacturer": {"Name": "STMicroelectronics"},
            "ProductDescription": "ARM Cortex-M4 MCU 168MHz",
            "DigiKeyPartNumber": "497-17363-ND",
            "QuantityAvailable": 5000,
            "ManufacturerLeadWeeks": "12",
            "ProductStatus": "Active",
            "DatasheetUrl": "https://example.com/datasheet.pdf",
        },
    ],
}

DIGIKEY_DETAIL_RESPONSE = {
    "ManufacturerPartNumber": "STM32F405RGT6",
    "Manufacturer": {"Name": "STMicroelectronics"},
    "ProductDescription": "ARM Cortex-M4 MCU 168MHz",
    "DigiKeyPartNumber": "497-17363-ND",
    "QuantityAvailable": 5000,
    "ManufacturerLeadWeeks": "12",
    "ProductStatus": "Active",
    "DatasheetUrl": "https://example.com/datasheet.pdf",
    "MinimumOrderQuantity": 1,
    "QuantityOnOrder": 200,
    "Category": {"Name": "Microcontrollers"},
    "Parameters": [
        {"ParameterText": "Package / Case", "ValueText": "LQFP-64"},
        {"ParameterText": "Core", "ValueText": "ARM Cortex-M4"},
    ],
    "StandardPricing": [
        {"BreakQuantity": 1, "UnitPrice": 12.50},
        {"BreakQuantity": 10, "UnitPrice": 11.20},
        {"BreakQuantity": 100, "UnitPrice": 9.80},
    ],
}

MOUSER_SEARCH_RESPONSE = {
    "SearchResults": {
        "Parts": [
            {
                "ManufacturerPartNumber": "ESP32-WROOM-32E",
                "Manufacturer": "Espressif",
                "Description": "WiFi+BT SoC Module",
                "MouserPartNumber": "356-ESP32WRM32E",
                "Availability": "2,500 In Stock",
                "LeadTime": "8 Weeks",
                "LifecycleStatus": "New Product",
                "DataSheetUrl": "https://example.com/esp32.pdf",
            },
        ],
    },
}

MOUSER_DETAIL_RESPONSE = {
    "SearchResults": {
        "Parts": [
            {
                "ManufacturerPartNumber": "ESP32-WROOM-32E",
                "Manufacturer": "Espressif",
                "Description": "WiFi+BT SoC Module",
                "MouserPartNumber": "356-ESP32WRM32E",
                "Availability": "2,500 In Stock",
                "LeadTime": "8 Weeks",
                "LifecycleStatus": "New Product",
                "DataSheetUrl": "https://example.com/esp32.pdf",
                "Min": 1,
                "Category": "RF Modules",
                "ProductAttributes": [
                    {"AttributeName": "Package / Case", "AttributeValue": "Module"},
                    {"AttributeName": "Frequency", "AttributeValue": "2.4 GHz"},
                ],
                "PriceBreaks": [
                    {"Quantity": 1, "Price": "$3.10", "Currency": "USD"},
                    {"Quantity": 10, "Price": "$2.80", "Currency": "USD"},
                    {"Quantity": 100, "Price": "$2.40", "Currency": "USD"},
                ],
            },
        ],
    },
}

MOUSER_EMPTY_RESPONSE = {"SearchResults": {"Parts": []}}

NEXAR_TOKEN_RESPONSE = {
    "access_token": "nexar_test_token",
    "expires_in": 3600,
    "token_type": "Bearer",
}

NEXAR_SEARCH_RESPONSE = {
    "data": {
        "supSearch": {
            "results": [
                {
                    "part": {
                        "mpn": "ATmega328P-AU",
                        "manufacturer": {"name": "Microchip"},
                        "shortDescription": "8-bit AVR MCU",
                        "bestDatasheet": {"url": "https://example.com/atmega.pdf"},
                        "sellers": [
                            {
                                "company": {"name": "DigiKey"},
                                "offers": [
                                    {
                                        "inventoryLevel": 10000,
                                        "moq": 1,
                                        "prices": [
                                            {"quantity": 1, "price": 2.50, "currency": "USD"},
                                            {"quantity": 25, "price": 2.10, "currency": "USD"},
                                        ],
                                        "factoryLeadDays": 84,
                                    }
                                ],
                            },
                            {
                                "company": {"name": "Mouser"},
                                "offers": [
                                    {
                                        "inventoryLevel": 5000,
                                        "moq": 1,
                                        "prices": [
                                            {"quantity": 1, "price": 2.60, "currency": "USD"},
                                        ],
                                        "factoryLeadDays": 90,
                                    }
                                ],
                            },
                        ],
                        "specs": [
                            {
                                "attribute": {"name": "Package / Case"},
                                "displayValue": "TQFP-32",
                            },
                            {
                                "attribute": {"name": "Lifecycle Status"},
                                "displayValue": "Production",
                            },
                        ],
                        "category": {"name": "Microcontrollers"},
                    }
                }
            ]
        }
    }
}

NEXAR_DETAIL_RESPONSE = {
    "data": {
        "supSearchMpn": {
            "results": [
                {
                    "part": {
                        "mpn": "ATmega328P-AU",
                        "manufacturer": {"name": "Microchip"},
                        "shortDescription": "8-bit AVR MCU",
                        "bestDatasheet": {"url": "https://example.com/atmega.pdf"},
                        "sellers": [
                            {
                                "company": {"name": "DigiKey"},
                                "offers": [
                                    {
                                        "inventoryLevel": 10000,
                                        "moq": 1,
                                        "prices": [
                                            {"quantity": 1, "price": 2.50, "currency": "USD"},
                                            {"quantity": 25, "price": 2.10, "currency": "USD"},
                                        ],
                                        "factoryLeadDays": 84,
                                        "factoryPackQuantity": 250,
                                    }
                                ],
                            },
                        ],
                        "specs": [
                            {
                                "attribute": {"name": "Package / Case"},
                                "displayValue": "TQFP-32",
                            },
                            {
                                "attribute": {"name": "Lifecycle Status"},
                                "displayValue": "Production",
                            },
                        ],
                        "category": {"name": "Microcontrollers"},
                        "descriptions": [{"text": "8-bit AVR MCU 20MHz 32KB Flash"}],
                    }
                }
            ]
        }
    }
}

NEXAR_EMPTY_RESPONSE = {"data": {"supSearchMpn": {"results": []}}}


# ======================================================================
# Helper to build mock httpx.Response
# ======================================================================


def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("GET", "https://test.example.com")
    resp = httpx.Response(status_code=status_code, json=json_data, request=request)
    return resp


# ======================================================================
# Shared model tests
# ======================================================================


class TestSharedModels:
    def test_lifecycle_status_values(self):
        assert LifecycleStatus.ACTIVE == "ACTIVE"
        assert LifecycleStatus.NRND == "NRND"
        assert LifecycleStatus.EOL == "EOL"
        assert LifecycleStatus.OBSOLETE == "OBSOLETE"
        assert LifecycleStatus.UNKNOWN == "UNKNOWN"

    def test_part_search_result_defaults(self):
        r = PartSearchResult(mpn="TEST", distributor="Test")
        assert r.stock_qty == 0
        assert r.lifecycle_status == LifecycleStatus.UNKNOWN
        assert r.datasheet_url is None

    def test_part_detail_extends_search_result(self):
        d = PartDetail(mpn="X", distributor="Y", specs={"a": 1}, package="QFP")
        assert d.specs == {"a": 1}
        assert d.package == "QFP"
        assert isinstance(d, PartSearchResult)

    def test_pricing_break_validation(self):
        pb = PricingBreak(quantity=10, unit_price=1.5, currency="EUR")
        assert pb.quantity == 10
        assert pb.currency == "EUR"

    def test_availability_info_defaults(self):
        a = AvailabilityInfo()
        assert a.stock_qty == 0
        assert a.minimum_order_qty == 1
        assert a.factory_stock is None


# ======================================================================
# Rate limiter tests
# ======================================================================


class TestRateLimiter:
    async def test_acquire_basic(self):
        rl = TokenBucketRateLimiter(rate=100.0, burst=10)
        # Should not block for burst-sized requests
        for _ in range(10):
            await rl.acquire()

    async def test_rate_limit_delays(self):
        """After burst is exhausted, acquire should wait for refill."""
        import time

        rl = TokenBucketRateLimiter(rate=100.0, burst=1)
        await rl.acquire()  # consume the 1 burst token
        start = time.monotonic()
        await rl.acquire()  # must wait ~10ms
        elapsed = time.monotonic() - start
        assert elapsed >= 0.005  # at least some delay


# ======================================================================
# Digi-Key adapter tests
# ======================================================================


class TestDigiKeyAdapter:
    @pytest.fixture
    def mock_client(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        return client

    @pytest.fixture
    def adapter(self, mock_client):
        return DigiKeyAdapter(
            client=mock_client,
            client_id="test_id",
            client_secret="test_secret",
        )

    def _setup_auth(self, mock_client):
        """Configure mock to return token on auth call then delegate."""
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        mock_client.post.return_value = token_resp

    async def test_search_parts(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        search_resp = _mock_response(DIGIKEY_SEARCH_RESPONSE)
        mock_client.post.side_effect = [token_resp, search_resp]

        results = await adapter.search_parts("STM32F405")
        assert len(results) == 1
        assert results[0].mpn == "STM32F405RGT6"
        assert results[0].manufacturer == "STMicroelectronics"
        assert results[0].distributor == "DigiKey"
        assert results[0].stock_qty == 5000
        assert results[0].lifecycle_status == LifecycleStatus.ACTIVE

    async def test_search_parts_empty(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        empty_resp = _mock_response({"Products": []})
        mock_client.post.side_effect = [token_resp, empty_resp]

        results = await adapter.search_parts("nonexistent_part_xyz")
        assert results == []

    async def test_search_parts_api_error_returns_empty(self, adapter, mock_client):
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        results = await adapter.search_parts("anything")
        assert results == []

    async def test_get_part_details(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        detail_resp = _mock_response(DIGIKEY_DETAIL_RESPONSE)
        mock_client.post.return_value = token_resp
        mock_client.get.return_value = detail_resp

        detail = await adapter.get_part_details("STM32F405RGT6")
        assert detail is not None
        assert detail.mpn == "STM32F405RGT6"
        assert detail.package == "LQFP-64"
        assert detail.category == "Microcontrollers"
        assert "Core" in detail.specs

    async def test_get_part_details_not_found(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        mock_client.post.return_value = token_resp
        mock_client.get.return_value = _mock_response({}, status_code=404)

        detail = await adapter.get_part_details("NONEXISTENT")
        assert detail is None

    async def test_get_part_details_api_error(self, adapter, mock_client):
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        detail = await adapter.get_part_details("anything")
        assert detail is None

    async def test_get_pricing(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        detail_resp = _mock_response(DIGIKEY_DETAIL_RESPONSE)
        mock_client.post.return_value = token_resp
        mock_client.get.return_value = detail_resp

        pricing = await adapter.get_pricing("STM32F405RGT6")
        assert len(pricing) == 3
        assert pricing[0].quantity == 1
        assert pricing[0].unit_price == 12.50
        assert pricing[2].quantity == 100

    async def test_get_pricing_not_found(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        mock_client.post.return_value = token_resp
        mock_client.get.return_value = _mock_response({}, status_code=404)

        pricing = await adapter.get_pricing("NONEXISTENT")
        assert pricing == []

    async def test_get_availability(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        detail_resp = _mock_response(DIGIKEY_DETAIL_RESPONSE)
        mock_client.post.return_value = token_resp
        mock_client.get.return_value = detail_resp

        avail = await adapter.get_availability("STM32F405RGT6")
        assert avail is not None
        assert avail.stock_qty == 5000
        assert avail.lead_time_days == 84  # 12 weeks * 7
        assert avail.minimum_order_qty == 1

    async def test_get_availability_not_found(self, adapter, mock_client):
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        mock_client.post.return_value = token_resp
        mock_client.get.return_value = _mock_response({}, status_code=404)

        avail = await adapter.get_availability("NONEXISTENT")
        assert avail is None

    async def test_token_refresh(self, adapter, mock_client):
        """Token should be fetched on first call and reused."""
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        search_resp = _mock_response(DIGIKEY_SEARCH_RESPONSE)
        mock_client.post.side_effect = [token_resp, search_resp, search_resp]

        await adapter.search_parts("STM32")
        # Second call should reuse the token (no new token request)
        await adapter.search_parts("STM32")
        # Only 3 post calls: 1 token + 2 searches
        assert mock_client.post.call_count == 3

    async def test_token_expired_triggers_refresh(self, adapter, mock_client):
        """Expired token should trigger a new auth request."""
        token_resp = _mock_response(DIGIKEY_TOKEN_RESPONSE)
        search_resp = _mock_response(DIGIKEY_SEARCH_RESPONSE)
        mock_client.post.side_effect = [token_resp, search_resp, token_resp, search_resp]

        await adapter.search_parts("STM32")
        # Force token expiry
        adapter._token_expires_at = 0.0
        await adapter.search_parts("STM32")
        # 4 calls: token + search + token + search
        assert mock_client.post.call_count == 4

    async def test_lifecycle_mapping(self, adapter, mock_client):
        """Verify Digi-Key lifecycle strings map correctly."""
        from tool_registry.tools.digikey.adapter import _map_lifecycle

        assert _map_lifecycle("Active") == LifecycleStatus.ACTIVE
        assert _map_lifecycle("Not Recommended for New Designs") == LifecycleStatus.NRND
        assert _map_lifecycle("Obsolete") == LifecycleStatus.OBSOLETE
        assert _map_lifecycle("Last Time Buy") == LifecycleStatus.EOL
        assert _map_lifecycle("Discontinued") == LifecycleStatus.EOL
        assert _map_lifecycle("SomethingElse") == LifecycleStatus.UNKNOWN

    async def test_lead_time_parsing(self, adapter, mock_client):
        from tool_registry.tools.digikey.adapter import _parse_lead_time

        assert _parse_lead_time("12") == 84
        assert _parse_lead_time(None) is None
        assert _parse_lead_time("bad") is None

    async def test_name_property(self, adapter):
        assert adapter.name == "DigiKey"


# ======================================================================
# Mouser adapter tests
# ======================================================================


class TestMouserAdapter:
    @pytest.fixture
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def adapter(self, mock_client):
        return MouserAdapter(client=mock_client, api_key="test_key")

    async def test_search_parts(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_SEARCH_RESPONSE)

        results = await adapter.search_parts("ESP32")
        assert len(results) == 1
        assert results[0].mpn == "ESP32-WROOM-32E"
        assert results[0].manufacturer == "Espressif"
        assert results[0].distributor == "Mouser"
        assert results[0].stock_qty == 2500
        assert results[0].lead_time_days == 56  # 8 weeks * 7

    async def test_search_parts_empty(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_EMPTY_RESPONSE)

        results = await adapter.search_parts("nonexistent")
        assert results == []

    async def test_search_parts_api_error(self, adapter, mock_client):
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )
        results = await adapter.search_parts("anything")
        assert results == []

    async def test_get_part_details(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_DETAIL_RESPONSE)

        detail = await adapter.get_part_details("ESP32-WROOM-32E")
        assert detail is not None
        assert detail.mpn == "ESP32-WROOM-32E"
        assert detail.package == "Module"
        assert detail.category == "RF Modules"
        assert "Frequency" in detail.specs

    async def test_get_part_details_not_found(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_EMPTY_RESPONSE)

        detail = await adapter.get_part_details("NONEXISTENT")
        assert detail is None

    async def test_get_part_details_api_error(self, adapter, mock_client):
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        detail = await adapter.get_part_details("anything")
        assert detail is None

    async def test_get_pricing(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_DETAIL_RESPONSE)

        pricing = await adapter.get_pricing("ESP32-WROOM-32E")
        assert len(pricing) == 3
        assert pricing[0].quantity == 1
        assert pricing[0].unit_price == 3.10
        assert pricing[2].unit_price == 2.40

    async def test_get_pricing_not_found(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_EMPTY_RESPONSE)

        pricing = await adapter.get_pricing("NONEXISTENT")
        assert pricing == []

    async def test_get_availability(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_DETAIL_RESPONSE)

        avail = await adapter.get_availability("ESP32-WROOM-32E")
        assert avail is not None
        assert avail.stock_qty == 2500
        assert avail.lead_time_days == 56
        assert avail.minimum_order_qty == 1

    async def test_get_availability_not_found(self, adapter, mock_client):
        mock_client.post.return_value = _mock_response(MOUSER_EMPTY_RESPONSE)

        avail = await adapter.get_availability("NONEXISTENT")
        assert avail is None

    async def test_lifecycle_mapping(self, adapter, mock_client):
        from tool_registry.tools.mouser.adapter import _map_lifecycle

        assert _map_lifecycle("New Product") == LifecycleStatus.ACTIVE
        assert _map_lifecycle("Not Recommended for New Designs") == LifecycleStatus.NRND
        assert _map_lifecycle("End of Life") == LifecycleStatus.EOL
        assert _map_lifecycle("Obsolete") == LifecycleStatus.OBSOLETE
        assert _map_lifecycle("SomethingElse") == LifecycleStatus.UNKNOWN

    async def test_stock_parsing(self, adapter, mock_client):
        from tool_registry.tools.mouser.adapter import _parse_stock

        assert _parse_stock("2,500 In Stock") == 2500
        assert _parse_stock("100 In Stock") == 100
        assert _parse_stock("") == 0
        assert _parse_stock("None Available") == 0

    async def test_lead_time_parsing(self, adapter, mock_client):
        from tool_registry.tools.mouser.adapter import _parse_lead_time

        assert _parse_lead_time("8 Weeks") == 56
        assert _parse_lead_time("5 Days") == 5
        assert _parse_lead_time("") is None
        assert _parse_lead_time("unknown") is None

    async def test_name_property(self, adapter):
        assert adapter.name == "Mouser"


# ======================================================================
# Nexar adapter tests
# ======================================================================


class TestNexarAdapter:
    @pytest.fixture
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def adapter(self, mock_client):
        return NexarAdapter(
            client=mock_client,
            client_id="test_id",
            client_secret="test_secret",
        )

    async def test_search_parts(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        search_resp = _mock_response(NEXAR_SEARCH_RESPONSE)
        mock_client.post.side_effect = [token_resp, search_resp]

        results = await adapter.search_parts("ATmega328P")
        assert len(results) == 1
        assert results[0].mpn == "ATmega328P-AU"
        assert results[0].manufacturer == "Microchip"
        assert results[0].distributor == "Nexar"
        assert results[0].stock_qty == 10000  # best offer
        assert results[0].lifecycle_status == LifecycleStatus.ACTIVE

    async def test_search_parts_empty(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        empty_resp = _mock_response({"data": {"supSearch": {"results": []}}})
        mock_client.post.side_effect = [token_resp, empty_resp]

        results = await adapter.search_parts("nonexistent")
        assert results == []

    async def test_search_parts_api_error(self, adapter, mock_client):
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        results = await adapter.search_parts("anything")
        assert results == []

    async def test_get_part_details(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        detail_resp = _mock_response(NEXAR_DETAIL_RESPONSE)
        mock_client.post.side_effect = [token_resp, detail_resp]

        detail = await adapter.get_part_details("ATmega328P-AU")
        assert detail is not None
        assert detail.mpn == "ATmega328P-AU"
        assert detail.package == "TQFP-32"
        assert detail.category == "Microcontrollers"

    async def test_get_part_details_not_found(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        empty_resp = _mock_response(NEXAR_EMPTY_RESPONSE)
        mock_client.post.side_effect = [token_resp, empty_resp]

        detail = await adapter.get_part_details("NONEXISTENT")
        assert detail is None

    async def test_get_part_details_api_error(self, adapter, mock_client):
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        detail = await adapter.get_part_details("anything")
        assert detail is None

    async def test_get_pricing(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        detail_resp = _mock_response(NEXAR_DETAIL_RESPONSE)
        mock_client.post.side_effect = [token_resp, detail_resp]

        pricing = await adapter.get_pricing("ATmega328P-AU")
        assert len(pricing) == 2
        # Should be sorted by quantity
        assert pricing[0].quantity == 1
        assert pricing[1].quantity == 25

    async def test_get_pricing_not_found(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        empty_resp = _mock_response(NEXAR_EMPTY_RESPONSE)
        mock_client.post.side_effect = [token_resp, empty_resp]

        pricing = await adapter.get_pricing("NONEXISTENT")
        assert pricing == []

    async def test_get_availability(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        detail_resp = _mock_response(NEXAR_DETAIL_RESPONSE)
        mock_client.post.side_effect = [token_resp, detail_resp]

        avail = await adapter.get_availability("ATmega328P-AU")
        assert avail is not None
        assert avail.stock_qty == 10000
        assert avail.lead_time_days == 84
        assert avail.minimum_order_qty == 1

    async def test_get_availability_not_found(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        empty_resp = _mock_response(NEXAR_EMPTY_RESPONSE)
        mock_client.post.side_effect = [token_resp, empty_resp]

        avail = await adapter.get_availability("NONEXISTENT")
        assert avail is None

    async def test_graphql_error_handling(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        error_resp = _mock_response({"errors": [{"message": "Bad query"}]})
        mock_client.post.side_effect = [token_resp, error_resp]

        results = await adapter.search_parts("anything")
        assert results == []

    async def test_token_refresh(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        search_resp = _mock_response(NEXAR_SEARCH_RESPONSE)
        mock_client.post.side_effect = [token_resp, search_resp, search_resp]

        await adapter.search_parts("ATmega")
        await adapter.search_parts("ATmega")
        # 1 token + 2 graphql calls = 3
        assert mock_client.post.call_count == 3

    async def test_token_expired_triggers_refresh(self, adapter, mock_client):
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        search_resp = _mock_response(NEXAR_SEARCH_RESPONSE)
        mock_client.post.side_effect = [
            token_resp, search_resp, token_resp, search_resp,
        ]

        await adapter.search_parts("ATmega")
        adapter._token_expires_at = 0.0
        await adapter.search_parts("ATmega")
        # 2 tokens + 2 graphql = 4
        assert mock_client.post.call_count == 4

    async def test_lifecycle_mapping(self, adapter, mock_client):
        from tool_registry.tools.nexar.adapter import _LIFECYCLE_MAP

        assert _LIFECYCLE_MAP["Production"] == LifecycleStatus.ACTIVE
        assert _LIFECYCLE_MAP["NRND"] == LifecycleStatus.NRND
        assert _LIFECYCLE_MAP["End of Life"] == LifecycleStatus.EOL
        assert _LIFECYCLE_MAP["Obsolete"] == LifecycleStatus.OBSOLETE

    async def test_multi_seller_availability_aggregation(self, adapter, mock_client):
        """Nexar aggregates stock across multiple sellers."""
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        # Use search response which has 2 sellers (10000 + 5000)
        inner = NEXAR_SEARCH_RESPONSE["data"]["supSearch"]["results"]
        multi_resp = _mock_response({"data": {"supSearchMpn": {"results": inner}}})
        mock_client.post.side_effect = [token_resp, multi_resp]

        avail = await adapter.get_availability("ATmega328P-AU")
        assert avail is not None
        assert avail.stock_qty == 15000  # 10000 + 5000

    async def test_pricing_dedup_lowest_price(self, adapter, mock_client):
        """When multiple sellers have same qty, keep lowest price."""
        token_resp = _mock_response(NEXAR_TOKEN_RESPONSE)
        inner = NEXAR_SEARCH_RESPONSE["data"]["supSearch"]["results"]
        multi_resp = _mock_response({"data": {"supSearchMpn": {"results": inner}}})
        mock_client.post.side_effect = [token_resp, multi_resp]

        pricing = await adapter.get_pricing("ATmega328P-AU")
        # qty=1: DigiKey $2.50 vs Mouser $2.60 -> should pick $2.50
        qty1 = [p for p in pricing if p.quantity == 1]
        assert len(qty1) == 1
        assert qty1[0].unit_price == 2.50

    async def test_name_property(self, adapter):
        assert adapter.name == "Nexar"


# ======================================================================
# Cross-adapter tests
# ======================================================================


class TestCrossAdapter:
    def test_all_adapters_implement_interface(self):
        """All adapters are instances of DistributorAdapter."""
        from tool_registry.tools.distributors.base import DistributorAdapter

        dk = DigiKeyAdapter(client=AsyncMock(), client_id="x", client_secret="y")
        mo = MouserAdapter(client=AsyncMock(), api_key="x")
        nx = NexarAdapter(client=AsyncMock(), client_id="x", client_secret="y")
        assert isinstance(dk, DistributorAdapter)
        assert isinstance(mo, DistributorAdapter)
        assert isinstance(nx, DistributorAdapter)

    def test_adapter_names_unique(self):
        dk = DigiKeyAdapter(client=AsyncMock(), client_id="x", client_secret="y")
        mo = MouserAdapter(client=AsyncMock(), api_key="x")
        nx = NexarAdapter(client=AsyncMock(), client_id="x", client_secret="y")
        names = {dk.name, mo.name, nx.name}
        assert len(names) == 3
