"""`forge upload`, `uploads`, `download` and `link`, against a mocked gateway."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cli.forge_cli import files as cli_files
from cli.forge_cli.main import main

UPLOAD = {
    "id": "u1",
    "status": "pending",
    "source": "api",
    "created_at": "2026-11-02T10:00:00Z",
    "files": [
        {"name": "board/board.kicad_pcb", "wp_type": "pcb_layout", "error": None,
         "work_product_id": None},
        {"name": "board/notes.exe", "wp_type": "", "error": "Unsupported file type",
         "work_product_id": None},
    ],
}  # fmt: skip


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/uploads" and request.method == "POST":
            return httpx.Response(201, json=UPLOAD)
        if path == "/v1/uploads":
            return httpx.Response(200, json=[UPLOAD])
        if path.endswith("/link"):
            return httpx.Response(
                200,
                json={"via": "gateway", "url": None, "path": "/v1/links/tok.sig",
                      "expires_at": "2026-11-02T11:00:00+00:00", "name": "a.step"},
            )  # fmt: skip
        if path.endswith("/file"):
            return httpx.Response(
                200,
                content=b"ISO-10303-21;",
                headers={"content-disposition": 'attachment; filename="bracket.step"'},
            )
        return httpx.Response(404, json={"detail": "nope"})

    real = httpx.Client

    def client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(cli_files.httpx, "Client", client)
    monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://gw.test:8000")
    return seen


def test_a_folder_uploads_with_its_structure_and_without_its_junk(
    tmp_path: Path, gateway: list[httpx.Request], capsys: pytest.CaptureFixture[str]
) -> None:
    board = tmp_path / "board"
    (board / ".git").mkdir(parents=True)
    (board / "board.kicad_pcb").write_text("(kicad_pcb)")
    (board / ".git" / "HEAD").write_text("ref")
    (board / ".DS_Store").write_text("x")
    main(["upload", str(board), "--project", "p1"])
    body = gateway[0].content
    assert b'filename="board/board.kicad_pcb"' in body
    assert b".git" not in body and b".DS_Store" not in body
    assert gateway[0].headers["X-MetaForge-Surface"] == "cli"
    out = capsys.readouterr().out
    assert "left out  board/notes.exe: Unsupported file type" in out
    assert "forge approvals approve upload:u1" in out


def test_now_imports_at_once(tmp_path: Path, gateway: list[httpx.Request]) -> None:
    f = tmp_path / "a.step"
    f.write_text("ISO-10303-21;")
    main(["upload", str(f), "--now"])
    assert b'name="approve"\r\n\r\ntrue' in gateway[0].content


def test_download_saves_under_the_gateways_name_and_will_not_overwrite(
    tmp_path: Path, gateway: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    main(["download", "wp-1"])
    assert (tmp_path / "bracket.step").read_bytes() == b"ISO-10303-21;"
    with pytest.raises(SystemExit) as exc:
        main(["download", "wp-1"])
    assert exc.value.code == cli_files.EXIT_USAGE


def test_link_prints_an_absolute_url(
    gateway: list[httpx.Request], capsys: pytest.CaptureFixture[str]
) -> None:
    main(["link", "wp-1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["url"] == "http://gw.test:8000/v1/links/tok.sig"


def test_uploads_lists(gateway: list[httpx.Request], capsys: pytest.CaptureFixture[str]) -> None:
    main(["uploads", "--json"])
    assert json.loads(capsys.readouterr().out)[0]["id"] == "u1"


def test_a_missing_path_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli_files.collect(["/no/such/file.step"])
    assert exc.value.code == cli_files.EXIT_USAGE
