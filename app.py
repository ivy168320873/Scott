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

# ── User data persistence (SQLite, cross-device sync) ─────────────────────────
import threading as _threading
import sqlite3 as _sqlite3

_USER_DATA_DB   = os.environ.get("USER_DATA_DB",   "./user_data.db")
_USER_DATA_FILE = os.environ.get("USER_DATA_FILE", "./user_data.json")  # legacy, migrate only
_user_data_lock = _threading.Lock()
_user_data_mem: dict = {}   # in-memory read cache


def _init_user_db():
    """Create SQLite table; migrate from legacy JSON on first run."""
    global _user_data_mem
    con = _sqlite3.connect(_USER_DATA_DB, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, ts TEXT)")
    con.commit()
    if con.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 0:
        try:
            with open(_USER_DATA_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            now_iso = datetime.now(timezone.utc).isoformat()
            for k, v in old.items():
                con.execute("INSERT OR IGNORE INTO kv(key,value,ts) VALUES(?,?,?)",
                            (k, json.dumps(v, ensure_ascii=False), now_iso))
            con.commit()
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    _user_data_mem = {r[0]: json.loads(r[1])
                      for r in con.execute("SELECT key,value FROM kv")}
    con.close()


try:
    _init_user_db()
except Exception:
    traceback.print_exc()


def _load_user_data() -> dict:
    return dict(_user_data_mem)


def _save_user_data(patch: dict):
    global _user_data_mem
    now_iso = datetime.now(timezone.utc).isoformat()
    with _user_data_lock:
        _user_data_mem.update(patch)
        try:
            con = _sqlite3.connect(_USER_DATA_DB, check_same_thread=False)
            for k, v in patch.items():
                con.execute("INSERT OR REPLACE INTO kv(key,value,ts) VALUES(?,?,?)",
                            (k, json.dumps(v, ensure_ascii=False), now_iso))
            con.commit()
            con.close()
        except Exception:
            traceback.print_exc()

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
        "FINNHUB_KEY":    bool(os.environ.get("FINNHUB_KEY")),
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
    # Finnhub
    fh_key = os.environ.get("FINNHUB_KEY", "")
    if fh_key:
        try:
            r = _req.get("https://finnhub.io/api/v1/quote",
                         params={"symbol": "AAPL", "token": fh_key}, timeout=8)
            ok = r.status_code == 200 and bool(r.json().get("c"))
            results["finnhub"] = {"ok": ok, "detail": "連線正常" if ok else "API key 無效或達到限額"}
        except Exception as e:
            results["finnhub"] = {"ok": False, "detail": str(e)[:120]}
    else:
        results["finnhub"] = {"ok": False, "detail": "未設定 FINNHUB_KEY"}
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


# ── TWSE / TPEX fallback for Taiwan stocks ────────────────────────────────────

_TW_OHLCV_CACHE: dict = {}   # symbol → {"ts": float, "data": dict}
_TW_OHLCV_TTL   = 30 * 60   # 30 minutes


def _fetch_ohlcv_twse(symbol: str) -> dict | None:
    """
    Fetch daily OHLCV from TWSE (上市) or TPEX (上櫃) for .TW / .TWO stocks.
    Uses official open-data APIs; fetches 14 months in parallel.
    Returns Yahoo-format dict or None on failure. Results cached 30 min.
    """
    sym_key = symbol.upper()
    cached = _TW_OHLCV_CACHE.get(sym_key)
    if cached and _time.time() - cached["ts"] < _TW_OHLCV_TTL:
        return cached["data"]

    stock_no = symbol.upper().replace(".TWO", "").replace(".TW", "").strip()
    if not stock_no.isdigit():
        return None

    from datetime import date as _date
    today = _date.today()

    # Generate date strings for the last 14 months (YYYYMM01)
    date_strs: list[str] = []
    y, m = today.year, today.month
    for _ in range(14):
        date_strs.append(f"{y}{m:02d}01")
        m -= 1
        if m == 0:
            m, y = 12, y - 1

    tw_headers = {"User-Agent": "Mozilla/5.0 (compatible; Scott/1.0)"}

    for is_otc in (False, True):
        long_name: list[str] = [""]   # mutable container so inner fn can update

        def _fetch_month(date_str: str) -> list:
            try:
                if not is_otc:
                    r = _req.get(
                        "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                        params={"response": "json", "date": date_str, "stockNo": stock_no},
                        headers=tw_headers, timeout=10,
                    )
                else:
                    yy, mm = date_str[:4], date_str[4:6]
                    r = _req.get(
                        "https://www.tpex.org.tw/web/stock/aftertrading/"
                        "daily_trading_info/st43_download.php",
                        params={"l": "zh-tw", "d": f"{yy}/{mm}/01",
                                "stkno": stock_no, "response": "json"},
                        headers=tw_headers, timeout=10,
                    )
                if r.status_code == 200:
                    j = r.json()
                    # Extract company name from title: "114年05月 6207 雷科 各日成交資訊"
                    title = j.get("title", "")
                    if title and not long_name[0]:
                        parts = title.split()
                        # Find the index of stock_no in parts, name is next token
                        for idx, p in enumerate(parts):
                            if stock_no in p and idx + 1 < len(parts):
                                candidate = parts[idx + 1]
                                if candidate not in ("各日成交資訊", "每日收盤行情"):
                                    long_name[0] = candidate
                                break
                    return j.get("data", [])
            except Exception:
                pass
            return []

        with ThreadPoolExecutor(max_workers=6) as ex:
            month_results = list(ex.map(_fetch_month, date_strs))
        all_rows = [row for batch in month_results for row in batch]

        if not all_rows:
            continue   # try TPEX next

        # ── Parse rows ──────────────────────────────────────────────────────
        # TSE/TPEX row: [ROC-date, volume, amount, open, high, low, close, chg, txn]
        timestamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
        for row in all_rows:
            try:
                if len(row) < 7:
                    continue
                parts = str(row[0]).strip().split("/")
                if len(parts) != 3:
                    continue
                year = int(parts[0]) + 1911   # ROC → Gregorian
                month_r, day_r = int(parts[1]), int(parts[2])
                dt = datetime(year, month_r, day_r, tzinfo=timezone.utc)

                def _p(x: str) -> float:
                    return float(str(x).replace(",", "").strip())

                o, h, l, c = _p(row[3]), _p(row[4]), _p(row[5]), _p(row[6])
                v = int(_p(row[1]))
                if c <= 0:
                    continue
                timestamps.append(int(dt.timestamp()))
                opens.append(o); highs.append(h); lows.append(l)
                closes.append(c); volumes.append(v)
            except Exception:
                continue

        if not timestamps:
            continue

        # Sort ascending by timestamp
        combined = sorted(zip(timestamps, opens, highs, lows, closes, volumes))
        ts_, o_, h_, l_, c_, v_ = zip(*combined)

        last_close = c_[-1]
        prev_close = c_[-2] if len(c_) >= 2 else last_close
        _result = {
            "chart": {
                "result": [{
                    "meta": {
                        "symbol": symbol.upper(),
                        "longName": long_name[0] or symbol.upper(),
                        "regularMarketPrice": last_close,
                        "previousClose":      prev_close,
                        "currency": "TWD",
                        "_source": "twse" if not is_otc else "tpex",
                    },
                    "timestamp": list(ts_),
                    "indicators": {
                        "quote": [{
                            "open":   list(o_),
                            "high":   list(h_),
                            "low":    list(l_),
                            "close":  list(c_),
                            "volume": list(v_),
                        }]
                    }
                }],
                "error": None,
            }
        }
        _TW_OHLCV_CACHE[symbol.upper()] = {"ts": _time.time(), "data": _result}
        return _result
    return None   # both TSE and TPEX failed


# ── Taiwan Stock 100-Point Momentum Score ─────────────────────────────────────

_TW_INST_CACHE:  dict = {}   # date_str → {stock_no: {foreign, trust}}
_TW_MARG_CACHE:  dict = {}   # date_str → {stock_no: {balance, short, buy, sell}}
_TW_ATTN_CACHE:  dict = {"ts": 0.0, "stocks": set()}
_TW_FUND_CACHE:  dict = {}   # stock_no → {per, pbr, div_yield, rev_yoy, ts}
_TW_SCORE_CACHE: dict = {}   # symbol → {"ts": float, "data": dict}
_TW_CACHE_LOCK   = _threading.Lock()
_TW_CACHE_TTL    = 6 * 3600   # 6 h
_TW_SCORE_TTL    = 30 * 60    # 30 min


def _tw_recent_dates(n: int = 7) -> list:
    """Return last n weekday YYYYMMDD strings (newest first)."""
    from datetime import date as _date, timedelta as _td
    result, d = [], _date.today()
    while len(result) < n:
        if d.weekday() < 5:
            result.append(d.strftime("%Y%m%d"))
        d -= _td(days=1)
    return result


def _fetch_tw_inst_bulk(date_str: str) -> dict:
    """Fetch TWSE T86 (all stocks institutional net buy) for one date."""
    with _TW_CACHE_LOCK:
        if date_str in _TW_INST_CACHE:
            return _TW_INST_CACHE[date_str]
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; Scott/1.0)"}
    out: dict = {}
    try:
        r = _req.get(
            "https://www.twse.com.tw/rwd/zh/fund/T86",
            params={"date": date_str, "selectType": "ALLBUT0999", "response": "json"},
            headers=hdrs, timeout=12,
        )
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if len(row) < 8:
                    continue
                sn = str(row[0]).strip()
                def _int(s):
                    try: return int(str(s).replace(",", "").replace("+", "") or 0)
                    except ValueError: return 0
                out[sn] = {"foreign": _int(row[4]), "trust": _int(row[7])}
    except Exception:
        pass
    with _TW_CACHE_LOCK:
        _TW_INST_CACHE[date_str] = out
    return out


