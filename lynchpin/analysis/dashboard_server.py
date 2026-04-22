"""Lightweight local server for the ecosystem dashboard."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core.io import resolve_analysis_path
from .ecosystem_dashboard import run_ecosystem_dashboard


def serve_dashboard(
    *,
    spec_path: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    refresh_on_request: bool = False,
) -> None:
    json_path = Path(resolve_analysis_path("ecosystem_dashboard.json"))
    html_path = Path(resolve_analysis_path("ecosystem_dashboard.html"))

    def rebuild() -> None:
        run_ecosystem_dashboard(spec_path, json_path, html_path)

    if refresh_on_request or not json_path.exists() or not html_path.exists():
        rebuild()

    class Handler(BaseHTTPRequestHandler):
        def _write(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if refresh_on_request and parsed.path in {"/", "/dashboard", "/api/dashboard.json"}:
                rebuild()

            if parsed.path in {"/", "/dashboard"}:
                body = html_path.read_bytes()
                self._write(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/dashboard.json":
                body = json_path.read_bytes()
                self._write(HTTPStatus.OK, body, "application/json; charset=utf-8")
                return
            if parsed.path == "/api/rebuild":
                rebuild()
                payload = {
                    "status": "ok",
                    "json": str(json_path),
                    "html": str(html_path),
                    "refresh_on_request": refresh_on_request,
                }
                body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
                self._write(HTTPStatus.OK, body, "application/json; charset=utf-8")
                return
            if parsed.path == "/healthz":
                self._write(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
                return
            if parsed.path == "/api/open":
                target = parse_qs(parsed.query).get("path", [""])[0]
                payload = {"requested_path": target, "exists": Path(target).exists() if target else False}
                self._write(
                    HTTPStatus.OK,
                    json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"dashboard server listening on http://{host}:{port}/")
    print(f"dashboard html: {html_path}")
    print(f"dashboard json: {json_path}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
