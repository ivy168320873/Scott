from flask import Flask, render_template, jsonify, request, Response
import requests as _req
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import time as _time
import traceback, os, json
import demo_data as _demo
import analyzer
import backtest as _bt
import signals as _sig
import patterns as _pat
import trader as _trader
import risk_manager as _rm
import scheduler as _sched
import monitor as _mon

app = Flask(__name__)

# Start background scheduler (only if SCHEDULER_ENABLE=true)
_sched.start_scheduler()

YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Yahoo Finance proxy (CORS bypass) ─────────────────────────────────────────

@app.route("/api/chart/<symbol>")
def chart_proxy(symbol):
    """Proxy Yahoo Finance chart API so the browser avoids CORS."""
    params = {k: v for k, v in request.args.items()}
    params.setdefault("range", "1y")
    params.setdefault("interval", "1d")
    params.setdefault("events", "history")
    try:
        r = _req.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params=params, headers=YAHOO_HEADERS, timeout=10,
        )
        if r.status_code == 200:
            resp = Response(r.content, status=200, mimetype="application/json")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
    except Exception:
        pass

    # Fallback: generate demo data
    hist = _demo.generate(symbol)
    name = _demo.name(symbol)
    timestamps = [int(ts.timestamp()) for ts in hist.index]
    result = {
        "chart": {
            "result": [{
                "meta": {
                    "symbol": symbol.upper(),
                    "longName": name,
                    "regularMarketPrice": float(hist["Close"].iloc[-1]),
                    "previousClose": float(hist["Close"].iloc[-2]),
                    "currency": "USD",
                    "_demo": True,
                },
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open":   hist["Open"].round(4).tolist(),
                        "high":   hist["High"].round(4).tolist(),
                        "low":    hist["Low"].round(4).tolist(),
                        "close":  hist["Close"].round(4).tolist(),
                        "volume": hist["Volume"].astype(int).tolist(),
                    }]
                }
            }],
            "error": None
        }
    }
    return jsonify(result)


# ── AI Analysis endpoint ───────────────────────────────────────────────────────

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    try:
        payload = request.json or {}
        result = analyzer.analyze(payload)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Signal detection endpoint ─────────────────────────────────────────────────

@app.route("/api/signals", methods=["POST"])
def api_signals():
    try:
        payload = request.json or {}
        result = _sig.detect(payload)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Backtest endpoint ─────────────────────────────────────────────────────────

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    try:
        payload = request.json or {}
        symbol   = payload.get("symbol", "NVDA").upper()
        strategy = payload.get("strategy", "rsi")
        params   = payload.get("params", {})
        ohlcv    = payload.get("ohlcv")   # sent from frontend

        if not ohlcv:
            # Fall back to demo data when frontend doesn't send OHLCV
            hist = _demo.generate(symbol)
            ohlcv = [
                {
                    "date":   row.Index.strftime("%Y-%m-%d"),
                    "open":   float(row.Open),
                    "high":   float(row.High),
                    "low":    float(row.Low),
                    "close":  float(row.Close),
                    "volume": int(row.Volume),
                }
                for row in hist.itertuples()
            ]

        result = _bt.run(ohlcv, strategy, params)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Candlestick pattern endpoint ──────────────────────────────────────────────