def _fetch_tw_marg_bulk(date_str: str) -> dict:
    """Fetch TWSE margin trading data for one date."""
    with _TW_CACHE_LOCK:
        if date_str in _TW_MARG_CACHE:
            return _TW_MARG_CACHE[date_str]
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; Scott/1.0)"}
    out: dict = {}
    try:
        r = _req.get(
            "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
            params={"date": date_str, "selectType": "STOCK", "response": "json"},
            headers=hdrs, timeout=12,
        )
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if len(row) < 12:
                    continue
                sn = str(row[0]).strip()
                def _int(s):
                    try: return int(str(s).replace(",", "") or 0)
                    except ValueError: return 0
                out[sn] = {
                    "balance": _int(row[5]),
                    "short":   _int(row[10]),
                    "buy":     _int(row[2]),
                    "sell":    _int(row[3]),
                }
    except Exception:
        pass
    with _TW_CACHE_LOCK:
        _TW_MARG_CACHE[date_str] = out
    return out


def _get_attention_stocks() -> set:
    """Return TWSE attention / disposal stock numbers (cached 6h)."""
    now = _time.time()
    with _TW_CACHE_LOCK:
        if now - _TW_ATTN_CACHE["ts"] < _TW_CACHE_TTL:
            return _TW_ATTN_CACHE["stocks"].copy()
    stocks: set = set()
    try:
        r = _req.get(
            "https://www.twse.com.tw/rwd/zh/announcement/attention",
            params={"response": "json"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; Scott/1.0)"},
            timeout=8,
        )
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if row and row[0]:
                    stocks.add(str(row[0]).strip())
    except Exception:
        pass
    with _TW_CACHE_LOCK:
        _TW_ATTN_CACHE.update({"ts": now, "stocks": stocks})
    return stocks


