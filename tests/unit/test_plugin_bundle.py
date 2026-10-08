"""The portable plugin bundle ChatGPT cloud uploads (FORGE-575).

ChatGPT installs a plugin from an uploaded archive rather than from a
marketplace, and expects the *portable* Agent Plugins layout. The committed
``integrations/codex/`` tree is written in the older Codex-specific shape,
which the current CLI rejects outright: probing ``codex-cli 0.161.0`` with
scratch layouts answers ``missing plugin.json`` for a manifest that exists
only under ``.codex-plugin/``.

So the packager transforms rather than copies, and these tests pin the
transform. They are structural and need no CLI -- but the shape they assert
was learned from the shipped binary, not from the documentation, which is the
same standard ``scripts/build_integrations.py`` set when it recorded "Verified
against codex-cli 0.118.0. That is a stronger source than the docs."
"""

from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.package_plugin_bundle import (
    MCP_SCHEMA,
    MCP_TRANSPORT,
    PLUGIN_SCHEMA,
    archive,
    build_bundle,
    to_portable_manifest,
    to_portable_mcp,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    return build_bundle(out_root=tmp_path_factory.mktemp("bundle-out"))


class TestPortableManifest:
    def test_the_manifest_is_at_the_plugin_root(self, bundle: Path) -> None:
        """Not only under .codex-plugin/ -- that is what the CLI rejects."""
        assert (bundle / "plugin.json").is_file()

    def test_the_codex_fallback_is_kept_alongside_it(self, bundle: Path) -> None:
        """The docs name it as the compatibility path for older hosts."""
        assert (bundle / ".codex-plugin" / "plugin.json").is_file()

    def test_interface_moves_under_the_openai_extension(self) -> None:
        """The portable schema reserves the root for vendor-neutral fields."""
        out = to_portable_manifest({"name": "x", "version": "1", "interface": {"displayName": "X"}})
        assert out["extensions"]["com.openai"]["interface"] == {"displayName": "X"}
        assert "interface" not in out

    def test_path_declarations_are_dropped(self) -> None:
        """A portable package discovers skills and servers; declaring paths
        the host never reads is noise that reads as configuration."""
        out = to_portable_manifest(
            {"name": "x", "version": "1", "skills": "./skills/", "mcpServers": "./.mcp.json"}
        )
        assert "skills" not in out and "mcpServers" not in out

    def test_the_schema_is_declared(self, bundle: Path) -> None:
        assert json.loads((bundle / "plugin.json").read_text())["$schema"] == PLUGIN_SCHEMA


class TestPortableMcp:
    def test_mcp_json_has_no_leading_dot(self, bundle: Path) -> None:
        """`.mcp.json` is the Codex name; the portable layout wants `mcp.json`."""
        assert (bundle / "mcp.json").is_file()

    def test_every_server_declares_a_transport(self, bundle: Path) -> None:
        """The portable schema requires `type`; the Codex file omitted it,
        and an entry without one is not a valid server definition."""
        servers = json.loads((bundle / "mcp.json").read_text())["mcpServers"]
        assert servers, "bundle ships no MCP server"
        for name, entry in servers.items():
            assert entry.get("type") == MCP_TRANSPORT, f"{name} has no transport"

    def test_the_tool_profile_cap_survives_the_transform(self, bundle: Path) -> None:
        """Dropping `?profile=core` loads every tool, and a host with a hard
        cap truncates the list without saying so."""
        servers = json.loads((bundle / "mcp.json").read_text())["mcpServers"]
        assert any("profile=core" in e.get("url", "") for e in servers.values())

    def test_the_schema_is_declared(self) -> None:
        out = to_portable_mcp({"mcpServers": {"m": {"url": "https://x/mcp"}}})
        assert out["$schema"] == MCP_SCHEMA


class TestSkillsTravel:
    def test_skills_are_carried_into_the_bundle(self, bundle: Path) -> None:
        """The thing a *connector* cannot deliver and a bundle can. If this
        ever reaches zero the bundle still installs, silently, as tools only."""
        skills = list((bundle / "skills").glob("*/SKILL.md"))
        assert len(skills) >= 20, f"only {len(skills)} skills in the bundle"

    def test_no_bytecode_is_shipped(self, bundle: Path) -> None:
        assert not list(bundle.rglob("__pycache__"))


class TestArchives:
    @pytest.mark.parametrize("fmt", ["zip", "tar.gz"])
    def test_the_archive_has_one_plugin_folder_at_its_root(
        self, bundle: Path, tmp_path: Path, fmt: str
    ) -> None:
        """A host unpacks the archive and looks for a plugin directory. A
        flat archive of loose files has no plugin in it to install."""
        path = archive(bundle, fmt, tmp_path, "9.9.9")
        if fmt == "zip":
            names = zipfile.ZipFile(path).namelist()
        else:
            names = [m.name for m in tarfile.open(path).getmembers()]
        roots = {n.split("/")[0] for n in names}
        assert roots == {bundle.name}, f"expected one root, got {roots}"
        assert f"{bundle.name}/plugin.json" in names

    def test_the_version_is_in_the_filename(self, bundle: Path, tmp_path: Path) -> None:
        assert "9.9.9" in archive(bundle, "zip", tmp_path, "9.9.9").name


class TestSourceStillMatchesWhatWeTransform:
    """The packager reads two files by name. If the generator renames either,
    packaging breaks at upload time rather than here."""

    def test_the_codex_source_files_exist(self) -> None:
        source = REPO_ROOT / "integrations" / "codex"
        assert (source / ".codex-plugin" / "plugin.json").is_file()
        assert (source / ".mcp.json").is_file()
