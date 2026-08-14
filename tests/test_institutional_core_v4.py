import sqlite3
from datetime import date, datetime, timedelta, timezone

import database_backup
import exchange_calendar
import market_clock
import monitor
import operational_readiness
import position_sizing_engine
import signal_confidence_engine as confidence
import tpex_flow


def test_nyse_holiday_and_scheduled_early_close_are_enforced():
    independence_observed = exchange_calendar.exchange_day("US", date(2026, 7, 3), env={})
    assert independence_observed["is_trading_day"] is False
    assert independence_observed["holiday_name"] == "Independence Day"

    early = exchange_calendar.exchange_day("US", date(2026, 11, 27), env={})
    assert early["is_trading_day"] is True
    assert early["early_close"] is True
    assert early["close_time"] == "13:00"

    session = market_clock.market_session(
        "NVDA", now=datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)
    )
    assert session["state"] == "POST"
    assert session["regular_close"].endswith("13:00:00-05:00")
    assert market_clock.daily_bar_is_complete(
        "NVDA", "2026-11-27", now=datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)
    ) is True

    monitor_time = datetime(2028, 11, 24, 13, 30)
    assert monitor.is_market_open(monitor_time) is False
    assert monitor.is_after_hours(monitor_time) is True
    assert monitor.market_status(monitor_time)["early_close"] is True


def test_twse_official_holiday_parser_and_emergency_override():
    parsed = exchange_calendar.parse_twse_holidays(
        {
            "fields": ["日期", "名稱", "說明"],
            "data": [
                ["2026-02-13", "春節前最後交易日", "照常交易"],
                ["2026-02-16", "農曆除夕", "市場無交易"],
            ],
        },
        2026,
    )
    assert parsed[date(2026, 2, 13)]["is_trading_day"] is True
    assert parsed[date(2026, 2, 16)]["is_trading_day"] is False

    override = exchange_calendar.exchange_day(
        "TW",
        date(2026, 7, 10),
        env={"MARKET_CLOSED_DATES_TW": "2026-07-10"},
    )
    assert override["is_trading_day"] is False
    assert override["precision"] == "EXPLICIT_OVERRIDE"


def test_tpex_official_flow_supports_two_symbols():
    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    institution_row = ["6488", "環球晶"] + ["0"] * 22
    institution_row[10] = "1,000,000"
    institution_row[13] = "200,000"
    institution_row[22] = "-50,000"
    institution_row[23] = "1,150,000"
    margin_row = ["6488", "環球晶"] + ["0"] * 13
    margin_row[2] = "10,000"
    margin_row[6] = "10,200"
    margin_row[10] = "500"
    margin_row[14] = "450"

    class Session:
        def get(self, url, **_kwargs):
            if "3itrade_hedge_result" in url:
                return Response({"tables": [{"fields": ["代號"] * 24, "data": [institution_row]}]})
            return Response({"tables": [{"fields": ["代號"] * 15, "data": [margin_row]}]})

    tpex_flow._CACHE.clear()
    result = tpex_flow.get_tpex_flow(
        "6488.TWO",
        session=Session(),
        now=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert result["source"] == "TPEX_OFFICIAL"
    assert result["institutional"]["total_net_lots"] == 1150
    assert result["institutional"]["dealer_net"] == -50000
    assert result["margin"]["margin_change"] == 200
    assert result["margin"]["short_change"] == -50
    assert result["score_adjustment"] == 0


def test_calibration_clusters_same_day_and_gates_kelly():
    correlated = [
        {
            "id": 1,
            "signal_date": "2026-08-01",
            "return_5d": 2,
            "net_return_5d": 1.8,
            "prediction_probability": 0.7,
            "was_correct": 1,
        },
        {
            "id": 2,
            "signal_date": "2026-08-01",
            "return_5d": -1,
            "net_return_5d": -1.2,
            "prediction_probability": 0.7,
            "was_correct": 0,
        },
    ]
    clustered = confidence._compute_stats("BUY", correlated)
    assert clustered["raw_evaluated_size"] == 2
    assert clustered["independent_sample_size"] == 1
    assert clustered["kelly_eligible"] is False

    start = date(2026, 1, 1)
    qualified = []
    for index in range(60):
        won = index < 51
        qualified.append(
            {
                "id": index + 1,
                "signal_date": (start + timedelta(days=index)).isoformat(),
                "return_5d": 2 if won else -1,
                "net_return_5d": 1.8 if won else -1.2,
                "prediction_probability": 0.8,
                "was_correct": int(won),
            }
        )
    stats = confidence._compute_stats("BUY", qualified)
    assert stats["independent_sample_size"] == 60
    assert stats["credible_interval_95"][0] > 50
    assert stats["kelly_eligible"] is True


def test_uncalibrated_position_never_exceeds_three_percent():
    closes = [100 + index for index in range(30)]
    result = position_sizing_engine.run_position_sizing(
        {
            "decision": "BUY",
            "top_tier_score": 90,
            "market_regime": "RISK_ON",
            "risk_budget_mult": 1,
            "chase_risk_score": 20,
            "ohlcv": {
                "closes": closes,
                "highs": [value + 2 for value in closes],
                "lows": [value - 2 for value in closes],
            },
            "win_rate_estimate": 0.99,
            "kelly_enabled": False,
        }
    )
    assert result["kelly_enabled"] is False
    assert result["kelly_fraction"] == 0
    assert max(value or 0 for value in result["sizing_breakdown"].values()) <= 3
    assert result["position_size_level"] in {"TINY", "SMALL"}


def test_verified_backup_and_readiness_are_auditable(tmp_path):
    db_path = tmp_path / "user_data.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE test(value TEXT)")
        connection.execute("INSERT INTO test(value) VALUES('kept')")
    now = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    backup = database_backup.backup_database(str(db_path), now=now)
    assert backup["ok"] is True
    status = database_backup.backup_status(str(db_path), now=now)
    assert status["ok"] is True
    with sqlite3.connect(tmp_path / "backups" / backup["filename"]) as restored:
        assert restored.execute("SELECT value FROM test").fetchone()[0] == "kept"

    readiness = operational_readiness.build_readiness(
        str(db_path),
        env={
            "RAILWAY_ENVIRONMENT": "production",
            "RAILWAY_VOLUME_MOUNT_PATH": str(tmp_path),
            "ACCESS_CODE": "set",
            "SECRET_KEY": "set",
        },
        now=now,
    )
    assert readiness["operational_ready"] is True
    assert readiness["status"] == "DEGRADED"
    assert not [
        item for item in readiness["checks"]
        if item["critical"] and item["status"] == "FAIL"
    ]
