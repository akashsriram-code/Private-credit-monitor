from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from private_credit_monitor.filings_analysis import FILINGS_ANALYSIS_SESSIONS_PATH, load_persisted_sessions


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        _write_json(self, 200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        try:
            sessions = load_persisted_sessions(sessions_path=FILINGS_ANALYSIS_SESSIONS_PATH)
            _write_json(self, 200, {"sessions": sessions})
        except Exception as exc:  # pragma: no cover
            _write_json(self, 500, {"error": str(exc), "sessions": []})