def _fetch_tw_fundamental(stock_no: str, is_otc: bool = False) -> dict:
    """
    Fetch PE ratio, PBR, dividend yield from TWSE/TPEX openapi,
    and monthly revenue YoY from MOPS.  Results cached 12 hours.
    """
    import re as _re
    now = _time.time()
    with _TW_CACHE_LOCK:
        cached = _TW_FUND_CACHE.get(stock_no)
        if cached and now - cached.get("ts", 0) < 12 * 3600:
            return cached

    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; Scott/1.0)"}
    result: dict = {"per": None, "pbr": None, "div_yield": None, "rev_yoy": None, "rev_mom": None}

    # ── PE / PBR / yield ────────────────────────────────────────────────────
    try:
        if not is_otc:
            r = _req.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
                         headers=hdrs, timeout=10)
        else:
            r = _req.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
                         headers=hdrs, timeout=10)
        if r.status_code == 200:
            code_key = "Code" if not is_otc else "SecuritiesCompanyCode"
            per_key  = "PER"  if not is_otc else "PE_ratio"
            pbr_key  = "PBR"  if not is_otc else "PB_ratio"
            dy_key   = "DividendYield" if not is_otc else "Dividend_yield"
            for item in r.json():
                if str(item.get(code_key, "")).strip() == stock_no:
                    def _f(k):
                        try: return float(str(item.get(k,"") or "").replace(",","")) or None
                        except ValueError: return None
                    result["per"]      = _f(per_key)
                    result["pbr"]      = _f(pbr_key)
                    result["div_yield"]= _f(dy_key)
                    break
    except Exception:
        pass

    # ── Monthly revenue YoY from MOPS ───────────────────────────────────────
    try:
        from datetime import date as _date
        today    = _date.today()
        roc_year = today.year - 1911
        month    = today.month - 1 or 12
        if month == 12:
            roc_year -= 1
        typek = "otc" if is_otc else "sii"
        r = _req.post(
            "https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs",
            data={
                "encodeURIComponent": "1", "step": "1", "firstin": "1",
                "off": "1", "isQuery": "Y",
                "TYPEK": typek,
                "year": str(roc_year),
                "month": f"{month:02d}",
                "co_id": stock_no,
            },
            headers={**hdrs, "Referer": "https://mops.twse.com.tw/"},
            timeout=10,
        )
        if r.status_code == 200:
            # Extract numeric cells from revenue table; YoY is 7th column, MoM is 6th
            cells = _re.findall(r'<td[^>]*>\s*([-+]?\d[\d,]*\.?\d*)\s*%?\s*</td>', r.text)
            if len(cells) >= 7:
                try: result["rev_mom"] = float(cells[5].replace(",", ""))
                except (ValueError, IndexError): pass
                try: result["rev_yoy"] = float(cells[6].replace(",", ""))
                except (ValueError, IndexError): pass
    except Exception:
        pass

    result["ts"] = now
    with _TW_CACHE_LOCK:
        _TW_FUND_CACHE[stock_no] = result
    return result


def _get_tw_inst_3d(stock_no: str) -> dict:
    """Aggregate 3-day institutional net buy/sell for stock_no (parallel fetch)."""
    dates = _tw_recent_dates(6)
    with ThreadPoolExecutor(max_workers=4) as ex:
        bulks = list(ex.map(_fetch_tw_inst_bulk, dates))
    foreign = trust = days = 0
    for bulk in bulks:
        if stock_no in bulk:
            foreign += bulk[stock_no]["foreign"]
            trust   += bulk[stock_no]["trust"]
            days    += 1
        if days >= 3:
            break
    return {"foreign_3d": foreign, "trust_3d": trust, "days": days}


def _get_tw_marg(stock_no: str) -> dict:
    """Get latest available margin trading data for stock_no."""
    for d in _tw_recent_dates(5):
        bulk = _fetch_tw_marg_bulk(d)
        if stock_no in bulk:
            return bulk[stock_no]
    return {}


def _sma(series: list, n: int) -> float:
    valid = [v for v in series[-n:] if v]
    return sum(valid) / len(valid) if valid else 0.0


def _check_ej_pattern(closes: list, highs: list, volumes: list) -> bool:
    """Heuristic 隔日沖: volume spike day followed by next-day close < prev close, 2+ times in 5 days."""
    if len(closes) < 6 or len(volumes) < 6:
        return False
    avg = _sma(volumes, 20) or _sma(volumes, len(volumes))
    if not avg:
        return False
    count = 0
    for i in range(-5, -1):
        try:
            if volumes[i] > avg * 2.5 and closes[i + 1] < closes[i] * 0.985:
                count += 1
        except IndexError:
            continue
    return count >= 2


