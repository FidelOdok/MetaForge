"""Unit tests for ``mcp_core.versioning`` (MET-389)."""

from __future__ import annotations

import pytest

from mcp_core.versioning import (
    DEFAULT_VERSION,
    deprecation_message,
    normalise_version,
    parse_versioned_tool_id,
    versioned_tool_id,
)


class TestNormaliseVersion:
    @pytest.mark.parametrize("version", ["v1", "v2", "v10", "v100"])
    def test_valid_versions(self, version: str) -> None:
        assert normalise_version(version) == version

    @pytest.mark.parametrize(
        "version",
        [
            "1",  # missing v prefix
            "v",  # no digits
            "v1.0",  # dot/semver
            "V1",  # uppercase
            "v1-beta",  # suffix
            "v 1",  # whitespace
            "",  # empty
            "  v1",  # leading whitespace
        ],
    )
    def test_rejects_malformed(self, version: str) -> None:
        with pytest.raises(ValueError, match="invalid schema_version"):
            normalise_version(version)

    @pytest.mark.parametrize("bad", [None, 1, ["v1"]])
    def test_rejects_non_string(self, bad: object) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            normalise_version(bad)  # type: ignore[arg-type]

    def test_default_version_is_v1(self) -> None:
        # Pin the default — bumping it is intentional and every existing
        # tool needs to migrate or stay on v1 explicitly.
        assert DEFAULT_VERSION == "v1"
        assert normalise_version(DEFAULT_VERSION) == "v1"


class TestVersionedToolId:
    def test_basic_compose(self) -> None:
        assert versioned_tool_id("knowledge.search", "v1") == "knowledge.search@v1"

    def test_namespace_with_dot(self) -> None:
        assert (
            versioned_tool_id("cadquery.create_parametric", "v2") == "cadquery.create_parametric@v2"
        )

    def test_rejects_already_versioned(self) -> None:
        with pytest.raises(ValueError, match="already carries an @-suffix"):
            versioned_tool_id("knowledge.search@v1", "v2")

    def test_rejects_bad_version(self) -> None:
        with pytest.raises(ValueError, match="invalid schema_version"):
            versioned_tool_id("knowledge.search", "1.0")


class TestParseVersionedToolId:
    def test_unversioned(self) -> None:
        assert parse_versioned_tool_id("knowledge.search") == ("knowledge.search", None)

    def test_versioned(self) -> None:
        assert parse_versioned_tool_id("knowledge.search@v1") == (
            "knowledge.search",
            "v1",
        )

    def test_double_versioned_takes_first_split(self) -> None:
        # ``a@v1@v2`` is malformed but the parse should pick (a, v1@v2)
        # then validate v1@v2 — which fails. So this raises.
        with pytest.raises(ValueError, match="invalid schema_version"):
            parse_versioned_tool_id("knowledge.search@v1@v2")

    def test_malformed_version_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid schema_version"):
            parse_versioned_tool_id("knowledge.search@v1.0")

    def test_round_trip(self) -> None:
        bare = "cadquery.create_parametric"
        version = "v3"
        composed = versioned_tool_id(bare, version)
        parsed = parse_versioned_tool_id(composed)
        assert parsed == (bare, version)


class TestDeprecationMessage:
    def test_basic(self) -> None:
        msg = deprecation_message("v1", "v2", "2026-Q3-W3")
        assert msg == (
            "⚠️ DEPRECATED: schema v1 sunsets 2026-Q3-W3. Migrate to v2 (pin via @v2 in tool name)."
        )

    def test_validates_version_arguments(self) -> None:
        with pytest.raises(ValueError, match="invalid schema_version"):
            deprecation_message("1", "v2", "2026-Q3-W3")
        with pytest.raises(ValueError, match="invalid schema_version"):
            deprecation_message("v1", "2", "2026-Q3-W3")
