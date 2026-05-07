"""Unit tests for ``forge sources list/show/delete`` (MET-411).

The CLI handlers are exercised via ``unittest.mock.patch`` against
``ForgeClient.list_sources / get_source / delete_source``. No real
HTTP, gateway, LightRAG, or Postgres is required — these tests run
under the bare ``[dev]`` install in CI.

Tests cover:
* default-call: no filters → client invoked with defaults, table printed.
* filter pass-through: --type, --project, --limit forwarded verbatim.
* table render: 3 rows produce a 4-line table (header + separator + 3).
* show: client.get_source called with the source id and detail printed.
* delete --yes: skips the prompt and calls delete_source.
* delete (no --yes, "n"): prompt shown, delete NOT called.
* delete (no --yes, "y"): prompt shown, delete called.
* empty-state list: friendly message, exit 0.
* show 404: ``ForgeClientNotFound`` → actionable message, non-zero exit,
  no stack trace.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cli.forge_cli.client import ForgeClientNotFound
from cli.forge_cli.main import build_parser, main
from cli.forge_cli.sources import _row_from_summary

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_SOURCES = [
    {
        "sourcePath": "uat://decisions/foo.md",
        "knowledgeType": "design_decision",
        "fragmentCount": 3,
        "indexedAt": "2026-05-01T12:00:00+00:00",
        "metadata": {"author": "mech"},
    },
    {
        "sourcePath": "uat://components/stm32.md",
        "knowledgeType": "component",
        "fragmentCount": 1,
        "indexedAt": "2026-05-01T11:00:00+00:00",
        "metadata": {"vendor": "st"},
    },
    {
        "sourcePath": "uat://failures/insert-pullout.md",
        "knowledgeType": "failure",
        "fragmentCount": 2,
        "indexedAt": "2026-05-01T10:00:00+00:00",
        "metadata": {},
    },
]

_LIST_RESPONSE = {"sources": _SAMPLE_SOURCES, "total": len(_SAMPLE_SOURCES)}
_EMPTY_RESPONSE: dict[str, Any] = {"sources": [], "total": 0}


def _detail_response(source_path: str = "uat://decisions/foo.md") -> dict[str, Any]:
    return {
        "sourcePath": source_path,
        "knowledgeType": "design_decision",
        "fragmentCount": 2,
        "indexedAt": "2026-05-01T12:00:00+00:00",
        "metadata": {"author": "mech"},
        "chunks": [
            {
                "content": "Decision body for foo",
                "heading": "Decision",
                "chunk_index": 0,
                "total_chunks": 2,
                "source_path": source_path,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


class TestSourcesParserWiring:
    def test_sources_list_parses_no_args(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sources", "list"])
        assert args.command == "sources"
        assert args.sources_command == "list"
        assert args.knowledge_type is None
        assert args.project_id is None
        assert args.limit == 100

    def test_sources_list_parses_filters(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "sources",
                "list",
                "--type",
                "design_decision",
                "--project",
                "11111111-1111-1111-1111-111111111111",
                "--limit",
                "25",
            ]
        )
        assert args.knowledge_type == "design_decision"
        assert args.project_id == "11111111-1111-1111-1111-111111111111"
        assert args.limit == 25

    def test_sources_show_parses_id(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sources", "show", "uat://foo"])
        assert args.sources_command == "show"
        assert args.source_id == "uat://foo"

    def test_sources_delete_parses_yes(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sources", "delete", "uat://foo", "--yes"])
        assert args.sources_command == "delete"
        assert args.source_id == "uat://foo"
        assert args.yes is True


# ---------------------------------------------------------------------------
# Row projection helper
# ---------------------------------------------------------------------------


class TestRowFromSummary:
    def test_camel_case_response(self) -> None:
        row = _row_from_summary(_SAMPLE_SOURCES[0])
        assert row["source_path"] == "uat://decisions/foo.md"
        assert row["knowledge_type"] == "design_decision"
        assert row["fragment_count"] == 3
        assert row["indexed_at"].startswith("2026-05-01")

    def test_snake_case_response(self) -> None:
        snake = {
            "source_path": "uat://x.md",
            "knowledge_type": "session",
            "fragment_count": 1,
            "indexed_at": "2026-05-01T00:00:00+00:00",
        }
        row = _row_from_summary(snake)
        assert row["source_path"] == "uat://x.md"
        assert row["fragment_count"] == 1


# ---------------------------------------------------------------------------
# forge sources list
# ---------------------------------------------------------------------------


def _patch_client(method_name: str, return_value: Any) -> Any:
    """Patch a single ``ForgeClient`` method while leaving the rest intact."""
    return patch(f"cli.forge_cli.client.ForgeClient.{method_name}", return_value=return_value)


class TestSourcesListCli:
    def test_sources_list_no_filters_calls_client_with_defaults(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch(
            "cli.forge_cli.client.ForgeClient.list_sources",
            return_value=_LIST_RESPONSE,
        ) as mock_list:
            main(["sources", "list"])

        mock_list.assert_called_once()
        kwargs = mock_list.call_args.kwargs
        assert kwargs.get("knowledge_type") is None
        assert kwargs.get("project_id") is None
        assert kwargs.get("limit") == 100

        captured = capsys.readouterr()
        assert "SOURCE_PATH" in captured.out
        assert "uat://decisions/foo.md" in captured.out

    def test_sources_list_filters_passed_through(self, capsys: pytest.CaptureFixture[str]) -> None:
        project_uuid = "11111111-1111-1111-1111-111111111111"
        with patch(
            "cli.forge_cli.client.ForgeClient.list_sources",
            return_value=_LIST_RESPONSE,
        ) as mock_list:
            main(
                [
                    "sources",
                    "list",
                    "--type",
                    "design_decision",
                    "--project",
                    project_uuid,
                    "--limit",
                    "25",
                ]
            )

        kwargs = mock_list.call_args.kwargs
        assert kwargs["knowledge_type"] == "design_decision"
        assert kwargs["project_id"] == project_uuid
        assert kwargs["limit"] == 25

    def test_sources_list_renders_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "cli.forge_cli.client.ForgeClient.list_sources",
            return_value=_LIST_RESPONSE,
        ):
            main(["sources", "list"])

        captured = capsys.readouterr()
        # ``format_table`` renders header + separator + N rows. The
        # structlog dev-renderer prints log lines on the same stream,
        # so we filter to the table-shaped lines only.
        body_lines = [line for line in captured.out.splitlines() if line.strip()]
        # Locate the header row, then assert exactly header + sep + 3 rows
        # follow it. 4 lines in the spec wording = header + 3 data rows
        # (separator is a render artefact of format_table).
        header_idx = next(i for i, line in enumerate(body_lines) if "SOURCE_PATH" in line)
        table_lines = body_lines[header_idx:]
        assert len(table_lines) == 5  # header + separator + 3 data rows
        # Header carries every documented column.
        assert "SOURCE_PATH" in table_lines[0]
        assert "KNOWLEDGE_TYPE" in table_lines[0]
        assert "FRAGMENT_COUNT" in table_lines[0]
        assert "INDEXED_AT" in table_lines[0]
        # Each of the 3 data rows is present.
        for src in (
            "uat://decisions/foo.md",
            "uat://components/stm32.md",
            "uat://failures/insert-pullout.md",
        ):
            assert any(src in line for line in table_lines), src

    def test_sources_list_handles_empty_response(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "cli.forge_cli.client.ForgeClient.list_sources",
            return_value=_EMPTY_RESPONSE,
        ):
            main(["sources", "list"])

        captured = capsys.readouterr()
        assert "No sources ingested yet" in captured.out


# ---------------------------------------------------------------------------
# forge sources show
# ---------------------------------------------------------------------------


class TestSourcesShowCli:
    def test_sources_show_calls_get_source(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "cli.forge_cli.client.ForgeClient.get_source",
            return_value=_detail_response(),
        ) as mock_get:
            main(["sources", "show", "uat://decisions/foo.md"])

        mock_get.assert_called_once_with("uat://decisions/foo.md")

        captured = capsys.readouterr()
        assert "uat://decisions/foo.md" in captured.out
        assert "design_decision" in captured.out
        assert "metadata" in captured.out
        assert "chunks" in captured.out

    def test_sources_show_handles_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "cli.forge_cli.client.ForgeClient.get_source",
            side_effect=ForgeClientNotFound("No knowledge source registered for 'uat://ghost'"),
        ):
            with pytest.raises(SystemExit) as excinfo:
                main(["sources", "show", "uat://ghost"])

        assert excinfo.value.code != 0
        captured = capsys.readouterr()
        # Actionable message on stderr, no stack trace.
        assert "Error" in captured.err
        assert "uat://ghost" in captured.err
        assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# forge sources delete
# ---------------------------------------------------------------------------


class TestSourcesDeleteCli:
    def test_sources_delete_with_yes_skips_confirm(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch(
                "cli.forge_cli.client.ForgeClient.delete_source",
                return_value={"sourcePath": "uat://foo", "deletedChunks": 4},
            ) as mock_delete,
            patch("builtins.input") as mock_input,
        ):
            main(["sources", "delete", "uat://foo", "--yes"])

        mock_delete.assert_called_once_with("uat://foo")
        # --yes must NOT prompt. Any input() call is a regression.
        mock_input.assert_not_called()

        captured = capsys.readouterr()
        assert "Deleted 4" in captured.out

    def test_sources_delete_without_yes_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch(
                "cli.forge_cli.client.ForgeClient.delete_source",
                return_value={"sourcePath": "uat://foo", "deletedChunks": 0},
            ) as mock_delete,
            patch("builtins.input", return_value="n") as mock_input,
        ):
            main(["sources", "delete", "uat://foo"])

        mock_input.assert_called_once()
        mock_delete.assert_not_called()

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_sources_delete_confirmed_calls_client(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch(
                "cli.forge_cli.client.ForgeClient.delete_source",
                return_value={"sourcePath": "uat://foo", "deletedChunks": 7},
            ) as mock_delete,
            patch("builtins.input", return_value="y") as mock_input,
        ):
            main(["sources", "delete", "uat://foo"])

        mock_input.assert_called_once()
        mock_delete.assert_called_once_with("uat://foo")

        captured = capsys.readouterr()
        assert "Deleted 7" in captured.out

    def test_sources_delete_handles_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "cli.forge_cli.client.ForgeClient.delete_source",
            side_effect=ForgeClientNotFound("No knowledge source registered for 'uat://ghost'"),
        ):
            with pytest.raises(SystemExit) as excinfo:
                main(["sources", "delete", "uat://ghost", "--yes"])

        assert excinfo.value.code != 0
        captured = capsys.readouterr()
        assert "uat://ghost" in captured.err
        assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# ForgeClient sources HTTP wiring
# ---------------------------------------------------------------------------


def _mock_response(data: dict[str, Any], status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


class TestForgeClientSourcesHttp:
    @patch("cli.forge_cli.client.httpx.Client")
    def test_list_sources_passes_filters(self, mock_client_cls: MagicMock) -> None:
        from cli.forge_cli.client import ForgeClient

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response(_LIST_RESPONSE)
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        result = fc.list_sources(
            knowledge_type="design_decision",
            project_id="11111111-1111-1111-1111-111111111111",
            limit=25,
        )

        assert result["total"] == 3
        params = mock_ctx.get.call_args.kwargs["params"]
        assert params["knowledgeType"] == "design_decision"
        assert params["projectId"] == "11111111-1111-1111-1111-111111111111"
        assert params["limit"] == 25
        assert params["offset"] == 0

    @patch("cli.forge_cli.client.httpx.Client")
    def test_get_source_url_encodes_path(self, mock_client_cls: MagicMock) -> None:
        from cli.forge_cli.client import ForgeClient

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response(_detail_response("uat://x.md"))
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        fc.get_source("uat://x.md")
        url = mock_ctx.get.call_args.args[0]
        # The colon and slashes in `uat://x.md` MUST be percent-encoded
        # so the FastAPI ``{path:path}`` matcher gets the raw value back.
        assert "uat%3A%2F%2Fx.md" in url

    @patch("cli.forge_cli.client.httpx.Client")
    def test_get_source_404_raises_not_found(self, mock_client_cls: MagicMock) -> None:
        from cli.forge_cli.client import ForgeClient

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response({"detail": "missing"}, status_code=404)
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        with pytest.raises(ForgeClientNotFound):
            fc.get_source("uat://ghost")

    @patch("cli.forge_cli.client.httpx.Client")
    def test_delete_source_round_trips_envelope(self, mock_client_cls: MagicMock) -> None:
        from cli.forge_cli.client import ForgeClient

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.delete.return_value = _mock_response(
            {"sourcePath": "uat://foo", "deletedChunks": 2}
        )
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        result = fc.delete_source("uat://foo")
        assert result["deletedChunks"] == 2
        mock_ctx.delete.assert_called_once()
