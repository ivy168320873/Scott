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
