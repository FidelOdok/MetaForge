"""Lightweight HTTP wrapper around the OCCT converter.

Runs as a microservice inside docker-compose so the gateway can call
it over the network instead of shelling out ``docker run``.

Endpoints:
    POST /convert  — multipart upload (file + quality) → GLB + metadata
    GET  /health   — liveness probe
"""

from __future__ import annotations

import base64
import json
import logging
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from convert import QUALITY_TIERS, convert

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("occt-server")

PORT = 8100


def _parse_multipart(headers: dict, body: bytes) -> tuple[bytes, str]:
    """Extract file bytes and filename from a multipart/form-data body."""
    content_type = headers.get("Content-Type", "")
    if "boundary=" not in content_type:
        raise ValueError("Missing boundary in Content-Type")

    boundary = content_type.split("boundary=")[1].strip()
    # Handle quoted boundary
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]

    parts = body.split(f"--{boundary}".encode())
    for part in parts:
        if b"filename=" not in part:
            continue
        # Split headers from body at double newline
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        part_headers = part[:header_end].decode("utf-8", errors="replace")
        part_body = part[header_end + 4 :]
        # Strip trailing \r\n-- if present
        if part_body.endswith(b"\r\n"):
            part_body = part_body[:-2]
        if part_body.endswith(b"--"):
            part_body = part_body[:-2]
        if part_body.endswith(b"\r\n"):
            part_body = part_body[:-2]

        # Extract filename
        filename = "upload.step"
        for segment in part_headers.split(";"):
            segment = segment.strip().split("\r\n")[0].split("\n")[0]
            if segment.startswith("filename="):
                raw = segment.split("=", 1)[1].strip().strip('"')
                filename = raw.split('"')[0]  # stop at closing quote
                break

        return part_body, filename

    raise ValueError("No file part found in multipart body")


class ConvertHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/convert":
            self._json_response(404, {"error": "not found"})
            return

        # Parse quality from query string
        qs = parse_qs(urlparse(self.path).query)
        quality = qs.get("quality", ["standard"])[0]
        if quality not in QUALITY_TIERS:
            self._json_response(400, {"error": f"invalid quality: {quality}"})
            return

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response(400, {"error": "empty body"})
            return
        body = self.rfile.read(content_length)

        # Parse multipart
        try:
            file_bytes, filename = _parse_multipart(dict(self.headers), body)
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
            return

        # Convert in a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / filename
            input_path.write_bytes(file_bytes)

            try:
                metadata = convert(str(input_path), quality, tmpdir)
            except Exception as exc:
                logger.error("Conversion failed: %s", exc)
                self._json_response(500, {"error": str(exc)})
                return

            glb_path = Path(tmpdir) / "model.glb"
            glb_bytes = glb_path.read_bytes()

        result = {
            "metadata": metadata,
            "glb_base64": base64.b64encode(glb_bytes).decode("ascii"),
        }
        self._json_response(200, result)

    def _json_response(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info(fmt, *args)


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), ConvertHandler)
    logger.info("OCCT converter listening on port %d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
