from datetime import datetime, timezone

import data_provider as data
import economic_calendar as calendar
import market_breadth_engine as breadth
import market_clock
import institutional_guard
import signal_confidence_engine as confidence
import twse_flow
from market_intelligence.filings import fetch_sec_filings


def _daily(dates):
    closes = [100 + index for index in range(len(dates))]
    return {
        "dates": list(dates),
        "timestamps": [
            int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
            for day in dates
        ],
        "opens": [price - 1 for price in closes],
        "highs": [price + 1 for price in closes],
        "lows": [price - 2 for price in closes],
        "closes": closes,
        "volumes": [1_000_000] * len(closes),
        "source": "unit",
        "is_demo": False,
    }


def test_incomplete_us_daily_bar_is_excluded_until_regular_close():
    raw = _daily(["2026-08-13", "2026-08-14"])
    during = market_clock.annotate_completed_bars(
        "NVDA", raw, now=datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    )
    assert during["closes"] == [100]
    assert during["excluded_incomplete_bar"] is True
    assert during["incomplete_bar"]["date"] == "2026-08-14"

    after = market_clock.annotate_completed_bars(
        "NVDA", raw, now=datetime(2026, 8, 14, 21, tzinfo=timezone.utc)
    )
    assert after["closes"] == [100, 101]
    assert after["excluded_incomplete_bar"] is False


def test_quote_contract_exposes_feed_scope_spread_and_freshness():
    now = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    quote = data._quote_contract(
        "NVDA",
        source="alpaca_snapshot",
        last=180,
        bid=179.98,
        ask=180.02,
        timestamp=now,
        feed="iex",
        now=now,
    )
    assert quote["feed_scope"] == "SINGLE_EXCHANGE_IEX"
    assert quote["spread_bps"] == 2.22
    assert quote["signal_usable"] is True
    assert quote["execution_ready"] is False

    sip = data._quote_contract(
        "NVDA",
        source="alpaca_snapshot",
        last=180,
        bid=179.98,
        ask=180.02,
        timestamp=now,
        feed="sip",
        now=now,
    )
    assert sip["feed_scope"] == "CONSOLIDATED_US_SIP"
    assert sip["execution_ready"] is True


def test_future_bars_are_removed_and_flagged_as_data_integrity_error():
    raw = _daily(["2026-08-13", "2026-08-15", "2026-08-16"])
    result = market_clock.annotate_completed_bars(
        "NVDA", raw, now=datetime(2026, 8, 14, 21, tzinfo=timezone.utc)
    )
    assert result["dates"] == ["2026-08-13"]
    assert result["last_bar_complete"] is True
    assert result["quality_flags"] == ["FUTURE_BAR"]
    assert len(result["excluded_bars"]) == 2


def test_stale_open_market_quote_blocks_bullish_signal():
    now = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    guard = institutional_guard.assess_signal_readiness(
        "NVDA",
        ohlcv={"closes": [100], "last_bar_complete": True},
        data_quality={"data_status": "OK", "is_demo": False},
        quote={"ok": True, "last": 100, "is_stale": True, "age_seconds": 3600},
        now=now,
    )
    assert guard["status"] == "BLOCKED"
    assert guard["max_bullish_decision"] == "WATCH"


def _trend(up=True):
    if up:
        closes = [100 + index * 0.5 for index in range(220)]
    else:
        closes = [300 - index * 0.5 for index in range(220)]
    return {"closes": closes, "is_demo": False, "source": "unit"}


def test_cross_sectional_breadth_distinguishes_broad_strength_from_weakness():
    symbols = tuple(f"T{index}" for index in range(8))
    strong_fn = lambda _symbol: _trend(True)
    weak_fn = lambda _symbol: _trend(False)
    strong = breadth.run_market_breadth(
        strong_fn, symbols=symbols, min_coverage=6
    )
    weak = breadth.run_market_breadth(
        weak_fn, symbols=symbols, min_coverage=6
    )
    assert strong["status"] == "OK"
    assert strong["breadth_score"] > 75
    assert weak["breadth_score"] < 25
    assert strong["is_exchange_wide"] is False


