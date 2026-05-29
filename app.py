from flask import Flask, render_template, jsonify, request, Response, make_response, session, redirect, url_for
import requests as _req
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import time as _time
import traceback, os, json, hashlib
import smtplib
import email.mime.multipart
import email.mime.text
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# ── Session / Auth config ─────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RAILWAY_ENVIRONMENT") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

_ACCESS_CODE = os.environ.get("ACCESS_CODE", "")   # set in Railway → empty = no auth required
_LOGIN_LOG: list[dict] = []   # in-memory log (last 200 entries)
_MAX_LOG = 200
_SERVER_START = datetime.now(timezone.utc)

# ── Login rate limiting ───────────────────────────────────────────────────────
_login_attempts: dict = {}   # ip -> {count, locked_until}
_MAX_ATTEMPTS  = 5
_LOCKOUT_SECS  = 15 * 60     # 15 minutes

def _is_locked(ip: str) -> tuple[bool, int]:
    rec = _login_attempts.get(ip, {})
    remaining = int(rec.get("locked_until", 0) - _time.time())
    if remaining > 0:
        return True, remaining
    if "locked_until" in rec:          # lock expired → clean up
        _login_attempts.pop(ip, None)
    return False, 0

def _record_fail(ip: str):
    rec = _login_attempts.get(ip, {"count": 0})
    rec["count"] = rec.get("count", 0) + 1
    if rec["count"] >= _MAX_ATTEMPTS:
        rec["locked_until"] = _time.time() + _LOCKOUT_SECS
    _login_attempts[ip] = rec

def _reset_attempts(ip: str):
    _login_attempts.pop(ip, None)

# ── User data persistence (cross-device sync) ─────────────────────────────────
import threading as _threading

_USER_DATA_FILE  = os.environ.get("USER_DATA_FILE", "./user_data.json")
_user_data_lock  = _threading.Lock()
_user_data_mem: dict = {}

def _load_user_data() -> dict:
    global _user_data_mem
    if _user_data_mem:
        return dict(_user_data_mem)
    try:
        with open(_USER_DATA_FILE, "r", encoding="utf-8") as f:
            _user_data_mem = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _user_data_mem = {}
    return dict(_user_data_mem)