@app.route("/api/patterns", methods=["POST"])
def api_patterns():
    try:
        payload = request.json or {}
        ohlcv = payload.get("ohlcv", [])
        result = _pat.detect(ohlcv)
        return jsonify({"ok": True, "patterns": result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── NASDAQ Screener proxy ─────────────────────────────────────────────────────

_screener_cache: dict = {"data": None, "ts": 0.0}

@app.route("/api/nasdaq-screener")
def nasdaq_screener():
    """Return all NASDAQ-listed stocks with price/change/volume (15-min server cache)."""
    global _screener_cache
    TTL = 900  # 15 minutes
    now = _time.time()

    if _screener_cache["data"] and now - _screener_cache["ts"] < TTL:
        return jsonify({"ok": True, "cached": True, **_screener_cache["data"]})

    try:
        r = _req.get(
            "https://api.nasdaq.com/api/screener/stocks",
            params={"tableonly": "true", "limit": "5000", "exchange": "NASDAQ", "download": "true"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
            },
            timeout=25,
        )
        if r.status_code == 200:
            rows = (r.json().get("data") or {}).get("table", {}).get("rows") or []
            stocks = []
            for row in rows:
                try:
                    sym = (row.get("symbol") or "").strip()
                    if not sym or "/" in sym or "^" in sym or len(sym) > 6:
                        continue
                    price = float((row.get("lastsale") or "0").replace("$", "").replace(",", "") or 0)
                    pct   = float((row.get("pctchange") or "0").replace("%", "").replace(",", "") or 0)
                    vol   = int((row.get("volume") or "0").replace(",", "") or 0)
                    mc    = float((row.get("marketCap") or "0").replace(",", "") or 0)
                    if price < 1.0 or vol < 100_000:
                        continue
                    stocks.append({
                        "symbol": sym,
                        "name": (row.get("name") or "")[:60],
                        "price": round(price, 4),
                        "pctchange": round(pct, 4),
                        "volume": vol,
                        "marketCap": mc,
                        "sector": row.get("sector") or "",
                    })
                except (ValueError, TypeError):
                    continue
            result = {"count": len(stocks), "stocks": stocks}
            _screener_cache = {"data": result, "ts": now}
            return jsonify({"ok": True, "cached": False, **result})
    except Exception:
        traceback.print_exc()

    return jsonify({"ok": False, "error": "NASDAQ screener unavailable"}), 500


# ── Macro economic news (10-min cache) ────────────────────────────────────────

_macro_cache: dict = {"data": None, "ts": 0.0}
_MACRO_TTL = 600  # 10 minutes

_MACRO_QUERIES = [
    "Federal Reserve interest rate",
    "inflation CPI consumer prices",
    "US economy GDP growth",
    "jobs employment nonfarm payrolls",
    "US Treasury yield bond",
]

@app.route("/api/macro-news")
def macro_news():
    global _macro_cache
    now = _time.time()
    if _macro_cache["data"] and now - _macro_cache["ts"] < _MACRO_TTL:
        return jsonify({"ok": True, "cached": True, "news": _macro_cache["data"]})

    results, seen = [], set()
    for q in _MACRO_QUERIES:
        try:
            r = _req.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "newsCount": 4, "quotesCount": 0},
                headers=YAHOO_HEADERS, timeout=7,
            )
            if r.status_code == 200:
                for n in r.json().get("news", []):
                    t = n.get("title", "")
                    if t and t not in seen:
                        seen.add(t)
                        results.append({
                            "title":     t,
                            "link":      n.get("link", ""),
                            "publisher": n.get("publisher", ""),
                            "published": n.get("providerPublishTime", 0),
                            "category":  q.split()[0],   # first word as tag
                        })
        except Exception:
            pass

    results.sort(key=lambda x: x.get("published", 0), reverse=True)
    payload = results[:16]
    _macro_cache = {"data": payload, "ts": now}
    return jsonify({"ok": True, "cached": False, "news": payload})


# ── Batch sector news (5-min server cache per symbol) ─────────────────────────

_news_cache: dict = {}
_NEWS_TTL = 300  # 5 minutes

@app.route("/api/sector-news")
def sector_news():
    """Fetch news for multiple symbols in one request (CORS bypass + cache)."""
    global _news_cache
    raw = request.args.get("syms", "")
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()][:12]
    if not syms:
        return jsonify({"ok": False, "error": "no symbols"}), 400

    now = _time.time()
    results: dict = {}
    for sym in syms:
        if sym in _news_cache and now - _news_cache[sym]["ts"] < _NEWS_TTL:
            results[sym] = _news_cache[sym]["news"]
            continue
        try:
            r = _req.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": sym, "newsCount": 5, "quotesCount": 0},
                headers=YAHOO_HEADERS, timeout=6,
            )
            if r.status_code == 200:
                raw_news = r.json().get("news", [])
                items = [
                    {
                        "title":     n.get("title", ""),
                        "link":      n.get("link", ""),
                        "publisher": n.get("publisher", ""),
                        "published": n.get("providerPublishTime", 0),
                    }
                    for n in raw_news[:5]
                ]
                _news_cache[sym] = {"news": items, "ts": now}
                results[sym] = items
        except Exception:
            pass
    return jsonify({"ok": True, "news": results, "ts": int(now)})


# ── Single-symbol news proxy ───────────────────────────────────────────────────

@app.route("/api/news/<symbol>")
def api_news(symbol):
    try:
        r = _req.get(
            f"https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": symbol, "newsCount": 8, "quotesCount": 0},
            headers=YAHOO_HEADERS, timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            news = data.get("news", [])
            items = [{"title": n.get("title",""), "link": n.get("link",""),
                      "publisher": n.get("publisher",""),
                      "published": n.get("providerPublishTime",0)} for n in news]
            return jsonify({"ok": True, "news": items})
    except Exception:
        pass
    return jsonify({"ok": True, "news": []})


# ── Walk-forward backtest endpoint ────────────────────────────────────────────