def _calc_tw_score(symbol: str) -> dict | None:
    """Calculate 100-point Taiwan stock momentum score (量價45 + 籌碼35 + 基本面20)."""
    sym_key = symbol.upper()
    cached  = _TW_SCORE_CACHE.get(sym_key)
    if cached and _time.time() - cached["ts"] < _TW_SCORE_TTL:
        return cached["data"]

    stock_no = symbol.upper().replace(".TWO", "").replace(".TW", "").strip()
    if not stock_no.isdigit():
        return None

    is_otc = sym_key.endswith(".TWO")
    ohlcv  = _fetch_ohlcv_twse(symbol)
    if not ohlcv:
        return None

    res    = ohlcv["chart"]["result"][0]
    meta   = res["meta"]
    q      = res["indicators"]["quote"][0]
    closes  = [v for v in q.get("close",  []) if v]
    opens   = [v for v in q.get("open",   []) if v]
    highs   = [v for v in q.get("high",   []) if v]
    lows    = [v for v in q.get("low",    []) if v]
    volumes = [v for v in q.get("volume", []) if v]
    n = min(len(closes), len(opens), len(highs), len(lows), len(volumes))
    if n < 20:
        return None
    closes = closes[-n:]; opens = opens[-n:]; highs = highs[-n:]
    lows   = lows[-n:];   volumes = volumes[-n:]

    # ── 1. 量價分 45 ────────────────────────────────────────────────────────
    vp = 0
    avg5  = _sma(volumes,  5)
    avg20 = _sma(volumes, 20)
    today_vol = volumes[-1]
    vol_ratio = today_vol / avg5 if avg5 > 0 else 1.0

    if   vol_ratio >= 3.0: vs = 15
    elif vol_ratio >= 2.0: vs = 11
    elif vol_ratio >= 1.5: vs =  8
    elif vol_ratio >= 1.0: vs =  5
    else:                  vs =  1
    vp += vs

    h20 = max(highs[-20:])
    if   closes[-1] >  h20:          b20_pts = 10
    elif closes[-1] >= h20 * 0.97:   b20_pts =  6
    else:                             b20_pts =  0
    vp += b20_pts

    if len(highs) >= 60:
        h60 = max(highs[-60:])
        if   closes[-1] >  h60:         b60_pts = 10
        elif closes[-1] >= h60 * 0.97:  b60_pts =  6
        else:                            b60_pts =  0
        broke60 = closes[-1] > h60
    else:
        b60_pts = 5      # insufficient data → neutral
        broke60 = None
        h60 = None
    vp += b60_pts

    ma5  = _sma(closes,  5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, min(60, n))
    ma_pts = (3 if ma5 > ma10 else 0) + (3 if ma10 > ma20 else 0) + (4 if ma20 > ma60 else 0)
    vp += ma_pts

    # ── 2. 籌碼分 35 ────────────────────────────────────────────────────────
    inst   = _get_tw_inst_3d(stock_no)
    marg   = _get_tw_marg(stock_no)
    f3d    = inst.get("foreign_3d", 0)
    t3d    = inst.get("trust_3d",   0)
    m_bal  = marg.get("balance", 0)
    m_sht  = marg.get("short",   0)
    m_buy  = marg.get("buy",     0)

    chip = 0
    if   f3d >  5000: chip += 12
    elif f3d >  1000: chip +=  9
    elif f3d >   300: chip +=  6
    elif f3d >     0: chip +=  3
    elif f3d < -1000: chip -=  4

    if   t3d >  1000: chip += 12
    elif t3d >   300: chip +=  9
    elif t3d >    50: chip +=  6
    elif t3d >     0: chip +=  3
    elif t3d <  -300: chip -=  4

    if m_buy > 0:
        chip += 3
    squeeze = False
    if m_bal > 0 and m_sht > 0:
        sq_ratio = m_sht / m_bal
        if sq_ratio > 0.15:
            chip += 5
            squeeze = True

    no_chip_data = inst.get("days", 0) == 0
    if no_chip_data:
        chip = max(chip, 14)   # neutral fallback

    # ── 3. 基本面 20 (PE/PBR + MOPS 月營收 YoY) ─────────────────────────────
    fdata     = _fetch_tw_fundamental(stock_no, is_otc)
    per       = fdata.get("per")
    pbr       = fdata.get("pbr")
    rev_yoy   = fdata.get("rev_yoy")
    div_yield = fdata.get("div_yield")

    # PE 評分 (0-12分)
    if per and per > 0:
        if   per <  10: pe_pts = 12
        elif per <  15: pe_pts = 10
        elif per <  20: pe_pts =  8
        elif per <  30: pe_pts =  5
        else:           pe_pts =  2
        pe_note = f"本益比 {per:.1f}x"
    else:
        pe_pts  = 6    # neutral
        pe_note = "本益比資料未取得"

    # PBR + yield 評分 (0-4分)
    pbr_pts = 0
    if pbr and pbr > 0:
        if   pbr < 1.0: pbr_pts = 4
        elif pbr < 2.0: pbr_pts = 3
        elif pbr < 3.5: pbr_pts = 2
        else:           pbr_pts = 1
    elif div_yield and div_yield > 0:
        pbr_pts = 3 if div_yield >= 4 else 2 if div_yield >= 2 else 1

    # 月營收 YoY 評分 (0-4分)
    if rev_yoy is not None:
        if   rev_yoy >  20: rev_pts = 4
        elif rev_yoy >  10: rev_pts = 3
        elif rev_yoy >   0: rev_pts = 2
        elif rev_yoy > -10: rev_pts = 1
        else:               rev_pts = 0
        rev_note = f"月營收 YoY {rev_yoy:+.1f}%"
    else:
        rev_pts  = 2   # neutral
        rev_note = "月營收資料未取得"

    fund      = pe_pts + pbr_pts + rev_pts
    fund_note = pe_note
    fund_items = [
        {"label": "本益比 (PE)",  "pts": pe_pts,  "detail": pe_note},
        {"label": "股價淨值比",   "pts": pbr_pts, "detail": f"PBR {pbr:.2f}" if pbr else (f"殖利率 {div_yield:.1f}%" if div_yield else "無資料")},
        {"label": "月營收 YoY",   "pts": rev_pts, "detail": rev_note},
    ]

    # ── 4. 排雷扣分 ─────────────────────────────────────────────────────────
    deductions: list = []
    warnings:   list = []

    if avg20 > 0:
        body   = abs(closes[-1] - opens[-1])
        u_shad = highs[-1] - max(closes[-1], opens[-1])
        if today_vol > avg20 * 2.0 and body > 0 and u_shad > body * 1.5:
            deductions.append({"type": "爆量長上影線", "pts": -15, "icon": "🕯️"})

    if n >= 4:
        p3  = closes[-4] or closes[-1]
        g3  = (closes[-1] - p3) / p3 * 100 if p3 else 0
        if g3 > 25:
            deductions.append({"type": f"短線過熱 {g3:.1f}%/3日", "pts": -10, "icon": "🔥"})
        elif g3 > 15:
            deductions.append({"type": f"漲勢偏快 {g3:.1f}%/3日", "pts":  -5, "icon": "⚡"})

    if stock_no in _get_attention_stocks():
        deductions.append({"type": "官方注意股", "pts": -15, "icon": "⛔"})
        warnings.append("官方注意股")

    if _check_ej_pattern(closes, highs, volumes):
        deductions.append({"type": "疑似隔日沖分點污染", "pts": -8, "icon": "⚠"})
        warnings.append("隔日沖")

    # ── 5. 位階 52週 ─────────────────────────────────────────────────────────
    n252  = min(252, len(highs))
    hi52  = max(highs[-n252:])
    lo52  = min(lows[-n252:])
    rng   = hi52 - lo52
    pos   = (closes[-1] - lo52) / rng * 100 if rng > 0 else 50.0
    if pos < 33:
        tier = {"label": "低位啟動", "color": "#3fb950", "icon": "🟢", "pct": round(pos, 1)}
    elif pos < 66:
        tier = {"label": "中位整理", "color": "#e3b341", "icon": "🟡", "pct": round(pos, 1)}
    else:
        tier = {"label": "高位謹慎", "color": "#f85149", "icon": "🔴", "pct": round(pos, 1)}

    # ── 6. 總分 & 操作等級 ───────────────────────────────────────────────────
    deduct = sum(d["pts"] for d in deductions)
    raw    = min(vp, 45) + min(chip, 35) + min(fund, 20)
    final  = max(0, min(100, raw + deduct))

    if   final >= 85: level = {"grade": "A+", "label": "強力買進", "color": "#3fb950", "icon": "🔥"}
    elif final >= 70: level = {"grade": "A",  "label": "值得關注", "color": "#58a6ff", "icon": "✅"}
    elif final >= 55: level = {"grade": "B",  "label": "觀察等待", "color": "#e3b341", "icon": "👀"}
    elif final >= 40: level = {"grade": "C",  "label": "謹慎操作", "color": "#f0883e", "icon": "⚠️"}
    else:             level = {"grade": "D",  "label": "建議迴避", "color": "#f85149", "icon": "❌"}
    if "隔日沖" in warnings:
        level["ej"] = True

    _score_result = {
        "symbol":   symbol.upper(),
        "longName": meta.get("longName", symbol),
        "price":    closes[-1],
        "score":    final,
        "tier":     tier,
        "level":    level,
        "breakdown": {
            "vol_price": {
                "score": min(vp, 45), "max": 45,
                "items": [
                    {"label": "量比放大",    "pts": vs,     "detail": f"今量/{5}日均 = {vol_ratio:.1f}x"},
                    {"label": "突破20日高",  "pts": b20_pts, "detail": f"20日高 = {h20:.2f}"},
                    {"label": "突破60日高",  "pts": b60_pts, "detail": f"60日高 = {h60:.2f}" if h60 else "資料不足"},
                    {"label": "均線多頭排列","pts": ma_pts,  "detail": f"5>{'>'.join(['10'] if ma5>ma10 else [])} 10>{'>'.join(['20'] if ma10>ma20 else [])} 20>{'>'.join(['60'] if ma20>ma60 else [])}"},
                ],
            },
            "chip": {
                "score": min(chip, 35), "max": 35,
                "items": [
                    {"label": "外資近3日",  "pts": min(12,max(-4,(12 if f3d>5000 else 9 if f3d>1000 else 6 if f3d>300 else 3 if f3d>0 else -4))), "detail": f"{f3d:+,} 張" if not no_chip_data else "資料未取得"},
                    {"label": "投信近3日",  "pts": min(12,max(-4,(12 if t3d>1000 else 9 if t3d>300 else 6 if t3d>50 else 3 if t3d>0 else -4))), "detail": f"{t3d:+,} 張" if not no_chip_data else "資料未取得"},
                    {"label": "融資動向",   "pts": 3 if m_buy > 0 else 0, "detail": f"融資餘額 {m_bal:,} 張"},
                    {"label": "軋空潛力",   "pts": 5 if squeeze else 0,   "detail": "券資比高，軋空動能" if squeeze else "無明顯軋空"},
                ],
            },
            "fundamental": {
                "score": min(fund, 20), "max": 20,
                "items": [
                    *fund_items,
                ],
            },
        },
        "deductions": deductions,
        "warnings":   warnings,
        "meta": {
            "vol_ratio": round(vol_ratio, 2),
            "ma5":   round(ma5,  2),
            "ma20":  round(ma20, 2),
            "ma60":  round(ma60, 2),
            "hi52w": round(hi52, 2),
            "lo52w": round(lo52, 2),
        },
    }
    _TW_SCORE_CACHE[sym_key] = {"ts": _time.time(), "data": _score_result}
    return _score_result