def _save_user_data(patch: dict):
    global _user_data_mem
    with _user_data_lock:
        _user_data_mem.update(patch)
        try:
            with open(_USER_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(_user_data_mem, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # best-effort write

def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

def _parse_ua(ua: str) -> str:
    """Return human-readable device name from User-Agent string."""
    ua = ua or ""
    # OS
    if "iPhone" in ua:     os_name = "iPhone"
    elif "iPad" in ua:     os_name = "iPad"
    elif "Android" in ua:  os_name = "Android"
    elif "Windows" in ua:  os_name = "Windows"
    elif "Macintosh" in ua or "Mac OS" in ua: os_name = "Mac"
    elif "Linux" in ua:    os_name = "Linux"
    else:                  os_name = "未知裝置"
    # Browser
    if "Edg/" in ua or "Edge/" in ua:   browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua: browser = "Opera"
    elif "Chrome/" in ua:               browser = "Chrome"
    elif "Firefox/" in ua:              browser = "Firefox"
    elif "Safari/" in ua:               browser = "Safari"
    else:                               browser = "瀏覽器"
    return f"{os_name} / {browser}"

def _get_ip() -> str:
    """Get real IP, respecting Railway's reverse proxy headers."""
    return (request.headers.get("X-Forwarded-For") or
            request.headers.get("X-Real-IP") or
            request.remote_addr or "unknown").split(",")[0].strip()

def _append_log(ip: str, device: str, success: bool, note: str = ""):
    global _LOGIN_LOG
    entry = {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ip": ip,
        "device": device,
        "success": success,
        "note": note,
    }
    _LOGIN_LOG.insert(0, entry)
    _LOGIN_LOG = _LOGIN_LOG[:_MAX_LOG]
    status = "✅ 成功" if success else "❌ 失敗"
    print(f"[LOGIN] {status} | IP:{ip} | {device} | {note}", flush=True)

@app.before_request
def _require_auth():
    """Block every request unless the session is authenticated or ACCESS_CODE is unset."""
    if not _ACCESS_CODE:
        return  # auth disabled
    if request.endpoint in ("login", "logout", "admin_logins", "admin_dashboard", "static"):
        return
    if session.get("auth") == _hash(_ACCESS_CODE):
        return
    # API calls return JSON 401 instead of redirect
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Unauthorized"), 401
    return redirect(url_for("login", next=request.path))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    locked_secs = 0
    if request.method == "POST":
        ip = _get_ip()
        device = _parse_ua(request.headers.get("User-Agent", ""))
        blocked, secs = _is_locked(ip)
        if blocked:
            locked_secs = secs
            error = f"嘗試次數過多，請等待 {secs // 60} 分 {secs % 60} 秒後再試"
            _append_log(ip, device, False, f"已鎖定 {secs}s")
        else:
            code = (request.form.get("code") or "").strip()
            if _ACCESS_CODE and _hash(code) == _hash(_ACCESS_CODE):
                _reset_attempts(ip)
                session.permanent = True
                session["auth"] = _hash(_ACCESS_CODE)
                _append_log(ip, device, True, "登入成功")
                return redirect(request.args.get("next") or "/")
            else:
                _record_fail(ip)
                rec = _login_attempts.get(ip, {})
                remain = _MAX_ATTEMPTS - rec.get("count", 0)
                _append_log(ip, device, False, "認識碼錯誤")
                if remain > 0:
                    error = f"認識碼錯誤，還有 {remain} 次機會"
                else:
                    error = f"已鎖定 {_LOCKOUT_SECS // 60} 分鐘，請稍後再試"
    return render_template("login.html", error=error, locked_secs=locked_secs)

@app.route("/logout")
def logout():
    ip = _get_ip()
    device = _parse_ua(request.headers.get("User-Agent", ""))
    _append_log(ip, device, True, "登出")
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin")
def admin_dashboard():
    """Master admin control panel."""
    # uptime
    delta = datetime.now(timezone.utc) - _SERVER_START
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s   = divmod(rem, 60)
    uptime_str = f"{h}h {m}m {s}s"

    # login stats
    total_logins  = sum(1 for l in _LOGIN_LOG if l["success"] and l["note"] != "登出")
    failed_logins = sum(1 for l in _LOGIN_LOG if not l["success"])
    last_login    = next((l for l in _LOGIN_LOG if l["success"] and l["note"] == "登入成功"), None)

    # env vars presence (never expose values)
    env_status = {
        "ACCESS_CODE":    bool(os.environ.get("ACCESS_CODE")),
        "SECRET_KEY":     bool(os.environ.get("SECRET_KEY")),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "ALPHA_VANTAGE_KEY": bool(os.environ.get("ALPHA_VANTAGE_KEY")),
        "LINE_NOTIFY_TOKEN": bool(os.environ.get("LINE_NOTIFY_TOKEN")),
        "SMTP_HOST":      bool(os.environ.get("SMTP_HOST")),
        "SCHEDULER_ENABLE": os.environ.get("SCHEDULER_ENABLE", "false"),
        "RAILWAY_ENVIRONMENT": os.environ.get("RAILWAY_ENVIRONMENT", "—"),
    }

    # scheduler & health
    try:
        sched = _sched.get_scheduler_status()
    except Exception:
        sched = {}
    try:
        health = _mon.system_health()
    except Exception:
        health = {}

    # daily report cache
    cache_age = None
    if _daily_report_cache.get("ts"):
        cache_age = int((_time.time() - _daily_report_cache["ts"]) / 60)

    return render_template("admin.html",
        uptime=uptime_str,
        server_start=_SERVER_START.strftime("%Y-%m-%d %H:%M UTC"),
        total_logins=total_logins,
        failed_logins=failed_logins,
        last_login=last_login,
        logs=_LOGIN_LOG[:30],
        env_status=env_status,
        sched=sched,
        health=health,
        cache_age=cache_age,
        alert_settings=_alert_schedule_settings,
        log_count=len(_LOGIN_LOG),
    )

@app.route("/admin/logins")
def admin_logins():
    """Login activity log — only accessible after authentication."""
    return render_template("logins.html", logs=_LOGIN_LOG)

@app.route("/api/admin/logins")
def api_admin_logins():
    return jsonify(ok=True, logs=_LOGIN_LOG)

@app.route("/api/admin/clear-log", methods=["POST"])
def api_admin_clear_log():
    global _LOGIN_LOG
    _LOGIN_LOG = []
    return jsonify(ok=True, message="登入紀錄已清空")

@app.route("/api/admin/clear-cache", methods=["POST"])
def api_admin_clear_cache():
    _daily_report_cache["report"] = None
    _daily_report_cache["ts"] = 0
    return jsonify(ok=True, message="每日報告快取已清空")

@app.route("/api/admin/test-connections")
def api_admin_test_connections():
    """Test external API connectivity — shows in admin dashboard."""
    results = {}
    # Claude
    try:
        import anthropic as _ant
        c = _ant.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        m = c.messages.create(model="claude-haiku-4-5-20251001", max_tokens=8,
                              messages=[{"role": "user", "content": "hi"}])
        results["claude"] = {"ok": True, "detail": m.model}
    except Exception as e:
        results["claude"] = {"ok": False, "detail": str(e)[:120]}
    # Alpha Vantage
    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        try:
            r = _req.get("https://www.alphavantage.co/query",
                         params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": av_key},
                         timeout=8)
            d = r.json()
            ok = bool(d.get("Global Quote"))
            results["alpha_vantage"] = {"ok": ok, "detail": "連線正常" if ok else "API key 無效或達到限額"}
        except Exception as e:
            results["alpha_vantage"] = {"ok": False, "detail": str(e)[:120]}
    else:
        results["alpha_vantage"] = {"ok": False, "detail": "未設定 ALPHA_VANTAGE_KEY"}
    # Yahoo Finance
    try:
        r = _req.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
                     params={"range": "1d", "interval": "1d"}, headers=YAHOO_HEADERS, timeout=8)
        results["yahoo"] = {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        results["yahoo"] = {"ok": False, "detail": str(e)[:120]}
    return jsonify(ok=True, results=results)

@app.route("/api/user/data", methods=["GET", "POST"])
def api_user_data():
    """Cross-device localStorage sync endpoint."""
    if request.method == "GET":
        return jsonify(ok=True, data=_load_user_data())
    patch = request.get_json(force=True, silent=True) or {}
    if patch:
        _save_user_data(patch)
    return jsonify(ok=True)

@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, msg="找不到此頁面"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, msg="伺服器發生錯誤"), 500

# Start background scheduler (only if SCHEDULER_ENABLE=true)
_sched.start_scheduler()

# ── Module-level caches and settings ──────────────────────────────────────────
_daily_report_cache: dict = {"report": None, "ts": 0}
_alert_schedule_settings: dict = {"enabled": False, "time": "16:00", "timezone": "America/New_York"}

YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Alpha Vantage OHLCV helper ────────────────────────────────────────────────

def _fetch_ohlcv_alpha_vantage(symbol: str, av_key: str) -> dict | None:
    """Fetch daily OHLCV from Alpha Vantage and return Yahoo-format dict, or None on failure."""
    try:
        r = _req.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "full",
                "apikey": av_key,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        ts_data = data.get("Time Series (Daily)")
        if not ts_data:
            return None
        # Sort dates ascending
        dates_sorted = sorted(ts_data.keys())
        timestamps = []
        opens, highs, lows, closes, volumes = [], [], [], [], []
        last_close = None
        for date_str in dates_sorted:
            day = ts_data[date_str]
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                timestamps.append(int(dt.replace(tzinfo=timezone.utc).timestamp()))
                opens.append(round(float(day.get("1. open", 0) or 0), 4))
                highs.append(round(float(day.get("2. high", 0) or 0), 4))
                lows.append(round(float(day.get("3. low", 0) or 0), 4))
                closes.append(round(float(day.get("4. close", 0) or 0), 4))
                volumes.append(int(float(day.get("6. volume", 0) or 0)))
                last_close = closes[-1]
            except (ValueError, TypeError):
                continue
        if not timestamps:
            return None
        prev_close = closes[-2] if len(closes) >= 2 else last_close
        return {
            "chart": {
                "result": [{
                    "meta": {
                        "symbol": symbol.upper(),
                        "longName": symbol.upper(),
                        "regularMarketPrice": last_close,
                        "previousClose": prev_close,
                        "currency": "USD",
                        "_source": "alpha_vantage",
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [{
                            "open":   opens,
                            "high":   highs,
                            "low":    lows,
                            "close":  closes,
                            "volume": volumes,
                        }]
                    }
                }],
                "error": None
            }
        }
    except Exception:
        traceback.print_exc()
        return None


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

    # Intermediate fallback: Alpha Vantage
    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        av_result = _fetch_ohlcv_alpha_vantage(symbol, av_key)
        if av_result:
            resp = Response(json.dumps(av_result), status=200, mimetype="application/json")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

    # Final fallback: generate demo data
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


@app.route("/api/analyst/config")
def api_analyst_config():
    """Return config status: API key presence, model availability."""
    return jsonify({
        "ok": True,
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
    })


@app.route("/api/analyst/fetch", methods=["POST"])
def api_analyst_fetch():
    """Auto-fetch industry reports and Gooaye Podcast content."""
    try:
        import fetcher as _fetcher
        payload = request.json or {}
        topic   = payload.get("topic", "半導體 AI 科技").strip() or "半導體 AI 科技"
        result  = _fetcher.fetch_all(topic)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
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


# ── Batch news headline translation ──────────────────────────────────────────

@app.route("/api/translate-news", methods=["POST"])
def api_translate_news():
    """Batch-translate English news headlines to Traditional Chinese using Claude Haiku."""
    try:
        import re as _re
        titles = (request.json or {}).get("titles", [])[:20]
        if not titles:
            return jsonify({"ok": False, "error": "no titles"})
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"})
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": (
                "請將以下英文財經新聞標題翻譯成繁體中文。\n"
                "只輸出翻譯結果，保持原本編號，每行一條，不要加任何說明：\n\n"
                + numbered
            )}],
        )
        text = resp.content[0].text if resp.content else ""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        translations = [_re.sub(r"^\d+[.、．]\s*", "", l) for l in lines]
        while len(translations) < len(titles):
            translations.append(titles[len(translations)])
        return jsonify({"ok": True, "translations": translations[:len(titles)]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


def _fetch_ohlcv_server(symbol: str) -> list | None:
    """Fetch 2y daily OHLCV from Yahoo Finance (server-side). Returns list of dicts or None.
    Falls back to Alpha Vantage if Yahoo fails and ALPHA_VANTAGE_KEY is set."""
    try:
        r = _req.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5y", "interval": "1d", "events": "history"},
            headers=YAHOO_HEADERS, timeout=12,
        )
        if r.status_code != 200:
            raise ValueError(f"Yahoo HTTP {r.status_code}")
        j = r.json()
        res = j["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = res["timestamp"]
        rows = []
        for i, t in enumerate(ts):
            try:
                rows.append({
                    "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    "open":   float(q["open"][i] or 0),
                    "high":   float(q["high"][i] or 0),
                    "low":    float(q["low"][i] or 0),
                    "close":  float(q["close"][i] or 0),
                    "volume": int(q["volume"][i] or 0),
                })
            except (TypeError, ValueError):
                continue
        return [r for r in rows if r["close"] > 0] or None
    except Exception:
        pass

    # Intermediate fallback: Alpha Vantage
    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        try:
            av_result = _fetch_ohlcv_alpha_vantage(symbol, av_key)
            if av_result:
                res = av_result["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                ts_list = res["timestamp"]
                rows = []
                for i, t in enumerate(ts_list):
                    try:
                        rows.append({
                            "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                            "open":   float(q["open"][i] or 0),
                            "high":   float(q["high"][i] or 0),
                            "low":    float(q["low"][i] or 0),
                            "close":  float(q["close"][i] or 0),
                            "volume": int(q["volume"][i] or 0),
                        })
                    except (TypeError, ValueError):
                        continue
                return [row for row in rows if row["close"] > 0] or None
        except Exception:
            pass

    return None


@app.route("/api/batch-backtest-4d", methods=["POST"])
def api_batch_backtest_4d():
    """
    Run decision_core_v2 backtest on a list of symbols.
    Accepts: { symbols: ["NVDA", "TSLA", ...] }   (max 12)
    Returns per-symbol stats + aggregate summary.
    """
    try:
        payload  = request.json or {}
        symbols  = [s.upper() for s in (payload.get("symbols") or [])[:12]]
        if not symbols:
            return jsonify({"ok": False, "error": "no symbols provided"}), 400

        results = []

        def _run_one(sym):
            ohlcv = _fetch_ohlcv_server(sym)
            if not ohlcv:
                hist = _demo.generate(sym, n=1260)  # 5 years of trading days
                ohlcv = [
                    {"date": row.Index.strftime("%Y-%m-%d"),
                     "open": float(row.Open), "high": float(row.High),
                     "low":  float(row.Low),  "close": float(row.Close),
                     "volume": int(row.Volume)}
                    for row in hist.itertuples()
                ]
            r = _bt.run(ohlcv, "decision_core", {})
            return {
                "symbol":        sym,
                "win_rate":      round(r.get("win_rate", 0), 1),
                "profit_factor": round(r.get("profit_factor", 0), 2),
                "num_trades":    r.get("num_trades", 0),
                "annual_return": round(r.get("annual_return", 0), 2),
                "max_drawdown":  round(r.get("max_drawdown", 0), 2),
                "sharpe":        round(r.get("sharpe", 0), 2),
                "expectancy":    round(r.get("expectancy", 0), 2),
                "bh_return":     round(r.get("bh_return", 0), 2),
                "is_demo":       len(ohlcv) > 0 and ohlcv[0].get("date","").startswith("20"),
            }

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_run_one, s): s for s in symbols}
            for f in as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as e:
                    results.append({"symbol": futures[f], "error": str(e)})

        results.sort(key=lambda x: x.get("win_rate", 0), reverse=True)

        valid = [r for r in results if "win_rate" in r and r["num_trades"] >= 2]
        agg = {}
        if valid:
            agg = {
                "avg_win_rate":      round(sum(r["win_rate"]      for r in valid) / len(valid), 1),
                "avg_profit_factor": round(sum(r["profit_factor"] for r in valid) / len(valid), 2),
                "avg_annual_return": round(sum(r["annual_return"] for r in valid) / len(valid), 2),
                "avg_max_drawdown":  round(sum(r["max_drawdown"]  for r in valid) / len(valid), 2),
                "symbols_tested":    len(valid),
                "pass_60pct":        sum(1 for r in valid if r["win_rate"] >= 60),
            }

        return jsonify({"ok": True, "results": results, "aggregate": agg})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trump-picks")
def api_trump_picks():
    """Fetch Trump's Truth Social posts and extract stock signals."""
    try:
        import trump as _trump
        result = _trump.fetch_and_analyze()
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/catalyst/<symbol>")
def api_catalyst(symbol):
    """
    Fast catalyst / theme research for a single stock.
    Returns structured JSON with theme, catalysts, bull/bear summary.
    """
    try:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        symbol = symbol.upper()[:10]
        prompt = (
            f"請用繁體中文分析 {symbol} 這支股票的題材與催化劑，輸出 JSON（只輸出純 JSON，不加說明）：\n"
            "{\n"
            '  "company":    "公司中文名稱（10字內）",\n'
            '  "theme":      "核心題材（AI/生技/新能源/國防/半導體…，30字內點出行業機遇）",\n'
            '  "catalysts":  ["催化劑1（20字內）", "催化劑2", "催化劑3"],\n'
            '  "bull_case":  "多頭理由：為什麼可能大漲或翻倍（50字內，具體說明邏輯）",\n'
            '  "bear_case":  "空頭風險：最大潛在利空（30字內）",\n'
            '  "analyst_tp": "分析師目標價或評級（如有，否則填 null）",\n'
            '  "horizon":    "預期題材發酵時間：短期/中期/長期"\n'
            "}\n"
            f"請搜尋最新資訊（近1個月）再回答。只輸出 JSON，不要 markdown。"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )
        texts = [b.text for b in resp.content if hasattr(b, "text") and b.text]
        raw = "\n".join(texts)
        import re as _re
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            return jsonify({"ok": True, "symbol": symbol, "data": data, "raw": raw})
        return jsonify({"ok": True, "symbol": symbol, "data": None, "raw": raw})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deep-news/<symbol>")
def api_deep_news(symbol):
    """Deep news search for a symbol using Claude web search (requires API key)."""
    try:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        symbol = symbol.upper()[:10]
        prompt = (
            f"請搜尋 {symbol} 這支股票的最新資訊，整理成簡潔的投資參考摘要：\n"
            "1. 最新重大新聞（近2週）\n"
            "2. 最新財報重點或法說會內容\n"
            "3. 分析師評級與目標價\n"
            "4. 股價走勢與技術面關鍵位置\n"
            "5. 主要風險與催化劑\n\n"
            "請用繁體中文，條列清晰，每點附上資料來源與日期。"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1800,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            messages=[{"role": "user", "content": prompt}],
        )
        texts = [b.text for b in resp.content if hasattr(b, "text") and b.text]
        content = "\n\n".join(texts)
        return jsonify({"ok": True, "symbol": symbol, "content": content})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── AI Chat endpoint ──────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Conversational Claude endpoint for stock Q&A."""
    try:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        from anthropic import Anthropic
        payload = request.json or {}
        message = (payload.get("message") or "").strip()[:1000]
        context = (payload.get("context") or "").strip()[:200]
        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400
        client = Anthropic(api_key=key)
        system = (
            "你是一位專業的股票分析助理，擅長美股與台股技術分析、基本面分析和量化策略。"
            "請用繁體中文回答，語氣專業但易懂，回答要具體有洞察力，不超過400字。"
        )
        ctx_prefix = (f"目前查看的股票：{context}\n") if context else ""
        user_msg = ctx_prefix + message
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        reply = resp.content[0].text if resp.content else ""
        return jsonify({"ok": True, "reply": reply})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Daily report endpoint ──────────────────────────────────────────────────────

@app.route("/api/daily-report")
def api_daily_report():
    """Generate a daily market summary. Cached 2 hours."""
    try:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        now = _time.time()
        if _daily_report_cache["report"] and now - _daily_report_cache["ts"] < 7200:
            return jsonify({"ok": True, "report": _daily_report_cache["report"],
                            "generated_at": _daily_report_cache["generated_at"], "cached": True})
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            messages=[{"role": "user", "content": (
                "請搜尋今日美股市場重點，用繁體中文整理以下內容：\n"
                "1. 📊 今日大盤表現（S&P500、NASDAQ、道瓊）\n"
                "2. 🔥 今日最強板塊與代表個股（漲幅前3）\n"
                "3. 📉 今日最弱板塊（跌幅前3）\n"
                "4. 📰 影響市場的重大新聞（Fed、財報、地緣政治）\n"
                "5. 🔮 明日關注重點（重要財報、經濟數據）\n"
                "請條列清晰，每點簡潔20-40字。"
            )}],
        )
        texts = [b.text for b in resp.content if hasattr(b, "text") and b.text]
        report = "\n\n".join(texts)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        _daily_report_cache.update({"report": report, "ts": now, "generated_at": generated_at})
        return jsonify({"ok": True, "report": report, "generated_at": generated_at, "cached": False})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Alerts endpoint ────────────────────────────────────────────────────────────

@app.route("/api/alerts/send", methods=["POST"])
def api_alerts_send():
    """Send alerts via email and/or LINE Notify."""
    try:
        payload     = request.json or {}
        signals     = payload.get("signals", [])
        alert_type  = payload.get("type", "both")
        email_to    = payload.get("email") or os.environ.get("ALERT_EMAIL_TO", "")
        line_token  = payload.get("lineToken") or os.environ.get("LINE_NOTIFY_TOKEN", "")
        sent = []
        errors = []

        msg_lines = ["📈 Scott 股票訊號提醒"]
        for s in signals[:10]:
            sym  = s.get("symbol", "")
            note = s.get("note", "")
            msg_lines.append(f"• {sym}: {note}" if note else f"• {sym}")
        plain_text = "\n".join(msg_lines)

        # ── LINE Notify ───────────────────────────────────────────────────────
        if alert_type in ("line", "both") and line_token:
            try:
                lr = _req.post(
                    "https://notify-api.line.me/api/notify",
                    headers={"Authorization": f"Bearer {line_token}"},
                    data={"message": "\n" + plain_text},
                    timeout=10,
                )
                if lr.status_code == 200:
                    sent.append("line")
                else:
                    errors.append(f"LINE {lr.status_code}: {lr.text[:100]}")
            except Exception as ex:
                errors.append(f"LINE error: {str(ex)[:80]}")

        # ── Email ─────────────────────────────────────────────────────────────
        if alert_type in ("email", "both") and email_to:
            try:
                smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
                smtp_port = int(os.environ.get("SMTP_PORT", "587"))
                smtp_user = os.environ.get("SMTP_USER", "")
                smtp_pass = os.environ.get("SMTP_PASS", "")
                from_addr = os.environ.get("ALERT_EMAIL_FROM", smtp_user)

                subject = f"📈 Scott 訊號 {datetime.now().strftime('%m/%d')}"
                rows_html = "".join(
                    f"<tr><td style='padding:6px 10px;border-bottom:1px solid #333;font-weight:700;color:#58a6ff'>{s.get('symbol','')}</td>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #333;color:#e6edf3'>{s.get('note','')}</td></tr>"
                    for s in signals[:10]
                )
                html_body = f"""<div style="background:#0d1117;color:#e6edf3;font-family:monospace;padding:20px;border-radius:10px">
<h2 style="color:#58a6ff">📈 Scott 股票訊號提醒</h2>
<table style="border-collapse:collapse;width:100%"><thead>
<tr><th style="text-align:left;padding:6px 10px;color:#8b949e">股票</th><th style="text-align:left;padding:6px 10px;color:#8b949e">訊號</th></tr>
</thead><tbody>{rows_html}</tbody></table>
<p style="color:#8b949e;font-size:12px;margin-top:16px">⚠️ 此為量化模型訊號，不構成投資建議。</p></div>"""

                msg = email.mime.multipart.MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = from_addr
                msg["To"]      = email_to
                msg.attach(email.mime.text.MIMEText(plain_text, "plain", "utf-8"))
                msg.attach(email.mime.text.MIMEText(html_body,  "html",  "utf-8"))

                if smtp_port == 465:
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as srv:
                        if smtp_user and smtp_pass:
                            srv.login(smtp_user, smtp_pass)
                        srv.sendmail(from_addr, [email_to], msg.as_bytes())
                else:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
                        srv.ehlo(); srv.starttls(); srv.ehlo()
                        if smtp_user and smtp_pass:
                            srv.login(smtp_user, smtp_pass)
                        srv.sendmail(from_addr, [email_to], msg.as_bytes())
                sent.append("email")
            except Exception as ex:
                errors.append(f"Email error: {str(ex)[:120]}")

        return jsonify({"ok": len(sent) > 0 or not errors, "sent": sent, "errors": errors})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/alerts/schedule-status", methods=["GET", "POST"])
def api_alerts_schedule_status():
    if request.method == "POST":
        data = request.json or {}
        _alert_schedule_settings.update({
            k: data[k] for k in ("enabled", "time", "timezone") if k in data
        })
    return jsonify({"ok": True, "settings": _alert_schedule_settings})


# ── Main page ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    resp = make_response(render_template("index.html", has_claude=has_claude))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