@app.route("/api/walkforward", methods=["POST"])
def api_walkforward():
    try:
        payload   = request.json or {}
        strategy  = payload.get("strategy", "decision_core")
        ohlcv     = payload.get("ohlcv")
        params    = payload.get("params", {})
        train_pct = float(payload.get("train_pct", 0.7))
        if not ohlcv:
            return jsonify({"ok": False, "error": "ohlcv required"}), 400
        result = _bt.walk_forward(ohlcv, strategy, train_pct, params)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Parameter optimization endpoint ──────────────────────────────────────────

@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    try:
        payload  = request.json or {}
        strategy = payload.get("strategy", "decision_core_v2")
        metric   = payload.get("metric", "win_rate")
        ohlcv    = payload.get("ohlcv")
        if not ohlcv:
            return jsonify({"ok": False, "error": "ohlcv required"}), 400
        result = _bt.optimize_parameters(ohlcv, strategy, metric)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Trading endpoints ─────────────────────────────────────────────────────────

@app.route("/api/trade/status")
def api_trade_status():
    try:
        engine = _trader.get_engine()
        return jsonify({"ok": True, **engine.status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/account")
def api_trade_account():
    try:
        engine = _trader.get_engine()
        return jsonify({"ok": True, **engine.get_account()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/positions")
def api_trade_positions():
    try:
        engine = _trader.get_engine()
        return jsonify({"ok": True, "positions": engine.get_positions()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/orders")
def api_trade_orders():
    try:
        engine = _trader.get_engine()
        limit  = int(request.args.get("limit", 20))
        return jsonify({"ok": True, "orders": engine.get_recent_orders(limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/execute", methods=["POST"])
def api_trade_execute():
    try:
        payload = request.json or {}
        symbol  = payload.get("symbol", "").upper()
        entry   = float(payload.get("entry", 0))
        stop    = float(payload.get("stop",  0))
        target  = float(payload.get("target", 0))
        note    = payload.get("note", "")
        if not symbol or not entry or not stop:
            return jsonify({"ok": False, "error": "symbol / entry / stop required"}), 400

        engine = _trader.get_engine()
        rm     = _rm.get_risk_manager()
        acct   = engine.get_account()
        equity = acct.get("equity", 0)
        open_positions = len(engine.get_positions())

        ok, shares, details = rm.validate_order(
            portfolio_value    = equity,
            start_of_day_value = equity,
            open_positions     = open_positions,
            entry              = entry,
            stop               = stop,
        )
        if not ok:
            return jsonify({"ok": False, "blocked": True, "reason": details.get("reason"), "details": details})

        result = engine.submit_order(symbol, shares, entry, stop, target or entry * 1.1, note)
        return jsonify({"ok": True, **result, "risk_details": details})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/close", methods=["POST"])
def api_trade_close():
    try:
        symbol = (request.json or {}).get("symbol", "").upper()
        if not symbol:
            return jsonify({"ok": False, "error": "symbol required"}), 400
        engine = _trader.get_engine()
        result = engine.close_position(symbol)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/risk-check", methods=["POST"])
def api_risk_check():
    try:
        payload = request.json or {}
        entry   = float(payload.get("entry", 0))
        stop    = float(payload.get("stop",  0))
        capital = float(payload.get("capital", 0))
        if not entry or not stop or not capital:
            return jsonify({"ok": False, "error": "entry / stop / capital required"}), 400
        rm = _rm.get_risk_manager()
        shares, details = rm.calc_shares(capital, entry, stop)
        return jsonify({"ok": True, "shares": shares, **details})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Scheduler endpoints ────────────────────────────────────────────────────────

@app.route("/api/scheduler/status")
def api_scheduler_status():
    try:
        return jsonify({"ok": True, **_sched.get_scheduler_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/scan", methods=["POST"])
def api_scheduler_scan():
    try:
        payload = request.json or {}
        syms    = payload.get("symbols")    # optional custom list
        import threading
        t = threading.Thread(target=_sched.trigger_scan_now, args=(syms,), daemon=True)
        t.start()
        return jsonify({"ok": True, "message": "掃描已在背景啟動，請稍後查看 /api/scheduler/status"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduler/candidates")
def api_scheduler_candidates():
    try:
        return jsonify({"ok": True, "candidates": _sched._scan_candidates,
                        "last_scan": _sched._last_scan_ts,
                        "count": len(_sched._scan_candidates)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Health & Monitoring endpoints ────────────────────────────────────────────

@app.route("/api/health")
def api_health():
    """System health check — market status, scheduler, kill switch, regime."""
    try:
        health = _mon.system_health()
        sched  = _sched.get_scheduler_status()
        engine = _trader.get_engine()
        acct   = engine.get_account()
        return jsonify({
            "ok":           True,
            "market":       health["market"],
            "kill_switch":  health["kill_switch"],
            "daily_pnl":    health["daily_pnl"],
            "candidates":   health["candidates"],
            "positions":    health["positions"],
            "regime":       health["regime"],
            "scheduler":    sched["running"],
            "equity":       acct.get("equity", 0),
            "alerts_recent": _mon.get_state().get_alerts(5),
            "ts":           _time.time(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/monitor/alerts")
def api_monitor_alerts():
    try:
        n = int(request.args.get("n", 60))
        alerts = _mon.get_state().get_alerts(n)
        return jsonify({"ok": True, "alerts": alerts, "count": len(alerts)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/monitor/status")
def api_monitor_status():
    try:
        state  = _mon.get_state()
        sched  = _sched.get_scheduler_status()
        engine = _trader.get_engine()
        acct   = engine.get_account()
        return jsonify({
            "ok":             True,
            "market":         _mon.market_status(),
            "regime":         state.get("regime", {}),
            "kill_switch":    state.is_kill_switch(),
            "daily_pnl":      state.get("daily_pnl_pct", 0.0),
            "sod_equity":     state.get("sod_equity"),
            "equity":         acct.get("equity", 0),
            "scheduler":      sched,
            "candidates":     sched.get("candidates", 0),
            "positions_tracked": len(state.get_positions()),
            "alerts":         state.get_alerts(20),
            "log":            sched.get("log", [])[-30:],
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/monitor/kill-switch", methods=["POST"])
def api_kill_switch():
    """Manually engage or reset the daily kill switch."""
    try:
        action = (request.json or {}).get("action", "status")
        state  = _mon.get_state()
        if action == "engage":
            state.engage_kill_switch("手動觸發")
        elif action == "reset":
            state.reset_kill_switch()
        return jsonify({"ok": True, "kill_switch": state.is_kill_switch()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stream")
def api_stream():
    """
    Server-Sent Events: push market status + health every 30 seconds.
    Browsers subscribe once and receive live updates without polling.
    """
    def generate():
        while True:
            try:
                health = _mon.system_health()
                data   = json.dumps({
                    "market":      health["market"],
                    "kill_switch": health["kill_switch"],
                    "daily_pnl":   health["daily_pnl"],
                    "candidates":  health["candidates"],
                    "positions":   health["positions"],
                    "regime":      health["regime"],
                    "ts":          _time.time(),
                })
                yield f"data: {data}\n\n"
            except Exception:
                yield "data: {}\n\n"
            _time.sleep(30)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":  "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Analyst Debate endpoints ──────────────────────────────────────────────────

@app.route("/api/analyst/start", methods=["POST"])
def api_analyst_start():
    try:
        import analyst as _analyst
        payload  = request.json or {}
        report   = payload.get("report", "").strip()
        podcast  = payload.get("podcast", "").strip()
        model    = payload.get("model", "claude-haiku-4-5-20251001")
        if not report and not podcast:
            return jsonify({"ok": False, "error": "請提供產業報告或 Podcast 內容"}), 400
        job_id = _analyst.start_job(report, podcast, model)
        return jsonify({"ok": True, "job_id": job_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/analyst/stream/<job_id>")
def api_analyst_stream(job_id):
    import analyst as _analyst
    import queue as _queue
    q = _analyst.get_queue(job_id)
    if q is None:
        return jsonify({"ok": False, "error": "job not found"}), 404

    def generate():
        while True:
            try:
                msg = q.get(timeout=180)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("step") in ("complete", "error"):
                    break
            except _queue.Empty:
                yield 'data: {"step":"timeout"}\n\n'
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Advanced Optimizer endpoint ──────────────────────────────────────────────

@app.route("/api/optimizer/run", methods=["POST"])
def api_optimizer_run():
    try:
        import optimizer as _opt
        payload  = request.json or {}
        analysis = payload.get("analysis", "rolling_wf")
        ohlcv    = payload.get("ohlcv", [])
        strategy = payload.get("strategy", "decision_core_v3")
        trades   = payload.get("trades", [])

        if not ohlcv:
            return jsonify({"ok": False, "error": "ohlcv required"}), 400

        if analysis == "rolling_wf":
            result = _opt.rolling_walk_forward(ohlcv, strategy)
        elif analysis == "monte_carlo":
            if not trades:
                r = _bt.run(ohlcv, strategy, {})
                trades = r.get("trades", [])
            result = _opt.monte_carlo(trades)
        elif analysis == "regime":
            result = _opt.regime_analysis(ohlcv, strategy)
        elif analysis == "stability":
            result = _opt.parameter_stability(ohlcv, strategy)
        else:
            return jsonify({"ok": False, "error": f"unknown analysis: {analysis}"}), 400

        if "error" in result:
            return jsonify({"ok": False, "error": result["error"]}), 400

        return jsonify({"ok": True, "analysis": analysis, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Main page ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return render_template("index.html", has_claude=has_claude)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
