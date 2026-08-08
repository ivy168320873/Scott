from flask import Flask

import signal_confidence_engine as confidence
from evolution_api import create_evolution_blueprint


def _ohlcv(symbol):
    closes = [100 + i * 0.25 for i in range(260)]
    return {
        "symbol": symbol,
        "opens": [value * 0.995 for value in closes],
        "highs": [value * 1.01 for value in closes],
        "lows": [value * 0.99 for value in closes],
        "closes": closes,
        "volumes": [1_000_000 + i * 1000 for i in range(260)],
        "timestamps": [f"2025-01-{(i % 28) + 1:02d}" for i in range(260)],
        "source": "unit",
        "is_demo": False,
    }


def test_blueprint_exposes_profile_and_evidence_evaluation(monkeypatch, tmp_path):
    db = str(tmp_path / "evolution.db")
    monkeypatch.setenv("USER_DATA_DB", db)
    confidence.init_db(db)
    app = Flask(__name__)
    app.register_blueprint(create_evolution_blueprint(_ohlcv))
    client = app.test_client()

    saved = client.put(
        "/api/evolution/risk-profile",
        json={"preset": "balanced", "account_size": 100_000, "min_signal_confidence": 40},
    )
    assert saved.status_code == 200
    assert saved.get_json()["profile"]["account_size"] == 100_000

    response = client.post(
        "/api/evolution/evaluate",
        json={"symbol": "NVDA", "portfolio": {"account_value": 100_000}},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["scorecard"]["evidence_coverage"] == 1
    assert payload["risk_gate"]["status"] in {"PASS", "REDUCE", "BLOCK"}
    assert "proposal" in payload
