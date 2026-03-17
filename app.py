from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from private_credit_monitor.filings_analysis import FILINGS_ANALYSIS_SESSIONS_PATH, load_persisted_sessions, run_live_analysis


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = ROOT_DIR / "config"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


@app.get("/")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.post("/api/filings-analysis")
def filings_analysis():
    try:
        payload = request.get_json(silent=True) or {}
        session, email_delivery = run_live_analysis(payload, sessions_path=FILINGS_ANALYSIS_SESSIONS_PATH)
        return jsonify(
            {
                "session": asdict(session),
                "email_status": email_delivery.status,
                "email_error": email_delivery.error,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc)}), 500


@app.get("/api/filings-analysis-sessions")
def filings_analysis_sessions():
    try:
        sessions = load_persisted_sessions(sessions_path=FILINGS_ANALYSIS_SESSIONS_PATH)
        return jsonify({"sessions": sessions})
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc), "sessions": []}), 500


@app.get("/data/<path:filename>")
def data_file(filename: str):
    return send_from_directory(DATA_DIR, filename)


@app.get("/config/<path:filename>")
def config_file(filename: str):
    return send_from_directory(CONFIG_DIR, filename)


@app.get("/<path:filename>")
def root_asset(filename: str):
    target = ROOT_DIR / filename
    if target.is_file():
        return send_from_directory(ROOT_DIR, filename)
    return send_from_directory(ROOT_DIR, "index.html")