@app.route("/api/tw-score/<symbol>")
def tw_score_api(symbol):
    auth = _require_auth()
    if auth:
        return auth
    sym = symbol.upper()
    if not (sym.endswith(".TW") or sym.endswith(".TWO")):
        return jsonify({"error": "only .TW / .TWO symbols supported"}), 400
    try:
        data = _calc_tw_score(sym)
        if not data:
            return jsonify({"error": "data unavailable"}), 503
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Taiwan full stock list (TWSE + TPEX) ──────────────────────────────────────

_TW_STOCKLIST_CACHE: dict = {"ts": 0.0, "data": []}
_TW_STOCKLIST_TTL   = 12 * 3600   # 12 hours


def _fetch_tw_stocklist_full() -> list:
    """
    Fetch all TWSE-listed and TPEX-listed stocks with name + volume.
    Returns list of {symbol, name, volume, price, market}, sorted by volume desc.
    Results cached 12 h.
    """
    now = _time.time()
    if now - _TW_STOCKLIST_CACHE["ts"] < _TW_STOCKLIST_TTL and _TW_STOCKLIST_CACHE["data"]:
        return _TW_STOCKLIST_CACHE["data"]

    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; Scott/1.0)"}
    result: list = []

    def _int(s):
        try: return int(str(s).replace(",", "").strip() or "0")
        except (ValueError, TypeError): return 0

    def _float(s):
        try: return float(str(s).replace(",", "").strip() or "0")
        except (ValueError, TypeError): return 0.0

    # ── TWSE listed (上市) ──────────────────────────────────────────────────
    try:
        r = _req.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers=hdrs, timeout=15,
        )
        if r.status_code == 200:
            for item in r.json():
                code = str(item.get("Code", "")).strip()
                name = str(item.get("Name", "")).strip()
                if not code.isdigit() or not name:
                    continue
                vol   = _int(item.get("TradeVolume", 0))
                price = _float(item.get("ClosingPrice", 0))
                if vol < 10 or price <= 0:
                    continue
                result.append({"symbol": f"{code}.TW", "name": name,
                                "volume": vol, "price": price, "market": "twse"})
    except Exception:
        pass

    # ── TPEX OTC (上櫃) ────────────────────────────────────────────────────
    try:
        r = _req.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers=hdrs, timeout=15,
        )
        if r.status_code == 200:
            for item in r.json():
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanyName", "") or item.get("Name", "")).strip()
                if not code.isdigit() or not name:
                    continue
                vol   = _int(item.get("TradingShares", 0))
                price = _float(item.get("Close", 0) or item.get("ClosingPrice", 0))
                if vol < 10 or price <= 0:
                    continue
                result.append({"symbol": f"{code}.TWO", "name": name,
                                "volume": vol, "price": price, "market": "tpex"})
    except Exception:
        pass

    result.sort(key=lambda x: x["volume"], reverse=True)

    if result:
        _TW_STOCKLIST_CACHE.update({"ts": now, "data": result})

    return result