def test_fomc_parser_uses_source_page_dates_not_static_calendar():
    html = """
    <div class="panel panel-default"><h4><a>2026 FOMC Meetings</a></h4>
      <div class="row fomc-meeting">
        <div class="fomc-meeting__month"><strong>January</strong></div>
        <div class="fomc-meeting__date">27-28</div>
        <div class="fomc-meeting__minutes">(Released February 18, 2026)</div>
      </div>
      <div class="row fomc-meeting">
        <div class="fomc-meeting__month"><strong>March</strong></div>
        <div class="fomc-meeting__date">17-18*</div>
      </div>
    </div>
    <div class="panel panel-default"><h4><a>2025 FOMC Meetings</a></h4></div>
    """
    events = calendar._parse_fomc(html, years=(2026,))
    assert [event["date"] for event in events] == ["2026-01-28", "2026-03-18"]
    assert all(event["source"] == "Federal Reserve" for event in events)


def test_bayesian_calibration_has_honest_interval_and_brier_score():
    prior = confidence._beta_posterior(0, 0)
    assert prior["probability_pct"] == 50
    assert prior["credible_interval_95"] == [2.5, 97.5]

    rows = [
        {"id": 1, "return_5d": 2, "net_return_5d": 1.8, "prediction_probability": .7, "was_correct": 1},
        {"id": 2, "return_5d": -1, "net_return_5d": -1.2, "prediction_probability": .6, "was_correct": 0},
        {"id": 3, "return_5d": 3, "net_return_5d": 2.8, "prediction_probability": .8, "was_correct": 1},
        {"id": 4, "return_5d": 1, "net_return_5d": .8, "prediction_probability": .4, "was_correct": 1},
    ]
    stats = confidence._compute_stats("BUY", rows)
    assert stats["probability_5d_pct"] == 66.67
    assert stats["credible_interval_95"][0] < stats["probability_5d_pct"]
    assert stats["credible_interval_95"][1] > stats["probability_5d_pct"]
    assert stats["brier_score"] == 0.2125


def test_sec_structured_filing_marks_dilution_risk_from_primary_source():
    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class Session:
        def get(self, url, **_kwargs):
            if url.endswith("company_tickers.json"):
                return Response({
                    "0": {"ticker": "POET", "cik_str": 1437424, "title": "POET Technologies Inc."}
                })
            return Response({
                "filings": {"recent": {
                    "form": ["424B5"],
                    "filingDate": ["2026-08-13"],
                    "accessionNumber": ["0000000000-26-000001"],
                    "primaryDocument": ["offering.htm"],
                    "primaryDocDescription": ["Prospectus supplement"],
                    "acceptanceDateTime": ["2026-08-13T20:00:00Z"],
                }}
            })

    filings = fetch_sec_filings(
        ["POET"],
        user_agent="ScottMarketIntelligence/2.0 owner@example.com",
        session=Session(),
        now=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert len(filings) == 1
    assert filings[0]["source_provider"] == "SEC EDGAR"
    assert filings[0]["raw"]["risk_category"] == "DILUTION_RISK"
    assert filings[0]["symbols"] == ["POET"]


def test_twse_official_flow_is_not_confused_with_ohlcv_proxy():
    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class Session:
        def get(self, url, **_kwargs):
            if url.endswith("T86"):
                return Response({
                    "stat": "OK",
                    "fields": [
                        "證券代號", "外資及陸資買賣超股數", "投信買賣超股數",
                        "自營商買賣超股數", "三大法人買賣超股數",
                    ],
                    "data": [["2330", "1,000,000", "200,000", "-50,000", "1,150,000"]],
                })
            return Response({
                "stat": "OK",
                "tables": [
                    {"fields": ["項目", "數值"], "data": [["融資總額", "1"]]},
                    {"fields": [
                        "代號", "名稱", "買進", "賣出", "現金償還", "前日餘額",
                        "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還",
                        "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記",
                    ], "data": [[
                        "2330", "台積電", "1", "2", "3", "10,000", "10,200",
                        "0", "1", "2", "3", "500", "450", "0", "0", "",
                    ]]},
                ],
            })

    twse_flow._CACHE.clear()
    result = twse_flow.get_twse_flow(
        "2330.TW",
        session=Session(),
        now=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert result["source"] == "TWSE_OFFICIAL"
    assert result["is_direct"] is True
    assert result["institutional"]["total_net_lots"] == 1150
    assert result["institutional"]["dealer_net"] == -50000
    assert result["margin"]["margin_change"] == 200
    assert result["score_adjustment"] == 0
