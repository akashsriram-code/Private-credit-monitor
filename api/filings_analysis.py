from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler

from private_credit_monitor.filings_analysis import FILINGS_ANALYSIS_SESSIONS_PATH, run_live_analysis


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        _write_json(self, 200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw or "{}")
            session, email_delivery = run_live_analysis(payload, sessions_path=FILINGS_ANALYSIS_SESSIONS_PATH)
            _write_json(
                self,
                200,
                {
                    "session": asdict(session),
                    "email_status": email_delivery.status,
                    "email_error": email_delivery.error,
                },
            )
        except ValueError as exc:
            _write_json(self, 400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover
            _write_json(self, 500, {"error": str(exc)})
