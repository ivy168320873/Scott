from scott_evolution import paper_trading


def _gate():
    return {"allowed": True, "status": "PASS", "max_shares": 10}


def _payload(**patch):
    value = {
        "client_order_id": "client-1",
        "symbol": "NVDA",
        "decision": "BUY",
        "entry_price": 100,
        "stop_price": 95,
        "target_price": 110,
        "qty": 10,
        "confidence_score": 80,
        "evidence_coverage": 1,
        "risk_profile": "balanced",
        "signal_record_id": 12,
    }
    value.update(patch)
    return value


def test_paper_trade_is_persistent_and_idempotent(tmp_path):
    db = str(tmp_path / "paper.db")
    trade = paper_trading.open_trade(_payload(), _gate(), db_path=db)
    duplicate = paper_trading.open_trade(_payload(), _gate(), db_path=db)
    assert trade["status"] == "OPEN"
    assert duplicate["id"] == trade["id"]
    assert duplicate["deduplicated"] is True
    assert paper_trading.list_trades(status="OPEN", db_path=db)[0]["id"] == trade["id"]


def test_same_bar_stop_is_assumed_before_target_and_outcome_syncs(tmp_path):
    db = str(tmp_path / "paper.db")
    trade = paper_trading.open_trade(_payload(), _gate(), db_path=db)
    synced = []
    result = paper_trading.mark_to_market(
        {"NVDA": {"open": 94, "high": 111, "low": 93, "close": 108}},
        outcome_hook=lambda closed: synced.append(closed),
        db_path=db,
    )
    closed = result["closed"][0]
    assert closed["exit_reason"] == "STOP"
    assert closed["exit_price"] == 94
    assert closed["return_pct"] == -6
    assert synced[0]["id"] == trade["id"]
    assert paper_trading.performance_summary(db)["win_rate"] == 0


def test_next_open_shadow_trade_waits_then_applies_costs(tmp_path):
    db = str(tmp_path / "paper.db")
    trade = paper_trading.open_trade(
        _payload(client_order_id="next-open-1", fill_model="NEXT_OPEN"),
        _gate(),
        db_path=db,
    )
    assert trade["status"] == "PENDING"
    assert trade["signal_price"] == 100

    marked = paper_trading.mark_to_market(
        {"NVDA": {"date": "2026-08-14", "open": 102, "high": 104, "low": 101, "close": 103}},
        db_path=db,
    )
    filled = marked["updated"][0]
    assert filled["status"] == "OPEN"
    assert filled["entry_price"] == 102.102
    assert filled["entry_cost_pct"] == 0.1
    assert filled["entry_filled_at"]
    assert paper_trading.performance_summary(db)["pending_trades"] == 0