@app.route("/api/tw-stocklist")
def tw_stocklist_api():
    """Return Taiwan listed + OTC stocks sorted by volume. ?limit=N (default 500, max 2000)."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limit = min(int(request.args.get("limit", 500)), 2000)
        data  = _fetch_tw_stocklist_full()
        resp  = Response(
            json.dumps({"ok": True, "count": len(data), "data": data[:limit]},
                       ensure_ascii=False),
            status=200, mimetype="application/json"
        )
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


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


# ── Finnhub OHLCV helper ──────────────────────────────────────────────────────

def _fetch_ohlcv_finnhub(symbol: str, fh_key: str) -> dict | None:
    """
    Fetch daily OHLCV from Finnhub /stock/candle (resolution=D, 2 years).
    Returns Yahoo-format dict or None on failure.
    Free tier: 60 req/min.  No TW stocks.
    """
    import time as _t
    now_ts  = int(_t.time())
    from_ts = now_ts - 2 * 365 * 24 * 3600   # 2 years back
    try:
        r = _req.get(
            "https://finnhub.io/api/v1/stock/candle",
            params={
                "symbol":     symbol.upper(),
                "resolution": "D",
                "from":       from_ts,
                "to":         now_ts,
                "token":      fh_key,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        if d.get("s") != "ok":
            return None

        timestamps = d["t"]
        opens      = [round(v, 4) for v in d["o"]]
        highs      = [round(v, 4) for v in d["h"]]
        lows       = [round(v, 4) for v in d["l"]]
        closes     = [round(v, 4) for v in d["c"]]
        volumes    = [int(v) for v in d["v"]]

        if not timestamps:
            return None

        last_close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else last_close

        # Fetch company name via Finnhub profile2
        long_name = symbol.upper()
        try:
            pr = _req.get(
                "https://finnhub.io/api/v1/stock/profile2",
                params={"symbol": symbol.upper(), "token": fh_key},
                timeout=6,
            )
            if pr.status_code == 200:
                long_name = pr.json().get("name") or long_name
        except Exception:
            pass

        return {
            "chart": {
                "result": [{
                    "meta": {
                        "symbol":              symbol.upper(),
                        "longName":            long_name,
                        "regularMarketPrice":  last_close,
                        "previousClose":       prev_close,
                        "currency":            "USD",
                        "_source":             "finnhub",
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
                "error": None,
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

    # Taiwan stock fallback: TWSE / TPEX official API
    sym_upper = symbol.upper()
    if sym_upper.endswith(".TW") or sym_upper.endswith(".TWO"):
        twse_result = _fetch_ohlcv_twse(sym_upper)
        if twse_result:
            resp = Response(json.dumps(twse_result), status=200, mimetype="application/json")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

    # Non-TW stocks: try Finnhub before Alpha Vantage
    sym_upper = symbol.upper()
    if not (sym_upper.endswith(".TW") or sym_upper.endswith(".TWO")):
        fh_key = os.environ.get("FINNHUB_KEY", "")
        if fh_key:
            fh_result = _fetch_ohlcv_finnhub(symbol, fh_key)
            if fh_result:
                resp = Response(json.dumps(fh_result), status=200, mimetype="application/json")
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp

    # Final structured fallback: Alpha Vantage
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

    # Finnhub fallback (non-TW only)
    sym_up = symbol.upper()
    if not (sym_up.endswith(".TW") or sym_up.endswith(".TWO")):
        fh_key = os.environ.get("FINNHUB_KEY", "")
        if fh_key:
            try:
                fh_result = _fetch_ohlcv_finnhub(symbol, fh_key)
                if fh_result:
                    res  = fh_result["chart"]["result"][0]
                    q    = res["indicators"]["quote"][0]
                    rows = []
                    for i, t in enumerate(res["timestamp"]):
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
        return jsonify({"ok": False, "error": _claude_error_msg(e)}), 500


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


# ── Shared Claude error mapper ────────────────────────────────────────────────

def _claude_error_msg(e: Exception) -> str:
    err = str(e)
    if "credit balance is too low" in err or "credit_balance" in err:
        return "⚠️ Anthropic API 額度不足，請至 console.anthropic.com → Billing 儲值後再試"
    if "invalid_api_key" in err or "authentication" in err.lower():
        return "⚠️ API Key 無效，請確認 ANTHROPIC_API_KEY 設定正確"
    if "overloaded" in err or "529" in err:
        return "⚠️ Claude 伺服器目前過載，請稍後幾分鐘再試"
    if "rate_limit" in err or "429" in err:
        return "⚠️ 請求過於頻繁，請稍等 1 分鐘再試"
    return "⚠️ AI 回應失敗，請稍後再試"

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
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        reply = resp.content[0].text if resp.content else ""
        return jsonify({"ok": True, "reply": reply})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": _claude_error_msg(e)}), 500


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
        return jsonify({"ok": False, "error": _claude_error_msg(e)}), 500


# ── Stock Notes ────────────────────────────────────────────────────────────────

@app.route("/api/notes", methods=["GET"])
def api_notes_get():
    data = _load_user_data()
    return jsonify(ok=True, notes=data.get("stock_notes", {}))

@app.route("/api/notes/<symbol>", methods=["POST", "DELETE"])
def api_note_update(symbol):
    symbol = symbol.upper()[:20]
    data = _load_user_data()
    notes = data.get("stock_notes", {})
    if request.method == "DELETE":
        notes.pop(symbol, None)
    else:
        body = request.get_json(force=True, silent=True) or {}
        note = (body.get("note") or "").strip()[:500]
        if note:
            notes[symbol] = {"note": note, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
        else:
            notes.pop(symbol, None)
    _save_user_data({"stock_notes": notes})
    return jsonify(ok=True)


# ── Price Alerts ───────────────────────────────────────────────────────────────

@app.route("/api/price-alerts", methods=["GET"])
def api_price_alerts_get():
    data = _load_user_data()
    return jsonify(ok=True, alerts=data.get("price_alerts", []))

@app.route("/api/price-alerts", methods=["POST"])
def api_price_alerts_add():
    body = request.get_json(force=True, silent=True) or {}
    symbol  = (body.get("symbol") or "").upper().strip()[:20]
    target  = float(body.get("target", 0))
    direction = body.get("direction", "above")  # "above" | "below"
    note    = (body.get("note") or "").strip()[:100]
    if not symbol or target <= 0:
        return jsonify(ok=False, error="需要 symbol 和 target"), 400
    data = _load_user_data()
    alerts = data.get("price_alerts", [])
    alerts = [a for a in alerts if not (a["symbol"] == symbol and a["direction"] == direction)]
    alerts.append({"symbol": symbol, "target": target, "direction": direction,
                   "note": note, "created": datetime.now().strftime("%Y-%m-%d %H:%M")})
    _save_user_data({"price_alerts": alerts})
    return jsonify(ok=True)

@app.route("/api/price-alerts/<symbol>", methods=["DELETE"])
def api_price_alerts_del(symbol):
    symbol = symbol.upper()
    direction = request.args.get("direction", "")
    data = _load_user_data()
    alerts = data.get("price_alerts", [])
    alerts = [a for a in alerts if not (a["symbol"] == symbol and
              (not direction or a["direction"] == direction))]
    _save_user_data({"price_alerts": alerts})
    return jsonify(ok=True)


# ── Earnings Calendar ──────────────────────────────────────────────────────────

@app.route("/api/earnings/<symbol>")
def api_earnings(symbol):
    """Fetch next earnings date from Yahoo Finance quoteSummary."""
    symbol = symbol.upper()[:20]
    try:
        r = _req.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={"modules": "calendarEvents,defaultKeyStatistics"},
            headers=YAHOO_HEADERS, timeout=10
        )
        if r.status_code != 200:
            return jsonify(ok=False, error=f"Yahoo HTTP {r.status_code}"), 502
        j = r.json()
        result = j.get("quoteSummary", {}).get("result", [{}])[0] if j.get("quoteSummary", {}).get("result") else {}
        cal = result.get("calendarEvents", {})
        earnings_dates = cal.get("earnings", {}).get("earningsDate", [])
        next_date = None
        if earnings_dates:
            ts = earnings_dates[0].get("raw")
            if ts:
                next_date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        eps_fwd = result.get("defaultKeyStatistics", {}).get("forwardEps", {}).get("fmt")
        pe_fwd  = result.get("defaultKeyStatistics", {}).get("forwardPE", {}).get("fmt")
        return jsonify(ok=True, symbol=symbol,
                       next_earnings=next_date,
                       forward_eps=eps_fwd,
                       forward_pe=pe_fwd)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:100]), 500


# ── Price alert background checker ────────────────────────────────────────────

def _run_price_alert_checker():
    """Check price alerts every 10 minutes and send notifications when triggered."""
    _time.sleep(30)  # wait for app to boot
    while True:
        try:
            data = _load_user_data()
            alerts = data.get("price_alerts", [])
            if alerts:
                remaining, triggered = [], []
                for a in alerts:
                    try:
                        r = _req.get(
                            f"https://query1.finance.yahoo.com/v8/finance/chart/{a['symbol']}",
                            params={"range": "1d", "interval": "1m"},
                            headers=YAHOO_HEADERS, timeout=8)
                        if r.status_code == 200:
                            price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
                            hit = (a["direction"] == "above" and price >= a["target"]) or \
                                  (a["direction"] == "below" and price <= a["target"])
                            if hit:
                                triggered.append({**a, "current_price": price})
                            else:
                                remaining.append(a)
                        else:
                            remaining.append(a)
                    except Exception:
                        remaining.append(a)
                if triggered:
                    _save_user_data({"price_alerts": remaining})
                    # Send notification via existing alerts endpoint
                    s = data.get("alertSettings_v1") or {}
                    if isinstance(s, str):
                        try: s = json.loads(s)
                        except Exception: s = {}
                    email = s.get("email", "")
                    line_token = s.get("lineToken", "") or os.environ.get("LINE_NOTIFY_TOKEN", "")
                    signals = [{"symbol": a["symbol"],
                                "note": f"價格警報：{'高於' if a['direction']=='above' else '低於'} "
                                        f"${a['target']} (現價 ${a['current_price']:.2f})"
                                        f"{' — '+a['note'] if a.get('note') else ''}"}
                               for a in triggered]
                    if email or line_token:
                        with app.app_context():
                            _req.post(
                                "http://localhost:" + str(int(os.environ.get("PORT", 8080))),
                                timeout=5)
                        # Use internal send logic directly
                        try:
                            _send_alerts_internal(signals, email, line_token)
                        except Exception:
                            pass
                    print(f"[PRICE ALERT] Triggered: {[a['symbol'] for a in triggered]}", flush=True)
        except Exception:
            pass
        _time.sleep(600)  # 10 minutes

def _send_alerts_internal(signals, email, line_token):
    """Shared alert sending logic (reused by price alert checker)."""
    msg_lines = ["📊 Scott 價格警報"] + [f"  • {s['symbol']}: {s['note']}" for s in signals]
    msg = "\n".join(msg_lines)
    if line_token:
        _req.post("https://notify-api.line.me/api/notify",
                  headers={"Authorization": f"Bearer {line_token}"},
                  data={"message": "\n" + msg}, timeout=10)
    if email:
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        if smtp_user and smtp_pass:
            import smtplib, email.mime.text as _emt
            m = _emt.MIMEText(msg, "plain", "utf-8")
            m["Subject"] = "📊 Scott 價格警報"
            m["From"] = smtp_user
            m["To"] = email
            with smtplib.SMTP(smtp_host, smtp_port) as srv:
                srv.starttls()
                srv.login(smtp_user, smtp_pass)
                srv.sendmail(smtp_user, [email], m.as_string())

_price_alert_thread = _threading.Thread(target=_run_price_alert_checker, daemon=True)
_price_alert_thread.start()


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


# ── Decision Engine API routes ────────────────────────────────────────────────
import decision_engine as _de


def _get_ohlcv_norm(symbol: str):
    """Fetch OHLCV for any symbol and return normalised dict, or None."""
    sym_up = symbol.upper()
    if sym_up.endswith(".TW") or sym_up.endswith(".TWO"):
        raw = _fetch_ohlcv_twse(sym_up)
        return _de.normalize_yahoo(raw) if raw else None
    else:
        rows = _fetch_ohlcv_server(sym_up)
        return _de.normalize_list(rows) if rows else None


@app.route("/api/chase-risk/<symbol>")
def api_chase_risk(symbol):
    """Chase Risk Score for a single symbol."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        sym   = symbol.upper().strip()
        ohlcv = _get_ohlcv_norm(sym)
        if not ohlcv:
            return jsonify({"ok": False, "error": f"無法取得 {sym} 的 K 線資料"}), 404
        result = _de.run_chase_risk(ohlcv)
        result["symbol"] = sym
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sell-decision", methods=["POST"])
def api_sell_decision():
    """
    Sell Decision Engine.
    Body: {symbol, cost, holding_days?, stop_pct?, trail_pct?, profit_target_pct?}
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        data  = request.json or {}
        sym   = str(data.get("symbol", "")).upper().strip()
        cost  = float(data.get("cost", 0) or 0)
        if not sym:
            return jsonify({"ok": False, "error": "symbol 必填"}), 400
        if cost <= 0:
            return jsonify({"ok": False, "error": "cost 必填且需 > 0"}), 400

        ohlcv = _get_ohlcv_norm(sym)
        if not ohlcv:
            return jsonify({"ok": False, "error": f"無法取得 {sym} 的 K 線資料"}), 404

        # Parse optional holding info
        buy_date_str  = data.get("buy_date", "")
        holding_days  = int(data.get("holding_days", 0) or 0)
        if not holding_days and buy_date_str:
            from datetime import date as _d, datetime as _dtm
            try:
                bd = _dtm.strptime(buy_date_str, "%Y-%m-%d").date()
                holding_days = (_d.today() - bd).days
            except Exception:
                pass

        result = _de.run_sell_decision(
            ohlcv,
            cost=cost,
            holding_days=max(holding_days, 0),
            stop_pct=float(data.get("stop_pct", 8) or 8),
            trail_pct=float(data.get("trail_pct", 15) or 15),
            profit_target_pct=float(data.get("profit_target_pct", 20) or 20),
        )
        result["symbol"] = sym
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/capital-efficiency", methods=["POST"])
def api_capital_efficiency():
    """
    Capital Efficiency Score for one or more holdings.
    Body: {holdings: [{symbol, cost, qty, buy_date?, current_price?}, ...],
           benchmark?: "QQQ"|"SPY"|"0050.TW"}
    Returns per-holding score + portfolio summary.
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        data      = request.json or {}
        holdings  = data.get("holdings", [])
        bench_sym = str(data.get("benchmark", "SPY") or "SPY").upper()

        if not holdings:
            return jsonify({"ok": False, "error": "holdings 必填"}), 400

        # Fetch benchmark return (best effort)
        bench_return: float | None = None
        try:
            bench_ohlcv = _get_ohlcv_norm(bench_sym)
            if bench_ohlcv and len(bench_ohlcv["closes"]) >= 21:
                bc = bench_ohlcv["closes"]
                bench_return = (bc[-1] - bc[-21]) / bc[-21] * 100
        except Exception:
            pass

        results = []
        for h in holdings[:20]:   # cap at 20 holdings
            sym  = str(h.get("symbol", "")).upper().strip()
            cost = float(h.get("cost", 0) or 0)
            if not sym or cost <= 0:
                continue
            ohlcv = _get_ohlcv_norm(sym)
            if not ohlcv:
                results.append({"symbol": sym, "ok": False, "error": "K 線資料不可用"})
                continue

            # Update current_price from OHLCV if not provided
            if not h.get("current_price") and ohlcv["closes"]:
                h = dict(h)
                h["current_price"] = ohlcv["closes"][-1]

            res = _de.run_capital_efficiency(h, ohlcv,
                                             benchmark_return=bench_return)
            results.append(res)

        if not results:
            return jsonify({"ok": False, "error": "無有效持倉資料"}), 400

        # Portfolio-level summary
        valid = [r for r in results if r.get("ok")]
        avg_score = round(sum(r["score"] for r in valid) / len(valid)) if valid else None

        return jsonify({
            "ok":             True,
            "benchmark":      bench_sym,
            "benchmark_return_pct": round(bench_return, 2) if bench_return is not None else None,
            "holdings":       results,
            "portfolio_avg_score": avg_score,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sector-leadership", methods=["POST"])
def api_sector_leadership():
    """
    Sector Leadership Score.
    Body: {sector: "台股半導體IC", symbols: ["2330.TW", ...], max_stocks?: 20}
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        data        = request.json or {}
        sector_name = str(data.get("sector", "未知板塊")).strip()
        symbols     = [s.upper().strip() for s in (data.get("symbols") or [])
                       if isinstance(s, str) and s.strip()]
        max_stocks  = min(int(data.get("max_stocks", 20) or 20), 30)

        if not symbols:
            return jsonify({"ok": False, "error": "symbols 必填"}), 400

        symbols = symbols[:max_stocks]

        # Parallel OHLCV fetch
        stocks_ohlcv: dict = {}
        def _fetch_one(sym: str):
            ohlcv = _get_ohlcv_norm(sym)
            return sym, ohlcv

        with ThreadPoolExecutor(max_workers=6) as ex:
            for sym, ohlcv in ex.map(_fetch_one, symbols):
                if ohlcv:
                    stocks_ohlcv[sym] = ohlcv

        result = _de.run_sector_leadership(sector_name, stocks_ohlcv)
        result["fetched"]  = len(stocks_ohlcv)
        result["requested"]= len(symbols)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


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
