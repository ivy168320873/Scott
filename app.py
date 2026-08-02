from flask import Flask, render_template, jsonify, request, Response, make_response, session, redirect, url_for, g
from urllib.parse import urlsplit
from werkzeug.middleware.proxy_fix import ProxyFix
import requests as _req
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import time as _time
import traceback, os, json, hashlib, hmac, re
import html as _html
import smtplib
import email.mime.multipart
import email.mime.text
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, deque

# Keep every SQLite-backed module on the Railway Volume when one is mounted.
# This must run before importing modules that read USER_DATA_DB at import time.
_RAILWAY_VOLUME = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
if not os.environ.get("USER_DATA_DB", "").strip():
    os.environ["USER_DATA_DB"] = (
        os.path.join(_RAILWAY_VOLUME, "user_data.db")
        if _RAILWAY_VOLUME
        else "./user_data.db"
    )
import data_provider as _dp
import analyzer
import backtest as _bt
import signals as _sig
import patterns as _pat
import trader as _trader
import risk_manager as _rm
import scheduler as _sched
import monitor as _mon
import sys as _sys
def get_twse_stock_day_all():
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    response = _req.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(data)
    return df
# 讓 app 能 import cli_agent 子目錄裡的 web_agent（手機版聊天）。
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli_agent"))
try:
    import web_agent as _web_agent
except Exception as _e:  # noqa: BLE001 — 匯入失敗不影響主程式其他功能
    _web_agent = None
    print(f"[agent] web_agent 不可用：{_e}", flush=True)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ── Session / Auth config ─────────────────────────────────────────────────────
_IS_PRODUCTION = (
    os.environ.get("RAILWAY_ENVIRONMENT", "").lower() in {"production", "prod"}
    or os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() in {"production", "prod"}
    or os.environ.get("FLASK_ENV", "").lower() in {"production", "prod"}
)
_ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
_ALLOW_INSECURE_NO_AUTH = os.environ.get("ALLOW_INSECURE_NO_AUTH", "false").lower() == "true"

if _IS_PRODUCTION and not _ACCESS_CODE and not _ALLOW_INSECURE_NO_AUTH:
    raise RuntimeError(
        "ACCESS_CODE must be configured in production. "
        "Set ALLOW_INSECURE_NO_AUTH=true only for a deliberately public demo."
    )
if _IS_PRODUCTION and not os.environ.get("SECRET_KEY", "").strip():
    raise RuntimeError("SECRET_KEY must be configured in production.")

app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = _IS_PRODUCTION
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

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

_USER_DATA_DB   = os.environ["USER_DATA_DB"]
_USER_DATA_FILE = os.environ.get("USER_DATA_FILE", "./user_data.json")  # legacy, migrate only
_user_data_lock = _threading.Lock()
_user_data_mem: dict = {}   # in-memory read cache
_CLIENT_SYNC_KEYS = {
    "portfolio_v1", "alertSettings_v1", "customScanList",
    "signalHistory_v2", "radarWatchlist", "radarActive",
}


def _strip_line_secrets(value):
    """Remove retired client-side LINE credentials from persisted data."""
    if isinstance(value, dict):
        return {
            k: _strip_line_secrets(v)
            for k, v in value.items()
            if k.lower().replace("_", "") not in {"linetoken", "linenotifytoken"}
        }
    if isinstance(value, list):
        return [_strip_line_secrets(v) for v in value]
    return value


def _db_connect():
    parent = os.path.dirname(os.path.abspath(_USER_DATA_DB))
    os.makedirs(parent, exist_ok=True)
    con = _sqlite3.connect(_USER_DATA_DB, timeout=10, check_same_thread=False)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con

# ── Production / demo-guard constants ────────────────────────────────────────
_DEMO_WARNING_MSG = "⚠️ 目前使用模擬資料，不能作為交易決策。"
_DEMO_SIGNAL_CAP  = {"bullish": "neutral", "mild-bullish": "neutral"}
_DEMO_SIGNAL_TEXT = "觀察（模擬資料）"


def _is_demo_ohlcv(ohlcv: dict | None) -> bool:
    """Return True when the OHLCV dict came from the demo fallback."""
    return bool(ohlcv and ohlcv.get("is_demo", False))


def _apply_demo_guard(result: dict) -> dict:
    """
    Cap strong buy signals to 'neutral' / '觀察' when running on demo data.
    Also injects demo_data_warning into the result.
    Only active in production; dev/sandbox still shows full signals for testing.
    """
    if not _IS_PRODUCTION:
        result["demo_data_warning"] = _DEMO_WARNING_MSG
        return result
    sc = result.get("signal_class", "")
    if sc in _DEMO_SIGNAL_CAP:
        result = dict(result)
        result["signal_class"] = _DEMO_SIGNAL_CAP[sc]
        result["signal"]       = _DEMO_SIGNAL_TEXT
    result["demo_data_warning"] = _DEMO_WARNING_MSG
    return result


def _init_user_db():
    """Create SQLite table; migrate from legacy JSON on first run."""
    global _user_data_mem
    con = _db_connect()
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
    _user_data_mem = {
        r[0]: _strip_line_secrets(json.loads(r[1]))
        for r in con.execute("SELECT key,value FROM kv")
    }
    # Purge legacy LINE Notify tokens that older versions stored in SQLite.
    for key, value in _user_data_mem.items():
        con.execute(
            "UPDATE kv SET value=? WHERE key=?",
            (json.dumps(value, ensure_ascii=False), key),
        )
    con.commit()
    con.close()


try:
    _init_user_db()
except Exception as _db_error:
    traceback.print_exc()
    if _IS_PRODUCTION:
        raise RuntimeError(f"Unable to initialise persistent database: {_db_error}") from _db_error

if _IS_PRODUCTION and not _RAILWAY_VOLUME:
    print(
        "[PERSISTENCE WARNING] No RAILWAY_VOLUME_MOUNT_PATH detected; "
        "SQLite data may be lost on redeploy.",
        flush=True,
    )


def _load_user_data() -> dict:
    return dict(_user_data_mem)


def _save_user_data(patch: dict):
    global _user_data_mem
    now_iso = datetime.now(timezone.utc).isoformat()
    with _user_data_lock:
        patch = _strip_line_secrets(patch)
        try:
            con = _db_connect()
            for k, v in patch.items():
                con.execute("INSERT OR REPLACE INTO kv(key,value,ts) VALUES(?,?,?)",
                            (k, json.dumps(v, ensure_ascii=False), now_iso))
            con.commit()
            con.close()
            _user_data_mem.update(patch)
        except Exception:
            traceback.print_exc()
            if _IS_PRODUCTION:
                raise

def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _safe_next_url(value: str | None) -> str:
    """Allow only same-site absolute paths after login."""
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"

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
    """Get the client IP after ProxyFix consumes Railway's trusted hop."""
    return (request.remote_addr or "unknown")[:64]

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


_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = _threading.Lock()


def _rate_limit(bucket: str, limit: int, window_seconds: int) -> Response | None:
    """Small in-process limiter for one-instance deployments.

    This is intentionally a safety net, not a distributed quota. If the app is
    scaled to multiple instances, move the buckets to Redis.
    """
    now = _time.monotonic()
    key = f"{bucket}:{_get_ip()}"
    with _RATE_LOCK:
        hits = _RATE_BUCKETS[key]
        while hits and now - hits[0] >= window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            resp = jsonify(ok=False, error="請求過於頻繁，請稍後再試")
            resp.status_code = 429
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        hits.append(now)
    return None


def _valid_hhmm(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is not None


def _valid_timezone_name(value) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return False
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(value)
        return True
    except Exception:
        return False


def _normalise_symbols(value, *, max_items: int = 50) -> list[str] | None:
    if not isinstance(value, list) or len(value) > max_items:
        return None
    symbols: list[str] = []
    for item in value:
        symbol = str(item or "").upper().strip()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol):
            return None
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _normalise_holdings(value, *, max_items: int = 50) -> list[dict] | None:
    if not isinstance(value, list) or not value or len(value) > max_items:
        return None
    holdings = []
    for item in value:
        if not isinstance(item, dict):
            return None
        symbol = str(item.get("symbol") or "").upper().strip()
        try:
            cost = float(item.get("cost", item.get("buyPrice", 0)) or 0)
            qty = float(item.get("qty", item.get("shares", 0)) or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol)
            or not np.isfinite(cost)
            or not np.isfinite(qty)
            or not 0 < cost <= 10_000_000
            or not 0 < qty <= 1_000_000_000
        ):
            return None
        buy_date = str(item.get("buy_date") or item.get("buyDate") or "")[:10]
        if buy_date:
            try:
                datetime.strptime(buy_date, "%Y-%m-%d")
            except ValueError:
                return None
        holdings.append({"symbol": symbol, "cost": cost, "qty": qty, "buy_date": buy_date})
    return holdings


def _normalise_text_list(value, *, max_items: int = 50, max_length: int = 80) -> list[str] | None:
    if not isinstance(value, list) or len(value) > max_items:
        return None
    result = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()[:max_length]
        if text:
            result.append(text)
    return result


_PUBLIC_ENDPOINTS = {"login", "logout", "static", "robots_txt", "healthz"}

@app.before_request
def _require_auth():
    """Block every request unless the session is authenticated or ACCESS_CODE is unset."""
    if getattr(g, "auth_checked", False):
        return
    g.auth_checked = True
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if request.path.startswith("/api/"):
        limited = _rate_limit("api", 180, 60)
        if limited is not None:
            return limited
    if not _ACCESS_CODE:
        return  # explicitly permitted local/public mode
    if hmac.compare_digest(str(session.get("auth", "")), _hash(_ACCESS_CODE)):
        return
    # API calls return JSON 401 instead of redirect
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Unauthorized"), 401
    return redirect(url_for("login", next=request.path))


@app.before_request
def _reject_cross_site_mutations():
    """Reject authenticated state changes initiated by another site."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.endpoint == "login":
        return
    origin = (request.headers.get("Origin") or "").rstrip("/")
    origin_host = urlsplit(origin).netloc.lower() if origin else ""
    if origin_host and origin_host != request.host.lower():
        return jsonify(ok=False, error="Cross-site request blocked"), 403


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' https://query1.finance.yahoo.com "
        "https://query2.finance.yahoo.com https://www.twse.com.tw "
        "https://openapi.twse.com.tw https://www.tpex.org.tw"
    )
    if _IS_PRODUCTION:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith("/api/") or request.path.startswith("/admin"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.route("/healthz")
def healthz():
    """Cheap public liveness probe; never calls market or broker APIs."""
    return jsonify(ok=True, service="scott", ts=datetime.now(timezone.utc).isoformat())

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
            if _ACCESS_CODE and hmac.compare_digest(_hash(code), _hash(_ACCESS_CODE)):
                _reset_attempts(ip)
                session.permanent = True
                session["auth"] = _hash(_ACCESS_CODE)
                _append_log(ip, device, True, "登入成功")
                return redirect(_safe_next_url(request.args.get("next")))
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
        "LINE_CHANNEL_ACCESS_TOKEN": bool(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")),
        "LINE_USER_ID":    bool(os.environ.get("LINE_USER_ID")),
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
@app.route("/twse")
def twse_api_test():
    df = get_twse_stock_day_all()
    return df.head(20).to_json(orient="records", force_ascii=False)
@app.route("/api/admin/test-connections")
def api_admin_test_connections():
    """Test external API connectivity — shows in admin dashboard."""
    limited = _rate_limit("admin_connection_test", 2, 60)
    if limited is not None:
        return limited
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
    limited = _rate_limit("user_data_write", 30, 60)
    if limited is not None:
        return limited
    raw_patch = request.get_json(force=True, silent=True) or {}
    if not isinstance(raw_patch, dict):
        return jsonify(ok=False, error="JSON object required"), 400
    patch = {k: v for k, v in raw_patch.items() if k in _CLIENT_SYNC_KEYS}
    if not patch:
        return jsonify(ok=False, error="No supported sync keys"), 400
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
_stored_alert_schedule = _load_user_data().get("alert_schedule_settings", {})
if isinstance(_stored_alert_schedule, dict):
    if isinstance(_stored_alert_schedule.get("enabled"), bool):
        _alert_schedule_settings["enabled"] = _stored_alert_schedule["enabled"]
    if _valid_hhmm(_stored_alert_schedule.get("time")):
        _alert_schedule_settings["time"] = _stored_alert_schedule["time"]
    if _valid_timezone_name(_stored_alert_schedule.get("timezone")):
        _alert_schedule_settings["timezone"] = _stored_alert_schedule["timezone"]

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
    symbol = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol):
        return jsonify({"chart": {"result": None, "error": {"description": "invalid symbol"}}}), 400
    allowed_ranges = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
    allowed_intervals = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d"}
    requested_range = request.args.get("range", "1y")
    requested_interval = request.args.get("interval", "1d")
    params = {
        "range": requested_range if requested_range in allowed_ranges else "1y",
        "interval": requested_interval if requested_interval in allowed_intervals else "1d",
        "events": "history",
        "includePrePost": request.args.get("includePrePost", "false").lower() == "true",
    }
    try:
        r = _req.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params=params, headers=YAHOO_HEADERS, timeout=10,
        )
        if r.status_code == 200:
            return Response(r.content, status=200, mimetype="application/json")
    except Exception:
        pass

    # Intraday data must remain intraday; substituting daily candles would make
    # opening-range and extended-hours calculations invalid.
    interval = str(params.get("interval", "1d"))
    if interval != "1d":
        return jsonify({
            "chart": {
                "result": None,
                "error": {"description": "intraday data unavailable"},
            }
        }), 503

    period = str(params.get("range", "1y"))
    normalised = _dp.get_ohlcv(symbol, period)
    if not normalised or not normalised.get("closes"):
        return jsonify({
            "chart": {
                "result": None,
                "error": {"description": "live market data unavailable"},
            }
        }), 503

    closes = normalised["closes"]
    timestamps = normalised.get("timestamps") or []
    if len(timestamps) != len(closes) or not all(timestamps):
        now_ts = int(datetime.now(timezone.utc).timestamp())
        timestamps = [now_ts - (len(closes) - 1 - i) * 86400 for i in range(len(closes))]
    return jsonify({
        "chart": {
            "result": [{
                "meta": {
                    "symbol": symbol,
                    "longName": symbol,
                    "regularMarketPrice": closes[-1],
                    "previousClose": closes[-2] if len(closes) > 1 else closes[-1],
                    "currency": "TWD" if symbol.endswith((".TW", ".TWO")) else "USD",
                    "_source": normalised.get("source", "unknown"),
                    "_demo": bool(normalised.get("is_demo", False)),
                },
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open": normalised.get("opens", closes),
                        "high": normalised.get("highs", closes),
                        "low": normalised.get("lows", closes),
                        "close": closes,
                        "volume": normalised.get("volumes", [0] * len(closes)),
                    }],
                    "adjclose": [{"adjclose": closes}],
                },
            }],
            "error": None,
        }
    })


# ── AI Analysis endpoint ───────────────────────────────────────────────────────

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    try:
        limited = _rate_limit("analyze", 12, 60)
        if limited is not None:
            return limited
        payload = request.json or {}
        sym = str(payload.get("symbol", "")).upper().strip()
        if sym and not re.fullmatch(r"[A-Z0-9.\-]{1,20}", sym):
            return jsonify({"ok": False, "error": "invalid symbol"}), 400

        if sym:
            # Merge any pre-supplied decision_results from the frontend.
            # We then fill in any missing keys automatically.
            dr: dict = dict(payload.get("decision_results") or {})

            # ── chase_risk + sell_decision: skip if frontend already sent them ─
            if "chase_risk" not in dr:
                try:
                    ohlcv_norm = _get_ohlcv_norm(sym)
                    if ohlcv_norm:
                        dr["chase_risk"] = _de.run_chase_risk(ohlcv_norm)
                        cost = float(payload.get("cost", 0) or 0)
                        if cost > 0:
                            holding_days = int(payload.get("holding_days", 0) or 0)
                            dr["sell_decision"] = _de.run_sell_decision(
                                ohlcv_norm, cost=cost, holding_days=holding_days
                            )
                except Exception as _de_err:
                    print(f"[api_analyze] chase_risk enrich failed: {_de_err}")

            # ── sector_leadership: auto-detect and fetch (Phase 2.5) ──────────
            if "sector_leadership" not in dr:
                try:
                    sector_name = _smap.get_sector(sym)
                    if sector_name:
                        sector_syms = _smap.get_sector_symbols(sector_name)
                        sector_ohlcv: dict = {}
                        def _fetch_s(s: str):
                            return s, _get_ohlcv_norm(s)
                        with ThreadPoolExecutor(max_workers=6) as ex:
                            for s, ov in ex.map(_fetch_s, sector_syms[:8]):
                                if ov:
                                    sector_ohlcv[s] = ov
                        dr["sector_leadership"] = _de.run_sector_leadership(
                            sector_name, sector_ohlcv
                        )
                    else:
                        # Unknown sector — None signals analyzer to show 板塊強度資料不足
                        dr["sector_leadership"] = None
                except Exception as _sl_err:
                    print(f"[api_analyze] sector_leadership fetch failed: {_sl_err}")

            if dr:
                payload = {**payload, "decision_results": dr}

        result = analyzer.analyze(payload)
        # Include Phase 1 decision_results in response so frontend can render
        # Chase Risk badge, Sell Decision block, and Sector Strength panel.
        dr_out = payload.get("decision_results") or {}

        # Demo guard: cap signals when data is synthetic
        ohlcv_for_guard = _get_ohlcv_norm(sym) if sym else None
        is_demo = _is_demo_ohlcv(ohlcv_for_guard)
        if is_demo:
            result = _apply_demo_guard(result)

        # Phase 11: auto-record notable signals in background
        if sym:
            import threading as _thr
            _thr.Thread(
                target=_obs_auto_record_from_result,
                args=(sym, {**result, "is_demo": is_demo}),
                daemon=True,
            ).start()

        return jsonify({
            "ok": True, **result,
            "decision_results": dr_out,
            "is_demo": is_demo,
            "data_source": (ohlcv_for_guard or {}).get("source", "unknown"),
        })
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

def _normalised_ohlcv_rows(data: dict) -> list[dict]:
    """Convert data_provider output to the row format used by backtest.py."""
    closes = data.get("closes") or []
    opens = data.get("opens") or closes
    highs = data.get("highs") or closes
    lows = data.get("lows") or closes
    volumes = data.get("volumes") or [0] * len(closes)
    timestamps = data.get("timestamps") or []
    rows = []
    for i, close in enumerate(closes):
        if i < len(timestamps) and timestamps[i]:
            date = datetime.fromtimestamp(timestamps[i], tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date = str(i)
        rows.append({
            "date": date,
            "open": float(opens[i] if i < len(opens) else close),
            "high": float(highs[i] if i < len(highs) else close),
            "low": float(lows[i] if i < len(lows) else close),
            "close": float(close),
            "volume": int(volumes[i] if i < len(volumes) else 0),
        })
    return rows


def _validated_ohlcv_rows(rows: list) -> list[dict] | None:
    """Return bounded, finite OHLCV rows or None when any candle is invalid."""
    if not 30 <= len(rows) <= 10_000:
        return None
    clean = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        try:
            open_price = float(row.get("open", row["close"]))
            high = float(row.get("high", row["close"]))
            low = float(row.get("low", row["close"]))
            close = float(row["close"])
            volume = max(0, int(float(row.get("volume", 0))))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        prices = (open_price, high, low, close)
        if (
            not all(np.isfinite(value) and value > 0 for value in prices)
            or low > min(open_price, close)
            or high < max(open_price, close)
            or high < low
        ):
            return None
        clean.append({
            "date": str(row.get("date", index))[:40],
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return clean


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    try:
        limited = _rate_limit("backtest", 10, 60)
        if limited is not None:
            return limited
        payload = request.json or {}
        symbol   = str(payload.get("symbol", "NVDA")).upper().strip()
        strategy = payload.get("strategy", "rsi")
        params   = payload.get("params", {})
        ohlcv    = payload.get("ohlcv")   # sent from frontend
        is_demo = bool(payload.get("is_demo", False))
        data_source = "client"

        if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol):
            return jsonify({"ok": False, "error": "invalid symbol"}), 400
        if strategy not in _bt.STRATEGIES:
            return jsonify({"ok": False, "error": "unknown strategy"}), 400
        if not isinstance(params, dict):
            return jsonify({"ok": False, "error": "params must be an object"}), 400
        if ohlcv is not None and not isinstance(ohlcv, list):
            return jsonify({"ok": False, "error": "ohlcv must be an array"}), 400

        if not ohlcv:
            normalised = _dp.get_ohlcv(symbol, str(payload.get("period", "5y")))
            if not normalised:
                return jsonify({
                    "ok": False,
                    "error": f"無法取得 {symbol} 的真實歷史資料，回測已停止",
                }), 503
            ohlcv = _normalised_ohlcv_rows(normalised)
            is_demo = bool(normalised.get("is_demo", False))
            data_source = normalised.get("source", "unknown")

        ohlcv = _validated_ohlcv_rows(ohlcv)
        if ohlcv is None:
            return jsonify({"ok": False, "error": "OHLCV 格式錯誤或資料量不合理"}), 400

        raw_cost_params = payload.get("cost_params") or {}
        if not isinstance(raw_cost_params, dict):
            return jsonify({"ok": False, "error": "cost_params must be an object"}), 400
        cost_params = dict(raw_cost_params)
        if cost_params and not cost_params.get("symbol"):
            cost_params["symbol"] = symbol

        result = _bt.run(ohlcv, strategy, params, cost_params=cost_params if cost_params.get("enabled") else None)
        return jsonify({
            "ok": True,
            **result,
            "is_demo": is_demo,
            "data_source": data_source,
        })
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
        limited = _rate_limit("walkforward", 4, 60)
        if limited is not None:
            return limited
        payload   = request.json or {}
        strategy  = payload.get("strategy", "decision_core")
        ohlcv     = payload.get("ohlcv")
        params    = payload.get("params", {})
        train_pct = float(payload.get("train_pct", 0.7))
        if strategy not in _bt.STRATEGIES:
            return jsonify({"ok": False, "error": "unknown strategy"}), 400
        if not isinstance(params, dict):
            return jsonify({"ok": False, "error": "params must be an object"}), 400
        if not isinstance(ohlcv, list) or (ohlcv := _validated_ohlcv_rows(ohlcv)) is None:
            return jsonify({"ok": False, "error": "valid ohlcv required"}), 400
        if not 0.5 <= train_pct <= 0.9:
            return jsonify({"ok": False, "error": "train_pct must be between 0.5 and 0.9"}), 400
        result = _bt.walk_forward(ohlcv, strategy, train_pct, params)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Parameter optimization endpoint ──────────────────────────────────────────

@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    try:
        limited = _rate_limit("optimize", 3, 60)
        if limited is not None:
            return limited
        payload  = request.json or {}
        strategy = payload.get("strategy", "decision_core_v2")
        metric   = payload.get("metric", "win_rate")
        ohlcv    = payload.get("ohlcv")
        if strategy not in _bt.STRATEGIES:
            return jsonify({"ok": False, "error": "unknown strategy"}), 400
        if metric not in {
            "win_rate", "total_return", "sharpe", "sortino",
            "calmar", "profit_factor", "expectancy",
        }:
            return jsonify({"ok": False, "error": "unknown metric"}), 400
        if not isinstance(ohlcv, list) or (ohlcv := _validated_ohlcv_rows(ohlcv)) is None:
            return jsonify({"ok": False, "error": "valid ohlcv required"}), 400
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
        limited = _rate_limit("trade_execute", 10, 60)
        if limited is not None:
            return limited
        payload = request.json or {}
        symbol  = str(payload.get("symbol", "")).upper().strip()
        try:
            entry   = float(payload.get("entry", 0))
            stop    = float(payload.get("stop",  0))
            target  = float(payload.get("target", 0))
        except (TypeError, ValueError, OverflowError):
            return jsonify({"ok": False, "error": "entry / stop / target 必須是數字"}), 400
        note    = str(payload.get("note", ""))[:500]
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol)
            or not all(np.isfinite(value) for value in (entry, stop, target))
            or entry <= 0
            or stop <= 0
            or stop >= entry
            or (target and target <= entry)
        ):
            return jsonify({
                "ok": False,
                "error": "需要有效的 symbol，且必須符合 stop < entry < target",
            }), 400

        engine = _trader.get_engine()
        confirm_live = (
            payload.get("confirm_live") is True
            and hmac.compare_digest(
                str(payload.get("confirmation", "")),
                "EXECUTE LIVE ORDER",
            )
        )
        if not engine.simulation and not engine.is_paper and not confirm_live:
            return jsonify({
                "ok": False,
                "blocked": True,
                "error": "真實交易需要逐筆明確確認",
            }), 403
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

        result = engine.submit_order(
            symbol,
            shares,
            entry,
            stop,
            target or entry * 1.1,
            note,
            confirm_live=confirm_live,
        )
        status = 200 if result.get("ok") else (403 if result.get("blocked") else 400)
        return jsonify({**result, "risk_details": details}), status
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/close", methods=["POST"])
def api_trade_close():
    try:
        limited = _rate_limit("trade_close", 10, 60)
        if limited is not None:
            return limited
        payload = request.json or {}
        symbol = str(payload.get("symbol", "")).upper().strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
            return jsonify({"ok": False, "error": "valid symbol required"}), 400
        engine = _trader.get_engine()
        confirm_live = (
            payload.get("confirm_live") is True
            and hmac.compare_digest(
                str(payload.get("confirmation", "")),
                "EXECUTE LIVE ORDER",
            )
        )
        result = engine.close_position(symbol, confirm_live=confirm_live)
        status = 200 if result.get("ok") else (403 if result.get("blocked") else 400)
        return jsonify(result), status
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trade/risk-check", methods=["POST"])
def api_risk_check():
    try:
        payload = request.json or {}
        try:
            entry   = float(payload.get("entry", 0))
            stop    = float(payload.get("stop",  0))
            capital = float(payload.get("capital", 0))
        except (TypeError, ValueError, OverflowError):
            return jsonify({"ok": False, "error": "entry / stop / capital 必須是數字"}), 400
        if (
            not all(np.isfinite(v) for v in (entry, stop, capital))
            or entry <= 0
            or stop <= 0
            or stop >= entry
            or capital <= 0
        ):
            return jsonify({"ok": False, "error": "需要符合 0 < stop < entry，且 capital > 0"}), 400
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
        limited = _rate_limit("scheduler_scan", 2, 60)
        if limited is not None:
            return limited
        payload = request.json or {}
        syms    = payload.get("symbols")    # optional custom list
        if syms is not None:
            if not isinstance(syms, list) or len(syms) > 50:
                return jsonify({"ok": False, "error": "symbols must be an array of at most 50"}), 400
            syms = [str(s).upper().strip() for s in syms]
            if any(not re.fullmatch(r"[A-Z0-9.\-]{1,20}", s) for s in syms):
                return jsonify({"ok": False, "error": "invalid symbol"}), 400
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


@app.route("/api/env-check")
def api_env_check():
    """
    Returns which environment variables are set (bool only, no values).
    Also checks OHLCV data source and Railway Volume path.
    Shows presence only, never secret values.
    """
    spy_ohlcv   = _get_ohlcv_norm("SPY")
    spy_source  = (spy_ohlcv or {}).get("source", "none")
    spy_is_demo = (spy_ohlcv or {}).get("is_demo", True)

    db_path = os.path.abspath(_USER_DATA_DB)
    volume_path = os.path.abspath(_RAILWAY_VOLUME) if _RAILWAY_VOLUME else ""
    try:
        db_is_persistent = bool(
            volume_path and os.path.commonpath([db_path, volume_path]) == volume_path
        )
    except ValueError:
        db_is_persistent = False
    db_exists = os.path.isfile(db_path)
    db_size_kb = round(os.path.getsize(db_path) / 1024, 1) if db_exists else 0

    return jsonify({
        "ok": True,
        "environment": os.environ.get("RAILWAY_ENVIRONMENT", "local"),
        "is_production": _IS_PRODUCTION,
        "env_vars": {
            "SECRET_KEY":        bool(os.environ.get("SECRET_KEY")),
            "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "ALPHA_VANTAGE_KEY": bool(os.environ.get("ALPHA_VANTAGE_KEY")),
            "FINNHUB_KEY":       bool(os.environ.get("FINNHUB_KEY")),
            "LINE_CHANNEL_ACCESS_TOKEN": bool(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")),
            "LINE_USER_ID":      bool(os.environ.get("LINE_USER_ID")),
            "SMTP_HOST":         bool(os.environ.get("SMTP_HOST")),
            "SMTP_USER":         bool(os.environ.get("SMTP_USER")),
            "SMTP_PASS":         bool(os.environ.get("SMTP_PASS")),
            "USER_DATA_DB":      bool(os.environ.get("USER_DATA_DB")),
            "SCHEDULER_ENABLE":  os.environ.get("SCHEDULER_ENABLE", "false"),
            "BACKGROUND_WORKERS_ENABLE": os.environ.get("BACKGROUND_WORKERS_ENABLE", "false"),
        },
        "data": {
            "spy_source":  spy_source,
            "spy_is_demo": spy_is_demo,
            "spy_bars":    len((spy_ohlcv or {}).get("closes", [])),
        },
        "db": {
            "path":      db_path,
            "exists":    db_exists,
            "size_kb":   db_size_kb,
            "persistent": db_is_persistent,
            "volume_configured": bool(volume_path),
        },
    })


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
        limited = _rate_limit("analyst_start", 3, 600)
        if limited is not None:
            return limited
        import analyst as _analyst
        payload  = request.json or {}
        report_raw = payload.get("report", "")
        podcast_raw = payload.get("podcast", "")
        if not isinstance(report_raw, str) or not isinstance(podcast_raw, str):
            return jsonify({"ok": False, "error": "report / podcast must be text"}), 400
        report   = report_raw.strip()
        podcast  = podcast_raw.strip()
        model    = payload.get("model", "claude-haiku-4-5-20251001")
        allowed_models = {"claude-haiku-4-5-20251001", "claude-sonnet-4-6"}
        if model not in allowed_models:
            return jsonify({"ok": False, "error": "unsupported model"}), 400
        if not report and not podcast:
            return jsonify({"ok": False, "error": "請提供產業報告或 Podcast 內容"}), 400
        if len(report) + len(podcast) > 40_000:
            return jsonify({"ok": False, "error": "輸入內容過長（合計最多 40000 字元）"}), 400
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
        limited = _rate_limit("analyst_fetch", 2, 300)
        if limited is not None:
            return limited
        import fetcher as _fetcher
        payload = request.json or {}
        topic_raw = payload.get("topic", "半導體 AI 科技")
        if not isinstance(topic_raw, str):
            return jsonify({"ok": False, "error": "topic must be text"}), 400
        topic = topic_raw.strip()[:120] or "半導體 AI 科技"
        result  = _fetcher.fetch_all(topic)
        return jsonify({"ok": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/analyst/stream/<job_id>")
def api_analyst_stream(job_id):
    import analyst as _analyst
    import queue as _queue
    if not re.fullmatch(r"[0-9a-f]{8}", job_id):
        return jsonify({"ok": False, "error": "invalid job id"}), 400
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
        limited = _rate_limit("advanced_optimizer", 2, 60)
        if limited is not None:
            return limited
        import optimizer as _opt
        payload  = request.json or {}
        analysis = payload.get("analysis", "rolling_wf")
        ohlcv    = payload.get("ohlcv", [])
        strategy = payload.get("strategy", "decision_core_v3")
        trades   = payload.get("trades", [])

        if strategy not in _bt.STRATEGIES:
            return jsonify({"ok": False, "error": "unknown strategy"}), 400
        if not isinstance(ohlcv, list) or (ohlcv := _validated_ohlcv_rows(ohlcv)) is None:
            return jsonify({"ok": False, "error": "valid ohlcv required"}), 400
        if analysis not in {"rolling_wf", "monte_carlo", "regime", "stability"}:
            return jsonify({"ok": False, "error": "unknown analysis"}), 400
        if not isinstance(trades, list) or len(trades) > 10_000:
            return jsonify({"ok": False, "error": "invalid trades"}), 400

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
        limited = _rate_limit("translate_news", 8, 60)
        if limited is not None:
            return limited
        import re as _re
        titles_raw = (request.json or {}).get("titles", [])
        if not isinstance(titles_raw, list):
            return jsonify({"ok": False, "error": "titles must be an array"}), 400
        if any(not isinstance(title, str) for title in titles_raw[:20]):
            return jsonify({"ok": False, "error": "titles must contain text only"}), 400
        titles = [title.strip()[:300] for title in titles_raw[:20] if title.strip()]
        if not titles:
            return jsonify({"ok": False, "error": "no titles"}), 400
        if sum(map(len, titles)) > 4_000:
            return jsonify({"ok": False, "error": "titles are too long"}), 400
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
        limited = _rate_limit("batch_backtest", 3, 60)
        if limited is not None:
            return limited
        payload  = request.json or {}
        raw_symbols = payload.get("symbols") or []
        if not isinstance(raw_symbols, list):
            return jsonify({"ok": False, "error": "symbols must be an array"}), 400
        symbols = list(dict.fromkeys(str(s).upper().strip() for s in raw_symbols[:12] if str(s).strip()))
        if not symbols:
            return jsonify({"ok": False, "error": "no symbols provided"}), 400

        results = []

        def _run_one(sym):
            normalised = _dp.get_ohlcv(sym, "5y")
            if not normalised:
                return {"symbol": sym, "error": "無法取得真實歷史資料"}
            ohlcv = _normalised_ohlcv_rows(normalised)
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
                "is_demo":       bool(normalised.get("is_demo", False)),
                "data_source":   normalised.get("source", "unknown"),
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
        limited = _rate_limit("trump_picks", 2, 300)
        if limited is not None:
            return limited
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
        limited = _rate_limit("catalyst", 6, 60)
        if limited is not None:
            return limited
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        symbol = symbol.upper().strip()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", symbol):
            return jsonify({"ok": False, "error": "invalid symbol"}), 400
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
        limited = _rate_limit("deep_news", 6, 60)
        if limited is not None:
            return limited
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        symbol = symbol.upper().strip()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", symbol):
            return jsonify({"ok": False, "error": "invalid symbol"}), 400
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
        limited = _rate_limit("chat", 12, 60)
        if limited is not None:
            return limited
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
        from anthropic import Anthropic
        payload = request.json or {}
        message_raw = payload.get("message") or ""
        context_raw = payload.get("context") or ""
        if not isinstance(message_raw, str) or not isinstance(context_raw, str):
            return jsonify({"ok": False, "error": "message / context must be text"}), 400
        message = message_raw.strip()[:1000]
        context = context_raw.strip()[:200]
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
        limited = _rate_limit("market_daily_report", 2, 300)
        if limited is not None:
            return limited
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
    if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol):
        return jsonify(ok=False, error="invalid symbol"), 400
    data = _load_user_data()
    notes = data.get("stock_notes", {})
    if request.method == "DELETE":
        notes.pop(symbol, None)
    else:
        body = request.get_json(force=True, silent=True) or {}
        note = str(body.get("note") or "").strip()[:500]
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
    symbol  = str(body.get("symbol") or "").upper().strip()[:20]
    try:
        target = float(body.get("target", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="target 必須是數字"), 400
    direction = body.get("direction", "above")  # "above" | "below"
    note    = str(body.get("note") or "").strip()[:100]
    if (
        not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol)
        or not np.isfinite(target)
        or target <= 0
        or direction not in {"above", "below"}
    ):
        return jsonify(ok=False, error="需要 symbol 和 target"), 400
    data = _load_user_data()
    alerts = data.get("price_alerts", [])
    alerts = [a for a in alerts if not (a["symbol"] == symbol and a["direction"] == direction)]
    alerts.append({"symbol": symbol, "target": target, "direction": direction,
                   "note": note, "created": datetime.now().strftime("%Y-%m-%d %H:%M")})
    alerts = alerts[-100:]
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

_BACKGROUND_WORKERS_ENABLED = (
    os.environ.get(
        "BACKGROUND_WORKERS_ENABLE",
        os.environ.get("SCHEDULER_ENABLE", "false"),
    ).lower()
    == "true"
)


def _line_configured() -> bool:
    return bool(
        os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        and os.environ.get("LINE_USER_ID", "").strip()
    )


def _send_line_message(message: str) -> tuple[bool, str]:
    """Push a text message through LINE Messaging API using server secrets."""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    recipient = os.environ.get("LINE_USER_ID", "").strip()
    if not token or not recipient:
        return False, "LINE Messaging API 未設定"
    try:
        response = _req.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "to": recipient,
                "messages": [{"type": "text", "text": str(message)[:5000]}],
            },
            timeout=10,
        )
        if response.status_code == 200:
            return True, ""
        return False, f"LINE HTTP {response.status_code}: {response.text[:100]}"
    except Exception as exc:
        return False, f"LINE error: {str(exc)[:80]}"

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
                    signals = [{"symbol": a["symbol"],
                                "note": f"價格警報：{'高於' if a['direction']=='above' else '低於'} "
                                        f"${a['target']} (現價 ${a['current_price']:.2f})"
                                        f"{' — '+a['note'] if a.get('note') else ''}"}
                               for a in triggered]
                    if email or _line_configured():
                        try:
                            _send_alerts_internal(signals, email)
                        except Exception:
                            pass
                    print(f"[PRICE ALERT] Triggered: {[a['symbol'] for a in triggered]}", flush=True)
        except Exception:
            pass
        _time.sleep(600)  # 10 minutes

def _send_alerts_internal(signals, email):
    """Shared alert sending logic (reused by price alert checker)."""
    msg_lines = ["📊 Scott 價格警報"] + [f"  • {s['symbol']}: {s['note']}" for s in signals]
    msg = "\n".join(msg_lines)
    if _line_configured():
        _send_line_message(msg)
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

_price_alert_thread = None
if _BACKGROUND_WORKERS_ENABLED:
    _price_alert_thread = _threading.Thread(
        target=_run_price_alert_checker,
        daemon=True,
        name="price-alert-checker",
    )
    _price_alert_thread.start()


# ── Decision-alert background scanner ─────────────────────────────────────────

def _send_decision_alert(alert):
    """Dispatch a single decision Alert via LINE / email using stored settings."""
    try:
        # Block in production when underlying data is demo
        ohlcv_chk = _get_ohlcv_norm(getattr(alert, "symbol", "") or "")
        if _IS_PRODUCTION and _is_demo_ohlcv(ohlcv_chk):
            print(f"[DECISION ALERT] Blocked (demo data in production): {getattr(alert,'symbol','?')}", flush=True)
            return

        data  = _load_user_data()
        s     = data.get("alertSettings_v1") or {}
        if isinstance(s, str):
            try: s = json.loads(s)
            except Exception: s = {}
        email_to   = s.get("email", "")
        if _line_configured():
            try:
                msg = _af.format_line(alert)
                ok, error = _send_line_message(msg)
                if not ok:
                    raise RuntimeError(error)
            except Exception as _le:
                print(f"[DECISION ALERT] LINE send failed: {_le}", flush=True)

        if email_to:
            try:
                smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
                smtp_port = int(os.environ.get("SMTP_PORT", 587))
                smtp_user = os.environ.get("SMTP_USER", "")
                smtp_pass = os.environ.get("SMTP_PASS", "")
                if smtp_user and smtp_pass:
                    import smtplib as _smtp, email.mime.text as _emt
                    m = _emt.MIMEText(_af.format_email_body(alert), "plain", "utf-8")
                    m["Subject"] = _af.format_email_subject(alert)
                    m["From"]    = smtp_user
                    m["To"]      = email_to
                    with _smtp.SMTP(smtp_host, smtp_port) as srv:
                        srv.starttls()
                        srv.login(smtp_user, smtp_pass)
                        srv.sendmail(smtp_user, [email_to], m.as_string())
            except Exception as _ee:
                print(f"[DECISION ALERT] email send failed: {_ee}", flush=True)
    except Exception as _de2:
        print(f"[DECISION ALERT] outer error: {_de2}", flush=True)


def _run_decision_alert_scanner():
    """Scan portfolio + watchlist every 10 min and fire decision alerts."""
    _time.sleep(60)   # let app finish booting
    while True:
        try:
            data      = _load_user_data()
            positions = data.get("holdings", [])
            watchlist = data.get("watchlist", [])
            if isinstance(watchlist, str):
                watchlist = [w.strip().upper() for w in watchlist.split(",") if w.strip()]

            if positions or watchlist:
                _ascn.run_full_scan(
                    positions=positions,
                    watchlist=watchlist,
                    ohlcv_fn=_get_ohlcv_norm,
                    send_fn=_send_decision_alert,
                )
        except Exception as _exc:
            print(f"[DECISION ALERT SCANNER] {_exc}", flush=True)
        _time.sleep(600)   # 10 minutes


_decision_alert_thread = None
if _BACKGROUND_WORKERS_ENABLED:
    _decision_alert_thread = _threading.Thread(
        target=_run_decision_alert_scanner,
        daemon=True,
        name="decision-alert-scanner",
    )
    _decision_alert_thread.start()


# ── Alerts endpoint ────────────────────────────────────────────────────────────

@app.route("/api/alerts/send", methods=["POST"])
def api_alerts_send():
    """Send alerts via email and/or server-configured LINE Messaging API."""
    try:
        limited = _rate_limit("alerts_send", 10, 60)
        if limited is not None:
            return limited
        payload     = request.json or {}
        signals     = payload.get("signals", [])
        alert_type  = payload.get("type", "both")
        email_to    = str(payload.get("email") or os.environ.get("ALERT_EMAIL_TO", ""))[:254]
        sent = []
        errors = []

        if alert_type not in {"line", "email", "both"}:
            return jsonify({"ok": False, "error": "invalid alert type"}), 400
        if not isinstance(signals, list) or not signals:
            return jsonify({"ok": False, "error": "signals must be a non-empty array"}), 400

        msg_lines = ["📈 Scott 股票訊號提醒"]
        for s in signals[:10]:
            if not isinstance(s, dict):
                continue
            sym  = str(s.get("symbol", ""))[:20]
            note = str(s.get("note", ""))[:500]
            msg_lines.append(f"• {sym}: {note}" if note else f"• {sym}")
        plain_text = "\n".join(msg_lines)

        # ── LINE Messaging API ────────────────────────────────────────────────
        if alert_type in ("line", "both"):
            ok, error = _send_line_message(plain_text)
            if ok:
                sent.append("line")
            elif error:
                errors.append(error)

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
                    f"<tr><td style='padding:6px 10px;border-bottom:1px solid #333;font-weight:700;color:#58a6ff'>{_html.escape(str(s.get('symbol',''))[:20])}</td>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #333;color:#e6edf3'>{_html.escape(str(s.get('note',''))[:500])}</td></tr>"
                    for s in signals[:10] if isinstance(s, dict)
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
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "JSON object required"}), 400
        enabled = data.get("enabled", _alert_schedule_settings["enabled"])
        alert_time = data.get("time", _alert_schedule_settings["time"])
        timezone_name = data.get("timezone", _alert_schedule_settings["timezone"])
        if not isinstance(enabled, bool) or not _valid_hhmm(alert_time) or not _valid_timezone_name(timezone_name):
            return jsonify({"ok": False, "error": "invalid schedule settings"}), 400
        _alert_schedule_settings.update({
            "enabled": enabled,
            "time": alert_time,
            "timezone": timezone_name,
        })
        _save_user_data({"alert_schedule_settings": _alert_schedule_settings})
    return jsonify({"ok": True, "settings": _alert_schedule_settings})


# ── Decision Engine API routes ────────────────────────────────────────────────
import decision_engine as _de
import sector_map      as _smap
import alert_history   as _ah
import alert_scanner   as _ascn
import alert_formatter as _af
import rotation_engine as _re

# Ensure DB table exists
_ah.init_db()


def _get_ohlcv_norm(symbol: str):
    """
    Fetch OHLCV for any symbol.
    Priority: data_provider (yfinance → Yahoo API → AV → Finnhub → TWSE → demo).
    Returns normalised dict; is_demo=True when using synthetic data.
    """
    return _dp.get_ohlcv(symbol.upper())


@app.route("/api/market-state")
def api_market_state():
    """Real-time market state via SPY + QQQ. Includes is_demo flag."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        state = _dp.market_state(_get_ohlcv_norm)
        return jsonify({"ok": True, **state})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ohlcv/<symbol>")
def api_ohlcv(symbol):
    """Return normalised OHLCV for a symbol (with is_demo + source fields)."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        sym = symbol.upper().strip()
        data = _get_ohlcv_norm(sym)
        if not data:
            return jsonify({"ok": False, "error": f"無法取得 {sym} 的 K 線資料"}), 404
        n = len(data.get("closes", []))
        last = data["closes"][-1] if data.get("closes") else None
        return jsonify({
            "ok":      True,
            "symbol":  sym,
            "bars":    n,
            "last_close": last,
            "is_demo": data.get("is_demo", False),
            "source":  data.get("source", "unknown"),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


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

        # Portfolio-level summary — derive aggregate level from avg score
        valid = [r for r in results if r.get("ok")]
        avg_score = round(sum(r["score"] for r in valid) / len(valid)) if valid else 0

        if   avg_score >= 75: p_level = "ADD";       p_label = "加碼機會";   p_color = "#3fb950"
        elif avg_score >= 55: p_level = "HOLD";      p_label = "繼續持有";   p_color = "#58a6ff"
        elif avg_score >= 40: p_level = "WATCH";     p_label = "留意觀察";   p_color = "#e3b341"
        elif avg_score >= 25: p_level = "TRIM";      p_label = "考慮減倉";   p_color = "#f0883e"
        elif avg_score >= 10: p_level = "ROTATE";    p_label = "換股考量";   p_color = "#bc8cff"
        else:                 p_level = "STOP_LOSS"; p_label = "停損出場";   p_color = "#f85149"

        # Aggregate reasons from worst holdings first
        worst = sorted(valid, key=lambda r: r["score"])[:3]
        agg_reasons = [f"{r['symbol']}: {r['reasons'][0]}" for r in worst if r.get("reasons")]
        if not agg_reasons:
            agg_reasons = [f"投資組合平均效率分數 {avg_score}"]

        _PORT_ACTIONS = {
            "ADD":       "整體動能強勁，可考慮在強勢持倉加碼",
            "HOLD":      "整體持倉效率良好，繼續持有",
            "WATCH":     "部分持倉需注意，建議設定停損線",
            "TRIM":      "整體效率偏低，考慮對弱勢持倉分批減倉",
            "ROTATE":    "多個持倉效率不足，建議逐步換股至強勢標的",
            "STOP_LOSS": "整體持倉表現差，建議積極減倉或停損",
        }

        # Collect all warning flags across holdings
        all_flags = []
        for r in valid:
            all_flags.extend(r.get("warning_flags", []))
        unique_flags = list(dict.fromkeys(all_flags))  # deduplicate, preserve order

        return jsonify({
            "ok":             True,
            "engine":         "capital_efficiency",
            "score":          avg_score,
            "level":          p_level,
            "level_label":    p_label,
            "level_color":    p_color,
            "reasons":        agg_reasons,
            "suggested_action": _PORT_ACTIONS.get(p_level, ""),
            "warning_flags":  unique_flags,
            "detail": {
                "holding_count": len(valid),
                "benchmark":     bench_sym,
                "benchmark_return_pct": round(bench_return, 2) if bench_return is not None else None,
            },
            "holdings":             results,
            "portfolio_avg_score":  avg_score,
            "benchmark":            bench_sym,
            "benchmark_return_pct": round(bench_return, 2) if bench_return is not None else None,
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


# ── Decision Alerts API ───────────────────────────────────────────────────────

@app.route("/api/decision-alerts")
def api_decision_alerts():
    """Return recent decision alerts (last 24 h by default)."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        hours = int(request.args.get("hours", 24))
        rows  = _ah.get_recent(hours=hours)
        stats = _ah.get_stats()
        return jsonify({"ok": True, "alerts": rows, "stats": stats, "count": len(rows)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/decision-alerts/scan", methods=["POST"])
def api_decision_alerts_scan():
    """
    Manually trigger a decision-alert scan.
    Body (optional): {symbol?, positions?, watchlist?}
    If symbol is provided, scan that single symbol.
    Otherwise fall back to stored portfolio + watchlist.
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("stress_test", 4, 60)
        if limited is not None:
            return limited
        body     = request.json or {}
        symbol   = str(body.get("symbol", "") or "").upper().strip()
        pos_list = body.get("positions") or []
        wl_list  = body.get("watchlist") or []

        if symbol:
            # Single-symbol quick scan
            ohlcv = _get_ohlcv_norm(symbol)
            if not ohlcv:
                return jsonify({"ok": False, "error": f"無法取得 {symbol} 的 K 線資料"}), 404
            alerts = _ascn.scan_symbol(symbol, _get_ohlcv_norm)
            fired  = []
            for a in alerts:
                _ah.record(a)
                fired.append(_af.format_app(a))
            # Phase 11: auto-record S/A alerts
            import threading as _thr
            for fa in fired:
                _thr.Thread(
                    target=_obe.record_signal,
                    kwargs=dict(
                        symbol=symbol,
                        signal_type=str(fa.get("signal_type", "") or fa.get("type", "")),
                        signal_class=str(fa.get("signal_class", "") or fa.get("level", "")),
                        signal=str(fa.get("signal", "")),
                        score=float(fa.get("score", 0) or 0),
                        price_at_signal=float(fa.get("price", 0) or 0),
                        market_state="",
                        is_demo=_is_demo_ohlcv(ohlcv),
                    ),
                    daemon=True,
                ).start()
            return jsonify({"ok": True, "alerts": fired, "count": len(fired)})

        # Full scan — use provided lists or fall back to stored data
        if not pos_list and not wl_list:
            data     = _load_user_data()
            pos_list = data.get("holdings", [])
            wl_raw   = data.get("watchlist", [])
            if isinstance(wl_raw, str):
                wl_list = [w.strip().upper() for w in wl_raw.split(",") if w.strip()]
            else:
                wl_list = wl_raw or []

        fired_alerts = _ascn.run_full_scan(
            positions=pos_list,
            watchlist=wl_list,
            ohlcv_fn=_get_ohlcv_norm,
            send_fn=_send_decision_alert,
        )
        fired_formatted = [_af.format_app(a) for a in fired_alerts]
        # Phase 11: auto-record fired alerts in background
        import threading as _thr2
        for fa in fired_formatted:
            sym_fa = str(fa.get("symbol", "") or "").upper()
            if not sym_fa:
                continue
            ohlcv_fa = _get_ohlcv_norm(sym_fa)
            _thr2.Thread(
                target=_obe.record_signal,
                kwargs=dict(
                    symbol=sym_fa,
                    signal_type=str(fa.get("signal_type", "") or fa.get("type", "")),
                    signal_class=str(fa.get("signal_class", "") or fa.get("level", "")),
                    signal=str(fa.get("signal", "")),
                    score=float(fa.get("score", 0) or 0),
                    price_at_signal=float(fa.get("price", 0) or 0),
                    market_state="",
                    is_demo=_is_demo_ohlcv(ohlcv_fa),
                ),
                daemon=True,
            ).start()
        return jsonify({
            "ok":     True,
            "alerts": fired_formatted,
            "count":  len(fired_formatted),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/decision-alerts/resolve/<alert_id>", methods=["POST"])
def api_decision_alerts_resolve(alert_id: str):
    """Mark a decision alert as resolved."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        _ah.resolve(alert_id)
        return jsonify({"ok": True, "resolved": alert_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Portfolio Rotation API ────────────────────────────────────────────────────

@app.route("/api/portfolio-rotation", methods=["POST"])
def api_portfolio_rotation():
    """
    Full portfolio rotation analysis.
    Body: {
      holdings?  / positions?  : list of position dicts,
      benchmark? : "QQQ"|"SPY"|"0050.TW"  (auto-fetches return),
      bench_return?: float      (pre-computed, overrides benchmark fetch),
      watchlist?: list[str],
    }
    Falls back to stored data when body fields are absent.
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body     = request.json or {}
        # Accept both 'holdings' and 'positions' as input key
        pos_list = body.get("holdings") or body.get("positions") or []
        wl_raw   = body.get("watchlist") or []
        bench_sym = str(body.get("benchmark", "") or "").upper().strip()

        # bench_return: prefer explicit float, else fetch from benchmark symbol
        bench_ret: float | None = None
        if body.get("bench_return") is not None:
            try:
                bench_ret = float(body["bench_return"])
            except (ValueError, TypeError):
                pass
        elif bench_sym:
            try:
                bohlcv = _get_ohlcv_norm(bench_sym)
                if bohlcv and bohlcv.get("closes") and len(bohlcv["closes"]) >= 22:
                    bc = bohlcv["closes"]
                    bench_ret = round((bc[-1] - bc[-22]) / bc[-22] * 100, 2)
            except Exception:
                pass

        # Fall back to stored data
        if not pos_list:
            data     = _load_user_data()
            pos_list = data.get("holdings", [])
            if not wl_raw:
                wl_stored = data.get("watchlist", [])
                if isinstance(wl_stored, str):
                    wl_raw = [w.strip().upper() for w in wl_stored.split(",") if w.strip()]
                else:
                    wl_raw = wl_stored or []

        if not pos_list:
            return jsonify({"ok": False, "error": "尚未建立持倉資料"}), 400

        result = _re.analyze_portfolio(
            positions=pos_list,
            ohlcv_fn=_get_ohlcv_norm,
            bench_return=bench_ret,
            watchlist=wl_raw,
        )

        # Phase 4 alert integration
        _rotation_fire_alerts(result.get("positions", []))

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


def _rotation_fire_alerts(pos_results: list[dict]):
    """
    Fire Phase-4 alerts for rotation-derived conditions:
      - drag_score >= 75
      - rotation_action in (ROTATE_FULL, STOP_LOSS)
      - capital_efficiency_score < 35
      - holding_days >= 10 AND relative_to_benchmark <= -5 (持續跑輸基準)
    """
    import alert_engine as _ae
    for pos in pos_results:
        if not pos.get("ok"):
            continue
        symbol       = pos.get("symbol", "")
        drag_score   = pos.get("drag_score", 0)
        action       = pos.get("rotation_action", "")
        ce_score     = pos.get("capital_efficiency_score") or 50
        rel_bench    = pos.get("relative_to_benchmark")    # None if no benchmark
        holding_days = pos.get("holding_days", 0)
        ce_result    = pos.get("ce_detail", {})

        should_alert = (
            drag_score >= 75
            or action in ("ROTATE_FULL", "STOP_LOSS")
            or ce_score < 35
            or (holding_days >= 10 and rel_bench is not None and rel_bench <= -5)
        )
        if not should_alert:
            continue

        try:
            a = _ae.evaluate_capital_efficiency(symbol, ce_result)
            if a and _ah.should_send(symbol, "CAPITAL_EFF", a.level):
                _ah.record(a)
                try:
                    _send_decision_alert(a)
                except Exception:
                    pass
        except Exception:
            pass


# ── Daily Report API — Phase 8 ────────────────────────────────────────────────
import daily_report_engine as _dre

_dre.init_db()

# In-memory settings (persisted via stored user data key "daily_report_settings")
_daily_report_settings: dict = {
    "enabled":        False,
    "timezone":       "America/New_York",
    "send_email":     True,
    "send_line":      True,
    "only_sa_alerts": False,
    "schedules": {
        "pre_market":  "08:30",
        "intraday":    "11:30",
        "pre_close":   "15:50",
        "post_market": "16:30",
    },
    "tw_schedules": {
        "pre_market":  "08:30",
        "intraday":    "10:30",
        "pre_close":   "13:20",
        "post_market": "14:30",
    },
}

_REPORT_TYPES = {"pre_market", "intraday", "post_market"}
_REPORT_BOOLEAN_SETTINGS = {"enabled", "send_email", "send_line", "only_sa_alerts"}


def _normalise_schedule_map(value, current: dict) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = dict(current)
    for key, fire_time in value.items():
        if key not in current or not _valid_hhmm(fire_time):
            return None
        result[key] = fire_time
    return result


_stored_daily_settings = _load_user_data().get("daily_report_settings", {})
if isinstance(_stored_daily_settings, dict):
    for _key in _REPORT_BOOLEAN_SETTINGS:
        if isinstance(_stored_daily_settings.get(_key), bool):
            _daily_report_settings[_key] = _stored_daily_settings[_key]
    if _valid_timezone_name(_stored_daily_settings.get("timezone")):
        _daily_report_settings["timezone"] = _stored_daily_settings["timezone"]
    for _key in ("schedules", "tw_schedules"):
        _schedule = _normalise_schedule_map(
            _stored_daily_settings.get(_key),
            _daily_report_settings[_key],
        )
        if _schedule is not None:
            _daily_report_settings[_key] = _schedule


def _make_ai_fn():
    """Return a callable that calls Claude API, or None if API key not set."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=key)
        def _call(prompt: str) -> str | None:
            try:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=800,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text if msg.content else None
            except Exception:
                return None
        return _call
    except Exception:
        return None


def _run_daily_report(report_type: str, *, dispatch: bool = False) -> dict:
    """Generate a report and optionally dispatch it."""
    data     = _load_user_data()
    pos_list = data.get("holdings", [])
    wl_raw   = data.get("watchlist", [])
    if isinstance(wl_raw, str):
        wl_raw = [w.strip().upper() for w in wl_raw.split(",") if w.strip()]

    report = _dre.generate_report(
        report_type=report_type,
        ohlcv_fn=_get_ohlcv_norm,
        positions=pos_list,
        watchlist=wl_raw,
        bench_sym="QQQ",
        ai_fn=_make_ai_fn(),
    )

    # Persist to DB
    try:
        _dre.save_report(report)
    except Exception:
        pass

    # Dispatch if settings allow
    s = _daily_report_settings
    should_dispatch = dispatch and (
        not s.get("only_sa_alerts")
        or report.get("alerts_summary", {}).get("sa_count", 0) > 0
    )
    if should_dispatch:
        if s.get("send_line"):
            _dispatch_report_line(report)
        if s.get("send_email"):
            _dispatch_report_email(report)

    return report


def _dispatch_report_line(report: dict):
    try:
        # Block in production when using demo data
        if _IS_PRODUCTION and report.get("is_demo"):
            print("[REPORT] LINE blocked: demo data in production", flush=True)
            return False
        msg = _dre.format_line(report)
        ok, error = _send_line_message(msg)
        if not ok and error:
            print(f"[REPORT] LINE send failed: {error}", flush=True)
        return ok
    except Exception as e:
        print(f"[REPORT] LINE send failed: {e}", flush=True)
        return False


def _dispatch_report_email(report: dict):
    try:
        # Block in production when using demo data
        if _IS_PRODUCTION and report.get("is_demo"):
            print("[REPORT] Email blocked: demo data in production", flush=True)
            return False
        settings  = _load_user_data().get("settings", {})
        email_to  = settings.get("email", "") or os.environ.get("SMTP_USER", "")
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        if not (smtp_user and smtp_pass and email_to):
            return False
        import email.mime.multipart as _mmp
        subject = _dre.format_email_subject(report)
        body    = _dre.format_email_body(report)
        m = _mmp.MIMEMultipart("alternative")
        m["Subject"] = subject
        m["From"]    = smtp_user
        m["To"]      = email_to
        import email.mime.text as _emt
        m.attach(_emt.MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
            srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_user, [email_to], m.as_string())
        return True
    except Exception as e:
        print(f"[REPORT] Email send failed: {e}", flush=True)
        return False


def _start_daily_report_scheduler():
    """Background thread: fires scheduled reports based on wall-clock time."""
    import threading
    from datetime import datetime, timezone
    _fired_today: dict[str, str] = {}   # report_type -> date string

    def _loop():
        while True:
            try:
                if not _daily_report_settings.get("enabled"):
                    _time.sleep(60)
                    continue
                tz_name = _daily_report_settings.get("timezone", "America/New_York")
                try:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo(tz_name)
                except Exception:
                    tz = timezone.utc
                now      = datetime.now(tz)
                hm       = now.strftime("%H:%M")
                date_str = now.strftime("%Y-%m-%d")
                # pick schedule set
                is_tw = "Taipei" in tz_name or "Asia" in tz_name
                sched = _daily_report_settings.get("tw_schedules" if is_tw else "schedules", {})
                for rtype, fire_time in sched.items():
                    actual_type = rtype if rtype != "pre_close" else "intraday"
                    key = f"{actual_type}:{date_str}:{fire_time}"
                    if hm == fire_time and key not in _fired_today:
                        _fired_today[key] = hm
                        try:
                            _run_daily_report(actual_type, dispatch=True)
                            print(f"[REPORT SCHEDULER] fired {actual_type} at {hm}", flush=True)
                        except Exception as exc:
                            print(f"[REPORT SCHEDULER] {actual_type} failed: {exc}", flush=True)
                # cleanup old keys (keep last 100)
                if len(_fired_today) > 100:
                    old_keys = list(_fired_today.keys())[:-50]
                    for k in old_keys:
                        _fired_today.pop(k, None)
            except Exception:
                pass
            _time.sleep(30)

    t = threading.Thread(target=_loop, daemon=True, name="daily-report-scheduler")
    t.start()
    return t


_daily_report_thread = (
    _start_daily_report_scheduler() if _BACKGROUND_WORKERS_ENABLED else None
)


@app.route("/api/daily-report/latest")
def api_daily_report_latest():
    """Return the latest report (optionally filtered by type)."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        rtype = request.args.get("type") or None
        if rtype is not None and rtype not in _REPORT_TYPES:
            return jsonify({"ok": False, "error": "invalid report type"}), 400
        report = _dre.get_latest(report_type=rtype)
        if not report:
            return jsonify({"ok": False, "error": "尚無報告，請先產生"}), 404
        return jsonify({"ok": True, "report": report})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/daily-report/generate", methods=["POST"])
def api_daily_report_generate():
    """Manually generate a report. Body: {type: 'pre_market'|'intraday'|'post_market'}"""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("daily_report_generate", 2, 300)
        if limited is not None:
            return limited
        body        = request.json or {}
        report_type = str(body.get("type", "pre_market"))
        if report_type not in _REPORT_TYPES:
            return jsonify({"ok": False, "error": "type 必須為 pre_market / intraday / post_market"}), 400
        report = _run_daily_report(report_type)
        return jsonify({"ok": True, "report": report})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/daily-report/send", methods=["POST"])
def api_daily_report_send():
    """Send latest (or specified) report via LINE + Email. Body: {type?, channels?:[line,email]}"""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("daily_report_send", 5, 60)
        if limited is not None:
            return limited
        body        = request.json or {}
        report_type = str(body.get("type", "pre_market"))
        channels    = body.get("channels") or ["line", "email"]
        if report_type not in _REPORT_TYPES:
            return jsonify({"ok": False, "error": "invalid report type"}), 400
        if (
            not isinstance(channels, list)
            or not channels
            or len(channels) > 2
            or any(channel not in {"line", "email"} for channel in channels)
        ):
            return jsonify({"ok": False, "error": "channels must contain line and/or email"}), 400
        report = _dre.get_latest(report_type=report_type)
        if not report:
            return jsonify({"ok": False, "error": "找不到報告，請先產生"}), 404
        sent = []
        if "line" in channels:
            if _dispatch_report_line(report):
                sent.append("LINE")
        if "email" in channels:
            if _dispatch_report_email(report):
                sent.append("Email")
        return jsonify({"ok": bool(sent), "sent": sent})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/daily-report/history")
def api_daily_report_history():
    """Return last N reports. ?limit=30"""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limit = min(100, max(1, int(request.args.get("limit", 30))))
        rows  = _dre.get_history(limit=limit)
        return jsonify({"ok": True, "reports": rows, "count": len(rows)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/daily-report/schedule", methods=["POST"])
def api_daily_report_schedule():
    """Update daily report schedule settings."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("daily_report_schedule", 10, 60)
        if limited is not None:
            return limited
        body = request.json or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "JSON object required"}), 400
        allowed = _REPORT_BOOLEAN_SETTINGS | {"timezone", "schedules", "tw_schedules"}
        if any(key not in allowed for key in body):
            return jsonify({"ok": False, "error": "unknown schedule setting"}), 400
        updated = dict(_daily_report_settings)
        for key in _REPORT_BOOLEAN_SETTINGS:
            if key in body:
                if not isinstance(body[key], bool):
                    return jsonify({"ok": False, "error": f"{key} must be boolean"}), 400
                updated[key] = body[key]
        if "timezone" in body:
            if not _valid_timezone_name(body["timezone"]):
                return jsonify({"ok": False, "error": "invalid timezone"}), 400
            updated["timezone"] = body["timezone"]
        for key in ("schedules", "tw_schedules"):
            if key in body:
                schedule = _normalise_schedule_map(body[key], _daily_report_settings[key])
                if schedule is None:
                    return jsonify({"ok": False, "error": f"invalid {key}"}), 400
                updated[key] = schedule
        _daily_report_settings.update(updated)
        _save_user_data({"daily_report_settings": _daily_report_settings})
        return jsonify({"ok": True, "settings": _daily_report_settings})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Stress Test API — Phase 9 ─────────────────────────────────────────────────
import stress_test_engine as _ste

_ste.init_db()


@app.route("/api/stress-test/run", methods=["POST"])
def api_stress_test_run():
    """
    Run a portfolio stress test.
    Body: {portfolio?, scenario, shock_pct?, benchmark?, include_watchlist?}
    Falls back to stored holdings when portfolio is absent.
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body     = request.json or {}
        portfolio = body.get("portfolio") or body.get("holdings") or []
        scenario  = str(body.get("scenario", "market_crash"))
        shock_pct = body.get("shock_pct")
        benchmark = str(body.get("benchmark", "QQQ") or "QQQ").upper()
        include_wl = body.get("include_watchlist", True)

        if scenario not in _ste.SCENARIOS:
            return jsonify({"ok": False, "error": "unknown scenario"}), 400
        if not isinstance(include_wl, bool):
            return jsonify({"ok": False, "error": "include_watchlist must be boolean"}), 400
        if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", benchmark):
            return jsonify({"ok": False, "error": "invalid benchmark"}), 400
        if shock_pct is not None:
            try:
                shock_pct = float(shock_pct)
            except (ValueError, TypeError, OverflowError):
                return jsonify({"ok": False, "error": "shock_pct must be numeric"}), 400
            if not np.isfinite(shock_pct) or not -100 <= shock_pct <= 100:
                return jsonify({"ok": False, "error": "shock_pct must be between -100 and 100"}), 400

        # Fallback to stored data
        if not portfolio:
            data     = _load_user_data()
            portfolio = data.get("holdings", [])

        if not portfolio:
            return jsonify({"ok": False, "error": "尚未建立持倉資料"}), 400
        portfolio = _normalise_holdings(portfolio)
        if portfolio is None:
            return jsonify({"ok": False, "error": "invalid portfolio"}), 400

        watchlist = []
        if include_wl:
            data     = _load_user_data()
            wl_raw   = data.get("watchlist", [])
            if isinstance(wl_raw, str):
                watchlist = [w.strip().upper() for w in wl_raw.split(",") if w.strip()]
            elif isinstance(wl_raw, list):
                watchlist = wl_raw or []
            else:
                watchlist = []
            watchlist = _normalise_symbols(watchlist[:50]) or []

        result = _ste.generate_stress_test(
            portfolio=portfolio,
            scenario=scenario,
            shock_pct=shock_pct,
            benchmark=benchmark,
            ohlcv_fn=_get_ohlcv_norm,
            watchlist=watchlist,
        )

        # Integrate with Phase 8: flag daily report if extreme stress
        _stress_flag_daily_report(result)

        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stress-test/latest")
def api_stress_test_latest():
    auth = _require_auth()
    if auth:
        return auth
    try:
        result = _ste.get_latest()
        if not result:
            return jsonify({"ok": False, "error": "尚無壓力測試結果"}), 404
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stress-test/history")
def api_stress_test_history():
    auth = _require_auth()
    if auth:
        return auth
    try:
        limit = min(100, max(1, int(request.args.get("limit", 20))))
        rows  = _ste.get_history(limit=limit)
        return jsonify({"ok": True, "results": rows, "count": len(rows)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stress-test/scenario", methods=["POST"])
def api_stress_test_scenario():
    """Preview scenario parameters without running full test."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        body     = request.json or {}
        scenario = str(body.get("scenario", "market_crash"))
        if scenario not in _ste.SCENARIOS:
            return jsonify({"ok": False, "error": "unknown scenario"}), 400
        return jsonify({
            "ok":      True,
            "scenario": scenario,
            "label":   _ste.SCENARIO_LABELS.get(scenario, scenario),
            "scenarios": [
                {"id": s, "label": _ste.SCENARIO_LABELS.get(s, s)}
                for s in _ste.SCENARIOS
            ],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _stress_flag_daily_report(result: dict):
    """
    If stress test reveals extreme risk, auto-generate an intraday daily report
    so it appears in the Phase 8 report dashboard.
    """
    score = result.get("portfolio_stress_score", 0)
    rs    = result.get("risk_summary", {})
    should_flag = (
        score > 80
        or rs.get("stop_loss_triggered")
        or rs.get("kill_signals_triggered")
        or rs.get("drag_critical")
    )
    if not should_flag:
        return
    try:
        import threading
        def _bg():
            try:
                _run_daily_report("intraday")
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()
    except Exception:
        pass


# ── Capital Efficiency Filter — Phase 12 ──────────────────────────────────────
import capital_filter_engine as _cfe


@app.route("/api/capital-filter", methods=["POST"])
def api_capital_filter():
    """
    Capital Efficiency Filter: rank a watchlist for capital deployment priority.
    Body: {
      symbols : list[str],   required
      sort_by ?: 'score' | 'gain5d' | 'vol_change' | 'breakout_dist' | 'rr_ratio'
    }
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("capital_filter", 4, 60)
        if limited is not None:
            return limited
        body    = request.json or {}
        syms    = body.get("symbols") or []
        sort_by = str(body.get("sort_by", "score") or "score")
        if not syms:
            # Fall back to stored watchlist
            ud  = _load_user_data()
            raw = ud.get("watchlist", [])
            if isinstance(raw, str):
                syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
            else:
                syms = [str(s).upper().strip() for s in (raw or []) if s]
        if not syms:
            return jsonify({"ok": False, "error": "請提供 symbols 或先設定自選清單"}), 400
        syms = _normalise_symbols(syms, max_items=30)
        if not syms:
            return jsonify({"ok": False, "error": "symbols must contain at most 30 valid symbols"}), 400
        if sort_by not in {"score", "gain5d", "vol_change", "breakout_dist", "rr_ratio"}:
            return jsonify({"ok": False, "error": "invalid sort_by"}), 400

        result = _cfe.run_capital_filter(syms, _get_ohlcv_norm, sort_by=sort_by)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Live Observation Period — Phase 11 ────────────────────────────────────────
import observation_engine as _obe

_obe.init_db(_USER_DATA_DB)


@app.route("/api/obs/record", methods=["POST"])
def api_obs_record():
    """Manually record a signal observation."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("observation_record", 30, 60)
        if limited is not None:
            return limited
        body = request.json or {}
        symbol = str(body.get("symbol", "") or "").upper().strip()
        signal_type = str(body.get("signal_type", "") or "").strip()[:40]
        signal_class = str(body.get("signal_class", "") or "").strip()[:40]
        signal = str(body.get("signal", "") or "").strip()[:120]
        market_state = str(body.get("market_state", "") or "").strip()[:40]
        try:
            score = float(body.get("score", 0) or 0)
            price_at_signal = float(body.get("price_at_signal", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return jsonify({"ok": False, "error": "score / price_at_signal must be numeric"}), 400
        if (
            not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol)
            or not signal_type
            or not np.isfinite(score)
            or not 0 <= score <= 100
            or not np.isfinite(price_at_signal)
            or price_at_signal < 0
        ):
            return jsonify({"ok": False, "error": "invalid observation"}), 400
        obs_id = _obe.record_signal(
            symbol=symbol,
            signal_type=signal_type,
            signal_class=signal_class,
            signal=signal,
            score=score,
            price_at_signal=price_at_signal,
            market_state=market_state,
            is_demo=bool(body.get("is_demo", False)),
        )
        return jsonify({"ok": True, "obs_id": obs_id,
                        "disclaimer": "目前為實盤觀察期，不代表自動下單。"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/obs/stats", methods=["GET"])
def api_obs_stats():
    try:
        days = min(365, max(1, int(request.args.get("days", 14))))
        return jsonify({"ok": True, **_obe.get_signal_stats(days)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/obs/daily-log", methods=["GET", "POST"])
def api_obs_daily_log():
    if request.method == "POST":
        auth = _require_auth()
        if auth:
            return auth
        try:
            limited = _rate_limit("observation_daily_log", 20, 60)
            if limited is not None:
                return limited
            body = request.json or {}
            log_date = body.get("date")
            if log_date:
                try:
                    datetime.strptime(str(log_date), "%Y-%m-%d")
                except ValueError:
                    return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400
            list_fields = {}
            for key in (
                "sector_leaders", "top_picks", "kill_signals", "sell_signals",
                "high_chase_risk", "rotation_recs",
            ):
                values = _normalise_text_list(body.get(key) or [], max_items=50, max_length=80)
                if values is None:
                    return jsonify({"ok": False, "error": f"invalid {key}"}), 400
                list_fields[key] = values
            try:
                alert_s_count = min(10_000, max(0, int(body.get("alert_s_count", 0) or 0)))
                alert_a_count = min(10_000, max(0, int(body.get("alert_a_count", 0) or 0)))
            except (TypeError, ValueError, OverflowError):
                return jsonify({"ok": False, "error": "alert counts must be integers"}), 400
            log = _obe.create_daily_log(
                log_date=str(log_date) if log_date else None,
                market_state=str(body.get("market_state", "") or "").strip()[:40],
                sector_leaders=list_fields["sector_leaders"],
                top_picks=list_fields["top_picks"],
                kill_signals=list_fields["kill_signals"],
                sell_signals=list_fields["sell_signals"],
                high_chase_risk=list_fields["high_chase_risk"],
                rotation_recs=list_fields["rotation_recs"],
                alert_s_count=alert_s_count,
                alert_a_count=alert_a_count,
                notes=str(body.get("notes", "") or "").strip()[:500],
            )
            return jsonify({"ok": True, "log": log,
                            "disclaimer": "目前為實盤觀察期，不代表自動下單。"})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500
    else:
        try:
            days = min(365, max(1, int(request.args.get("days", 14))))
            log_date = request.args.get("date")
            if log_date:
                try:
                    datetime.strptime(log_date, "%Y-%m-%d")
                except ValueError:
                    return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400
            result = _obe.get_daily_log(days=days, log_date=log_date)
            return jsonify({"ok": True, "logs": result if isinstance(result, list) else [result],
                            "disclaimer": "目前為實盤觀察期，不代表自動下單。"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/obs/report", methods=["GET"])
def api_obs_report():
    try:
        return jsonify({"ok": True, **_obe.get_accuracy_report()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/obs/update-outcomes", methods=["POST"])
def api_obs_update_outcomes():
    auth = _require_auth()
    if auth:
        return auth
    try:
        limited = _rate_limit("observation_update", 1, 300)
        if limited is not None:
            return limited
        result = _obe.update_outcomes(ohlcv_fn=_get_ohlcv_norm)
        return jsonify({"ok": True, **result,
                        "disclaimer": "目前為實盤觀察期，不代表自動下單。"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


def _obs_auto_record_from_result(sym: str, result: dict, market_st: str = "") -> None:
    """Background: auto-record a notable analyze result as a signal observation."""
    try:
        sig_type = str(result.get("signal", "") or result.get("recommendation", "") or "")
        if not sig_type:
            return
        score = float(result.get("score", 0) or 0)
        if score < 50 and sig_type not in ("BUY", "STRONG_BUY", "SELL", "TRIM"):
            return
        ohlcv = _get_ohlcv_norm(sym)
        price = 0.0
        is_demo = bool(result.get("is_demo", False))
        if ohlcv and ohlcv.get("closes"):
            price = float(ohlcv["closes"][-1])
        _obe.record_signal(
            symbol=sym,
            signal_type=sig_type,
            signal_class=str(result.get("signal_class", "") or ""),
            signal=sig_type,
            score=score,
            price_at_signal=price,
            market_state=market_st,
            is_demo=is_demo,
        )
    except Exception:
        pass


# ── Top-Tier Decision System — Phase 13B ──────────────────────────────────────
import top_tier_engine          as _tte    # legacy simplified engine (UI compat)
import market_regime_engine     as _mre
import data_quality_engine      as _dqe
import top_tier_decision_engine as _ttde
import position_sizing_engine   as _pse


# ── GET /api/market-regime ────────────────────────────────────────────────────

@app.route("/api/market-regime")
def api_market_regime():
    """
    Current market regime: RISK_ON / NEUTRAL / RISK_OFF / CRASH_RISK.
    Based on SPY and QQQ MA positions and daily changes.
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        result = _mre.run_market_regime(_get_ohlcv_norm)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── GET /api/top-tier-decision/<symbol> ──────────────────────────────────────

@app.route("/api/top-tier-decision/<symbol>")
def api_top_tier_decision_get(symbol: str):
    """
    Full top-tier decision for a symbol.
    Optional query params: cost, holding_days
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        sym  = str(symbol or "").upper().strip()
        cost = float(request.args.get("cost", 0) or 0)
        days = int(request.args.get("holding_days", 0) or 0)
        if not sym:
            return jsonify({"ok": False, "error": "symbol 不能為空"}), 400
        result = _ttde.run_top_tier_decision(
            sym, _get_ohlcv_norm, cost=cost, holding_days=days
        )
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── POST /api/top-tier-decision ───────────────────────────────────────────────

@app.route("/api/top-tier-decision", methods=["POST"])
def api_top_tier_decision():
    """
    Full top-tier decision. Accepts:
      { symbol, cost?, holding_days?, sector_name?,
        watchlist_symbols?[list of symbol strings] }
    Also accepts legacy frontend payload (falls back gracefully).
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body = request.json or {}
        sym  = str(body.get("symbol", "") or "").upper().strip()

        # Legacy frontend path: no symbol means pre-computed data from old engine
        if not sym:
            result = _tte.run_top_tier(body)
            return jsonify(result)

        cost  = float(body.get("cost", 0) or 0)
        days  = int(body.get("holding_days", 0) or 0)
        sec_n = str(body.get("sector_name", "") or "")

        # Build watchlist ohlcv map if symbols provided
        wl_syms = body.get("watchlist_symbols") or []
        wl_ohlcv: dict = {}
        for ws in (wl_syms or []):
            try:
                wl_ohlcv[str(ws).upper()] = _get_ohlcv_norm(str(ws).upper())
            except Exception:
                pass

        result = _ttde.run_top_tier_decision(
            sym, _get_ohlcv_norm,
            cost=cost, holding_days=days,
            sector_name=sec_n,
            watchlist_ohlcv=wl_ohlcv if wl_ohlcv else None,
        )
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Position Sizing Engine — Phase 12B ────────────────────────────────────────

@app.route("/api/position-size", methods=["POST"])
def api_position_size():
    """
    Compute optimal position size using Fixed-Risk + Half-Kelly + ATR methods.
    Body: {
      decision          : "STRONG_BUY" | "BUY" | ...
      top_tier_score    : int 0-100
      market_regime     : "RISK_ON" | "NEUTRAL" | "RISK_OFF" | "CRASH_RISK"
      risk_budget_mult  : float 0.0-1.5
      chase_risk_score  : int 0-100
      symbol?           : str   (optional; fetches OHLCV for ATR)
      account_size?     : float (USD; enables share/dollar output)
      entry_price?      : float
      stop_loss_price?  : float (override; default = ATR-based)
      risk_per_trade_pct? : float (default 1.0)
      max_position_pct?   : float % (default 15.0)
      win_rate_estimate?  : float 0-1 (default 0.55)
      reward_risk_ratio?  : float (default 2.0)
    }
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body = request.json or {}
        sym  = str(body.get("symbol", "") or "").upper().strip()

        # Fetch OHLCV for ATR calculation if symbol provided
        if sym and "ohlcv" not in body:
            try:
                body["ohlcv"] = _get_ohlcv_norm(sym)
            except Exception:
                pass

        result = _pse.run_position_sizing(body)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# Alias used in user docs / external references
app.add_url_rule("/api/position-sizing", endpoint="api_position_sizing_alias",
                 view_func=api_position_size, methods=["POST"])


@app.route("/api/position-size/<symbol>")
def api_position_size_get(symbol: str):
    """
    Quick position size check for a symbol using current market conditions.
    Queries market regime and chase risk automatically.
    Optional query params: account_size, risk_per_trade_pct, entry_price, cost
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return jsonify({"ok": False, "error": "symbol 不能為空"}), 400

        # Fetch all data
        ohlcv  = _get_ohlcv_norm(sym)
        mr     = _mre.run_market_regime(_get_ohlcv_norm)
        from risk_engine import calc_chase_risk
        cr     = calc_chase_risk(ohlcv) if (ohlcv or {}).get("closes") else {"score": 50}

        account = float(request.args.get("account_size", 0) or 0)
        risk_pct = float(request.args.get("risk_per_trade_pct", 1.0) or 1.0)
        entry   = float(request.args.get("entry_price", 0) or 0)

        payload = {
            "decision":           "BUY",   # conservative default
            "top_tier_score":     60,
            "market_regime":      mr.get("market_regime", "NEUTRAL"),
            "risk_budget_mult":   mr.get("risk_budget_multiplier", 0.6),
            "chase_risk_score":   cr.get("score") or 50,
            "ohlcv":              ohlcv,
            "account_size":       account,
            "entry_price":        entry or None,
            "risk_per_trade_pct": risk_pct,
        }
        result = _pse.run_position_sizing(payload)
        result["symbol"] = sym
        result["market_regime"] = mr.get("market_regime")
        result["chase_risk_score"] = cr.get("score")
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Signal Confidence Engine — Phase 12C ──────────────────────────────────────
import signal_confidence_engine as _sce

try:
    _sce.init_db(_USER_DATA_DB)
except Exception:
    traceback.print_exc()


@app.route("/api/signal-confidence")
def api_signal_confidence_all():
    """Return confidence statistics for all signal types."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        data = _sce.get_confidence_stats()
        return jsonify({"ok": True, "signal_confidence": data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/signal-confidence/<signal_type>")
def api_signal_confidence_type(signal_type: str):
    """Return confidence statistics for a single signal type."""
    auth = _require_auth()
    if auth:
        return auth
    valid = {st.upper() for st in _sce.SIGNAL_TYPES}
    stype = signal_type.upper().replace("-", "_")
    if stype not in valid:
        return jsonify({"ok": False, "error": f"Unknown signal type. Valid: {sorted(valid)}"}), 400
    try:
        data = _sce.get_confidence_stats(stype)
        return jsonify({"ok": True, "signal_confidence": data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/signal-confidence/record", methods=["POST"])
def api_signal_confidence_record():
    """Manually record one signal for testing or production use."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        body = request.json or {}
        result = _sce.record_signal(body)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/signal-confidence/update-outcomes", methods=["POST"])
def api_signal_confidence_update():
    """Update 1d/3d/5d outcomes for recorded signals."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        body = request.json or {}
        # Accept either a single dict or {"updates": [...]}
        updates = body if isinstance(body, list) else body.get("updates", body)
        result = _sce.update_outcomes(updates)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/signal-history")
def api_signal_history():
    """Return recent signal history records."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        limit = int(request.args.get("limit", 50))
        data  = _sce.get_signal_history(limit=limit)
        return jsonify({"ok": True, "count": len(data), "records": data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Phase 13: Institutional Grade Engines ─────────────────────────────────────
import institutional_flow_engine as _ife
import macro_risk_engine         as _mre13
import portfolio_manager_engine  as _pme

_portfolio_cache: dict = {"result": None, "ts": 0}


# ── Phase 13A: Institutional Flow ─────────────────────────────────────────────

@app.route("/api/institutional-flow/<symbol>")
def api_institutional_flow(symbol: str):
    """Institutional accumulation/distribution analysis for a single symbol."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        sym = symbol.upper().strip()
        if not sym:
            return jsonify({"ok": False, "error": "symbol required"}), 400
        ohlcv       = _get_ohlcv_norm(sym)
        qqq_ohlcv   = _get_ohlcv_norm("QQQ")
        # Try to get sector ETF
        from sector_map import SYMBOL_TO_SECTOR
        sector_name = SYMBOL_TO_SECTOR.get(sym, "")
        sector_etf_sym = None
        _SECTOR_ETF_MAP = {"半導體AI晶片": "SOXX", "AI雲端軟體/網路安全": "XLK",
                           "電力能源": "XLE", "國防航太": "ITA"}
        if sector_name:
            sector_etf_sym = _SECTOR_ETF_MAP.get(sector_name)
        sector_ohlcv = _get_ohlcv_norm(sector_etf_sym) if sector_etf_sym else None
        result = _ife.run_institutional_flow(sym, ohlcv, qqq_ohlcv, sector_ohlcv)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/institutional-flow/batch", methods=["POST"])
def api_institutional_flow_batch():
    """Batch institutional flow analysis for multiple symbols."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        body    = request.json or {}
        symbols = body.get("symbols") or []
        if not symbols:
            return jsonify({"ok": False, "error": "symbols list required"}), 400
        symbols = [str(s).upper().strip() for s in symbols[:20]]  # cap at 20

        qqq_ohlcv = _get_ohlcv_norm("QQQ")
        results   = {}
        for sym in symbols:
            try:
                ohlcv = _get_ohlcv_norm(sym)
                results[sym] = _ife.run_institutional_flow(sym, ohlcv, qqq_ohlcv)
            except Exception as e:
                results[sym] = {"ok": False, "symbol": sym, "error": str(e)}

        return jsonify({"ok": True, "results": results, "count": len(results)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Phase 13B: Portfolio Manager ──────────────────────────────────────────────

@app.route("/api/portfolio-manager/recommendation", methods=["POST"])
def api_portfolio_recommendation():
    """
    Full portfolio allocation recommendation.
    Body: {
      account_value    : float,
      current_cash     : float,
      holdings         : [{symbol, shares, cost, sector?}],
      watchlist        : [symbol, ...],
      risk_preference  : "conservative" | "balanced" | "aggressive"
    }
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body = request.json or {}
        result = _pme.run_portfolio_recommendation(
            account_value   = float(body.get("account_value", 0) or 0),
            current_cash    = float(body.get("current_cash",  0) or 0),
            holdings        = body.get("holdings",   []) or [],
            watchlist       = body.get("watchlist",  []) or [],
            risk_preference = str(body.get("risk_preference", "balanced") or "balanced"),
            ohlcv_fn        = _get_ohlcv_norm,
        )
        _portfolio_cache["result"] = result
        _portfolio_cache["ts"]     = _time.time()
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/portfolio-manager/latest")
def api_portfolio_latest():
    """Return the most recent portfolio recommendation (from in-memory cache)."""
    auth = _require_auth()
    if auth:
        return auth
    if not _portfolio_cache.get("result"):
        return jsonify({"ok": False, "error": "尚無組合分析結果，請先呼叫 POST /api/portfolio-manager/recommendation"}), 404
    age = round(_time.time() - _portfolio_cache["ts"])
    return jsonify({"ok": True, "cached_seconds_ago": age, **_portfolio_cache["result"]})


# ── Phase 13C: Macro Risk ─────────────────────────────────────────────────────

@app.route("/api/macro-risk")
def api_macro_risk():
    """Macro-level risk score across equity, bonds, defensive rotation, and volatility."""
    auth = _require_auth()
    if auth:
        return auth
    try:
        result = _mre13.run_macro_risk(_get_ohlcv_norm)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Phase 15: Investment Committee ───────────────────────────────────────────
import investment_committee_engine as _ice

_committee_cache: dict = {}   # symbol -> {result, ts}


@app.route("/api/investment-committee/<symbol>")
def api_investment_committee_get(symbol: str):
    """
    GET single-symbol investment committee analysis.
    Optional query params:
      risk_profile : conservative | balanced | aggressive  (default: balanced)
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        sym          = symbol.upper().strip()
        risk_profile = request.args.get("risk_profile", "balanced")
        if not sym:
            return jsonify({"ok": False, "error": "symbol required"}), 400

        cached = _committee_cache.get(sym)
        if cached and _time.time() - cached["ts"] < 300:   # 5-min TTL
            return jsonify({"ok": True, "cached_seconds_ago": round(_time.time() - cached["ts"]),
                            **cached["result"]})

        result = _ice.run_investment_committee(sym, _get_ohlcv_norm,
                                               risk_profile=risk_profile)
        _committee_cache[sym] = {"result": result, "ts": _time.time()}
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/investment-committee", methods=["POST"])
def api_investment_committee_post():
    """
    POST — batch or single committee analysis with optional holdings.
    Body: {
      symbol       : str,
      risk_profile : str (optional),
      holdings     : [{symbol, shares, cost}] (optional)
    }
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body         = request.json or {}
        sym          = str(body.get("symbol") or "").upper().strip()
        risk_profile = str(body.get("risk_profile") or "balanced")
        holdings     = body.get("holdings") or []
        if not sym:
            return jsonify({"ok": False, "error": "symbol required"}), 400

        result = _ice.run_investment_committee(
            sym, _get_ohlcv_norm,
            holdings=holdings,
            risk_profile=risk_profile,
        )
        _committee_cache[sym] = {"result": result, "ts": _time.time()}
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Phase 16: 7-Module Momentum Score ────────────────────────────────────────
try:
    import final_decision_engine as _fde
    _HAS_FDE = True
except ImportError:
    _HAS_FDE = False

_momentum_score_cache: dict = {}   # symbol -> {result, ts}
_MOMENTUM_SCORE_TTL = 300          # 5 min


@app.route("/api/momentum-score/<symbol>")
def api_momentum_score(symbol: str):
    """
    7-module institutional momentum score.

    Returns
    -------
    {
      ok, symbol, final_score, grade, signal_status,
      action_code, action_label, action,
      stop_loss, target_range, invalidation,
      position_sizing_suggestion,
      component_scores, weighted_scores,
      reasons, risk_warnings,
      confidence, is_demo, disclaimer,
      detail: {risk_level, distribution, has_news, gate_notes}
    }

    Query params:
      bench   : benchmark symbol (default QQQ)
      profile : risk_profile conservative|balanced|aggressive (default balanced)
    """
    auth = _require_auth()
    if auth:
        return auth
    if not _HAS_FDE:
        return jsonify({"ok": False, "error": "final_decision_engine 模組未載入"}), 503

    sym = symbol.upper().strip()
    if not sym:
        return jsonify({"ok": False, "error": "symbol required"}), 400

    bench_sym    = (request.args.get("bench") or "QQQ").upper().strip()
    risk_profile = (request.args.get("profile") or "balanced").lower().strip()

    # cache check
    cache_key = f"{sym}:{bench_sym}:{risk_profile}"
    cached = _momentum_score_cache.get(cache_key)
    if cached and _time.time() - cached["ts"] < _MOMENTUM_SCORE_TTL:
        return jsonify({"ok": True, "cached": True, **cached["result"]})

    try:
        ohlcv = _get_ohlcv_norm(sym)
        if not ohlcv:
            return jsonify({"ok": False, "error": f"無法取得 {sym} 的 K 線資料"}), 404

        bench_ohlcv = None
        if bench_sym and bench_sym != sym:
            try:
                bench_ohlcv = _get_ohlcv_norm(bench_sym)
            except Exception:
                bench_ohlcv = None

        result = _fde.compute(
            ohlcv,
            bench_ohlcv=bench_ohlcv,
            news_items=None,       # 可由呼叫端 POST 傳入，GET 版本不含新聞
            fundamentals=None,
            risk_profile=risk_profile,
        )

        _momentum_score_cache[cache_key] = {"result": result, "ts": _time.time()}
        return jsonify({"ok": True, "symbol": sym, "cached": False, **result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/momentum-score", methods=["POST"])
def api_momentum_score_post():
    """
    POST version — supports news_items and fundamentals.

    Body
    ----
    {
      symbol       : str,
      bench        : str (optional, default QQQ),
      profile      : str (optional, default balanced),
      news_items   : [{sentiment, headline}, ...] (optional),
      fundamentals : {pe, pb, revenue_growth_yoy, ...} (optional)
    }
    """
    auth = _require_auth()
    if auth:
        return auth
    if not _HAS_FDE:
        return jsonify({"ok": False, "error": "final_decision_engine 模組未載入"}), 503

    try:
        body         = request.json or {}
        sym          = str(body.get("symbol") or "").upper().strip()
        bench_sym    = str(body.get("bench") or "QQQ").upper().strip()
        risk_profile = str(body.get("profile") or "balanced").lower().strip()
        news_items   = body.get("news_items") or None
        fundamentals = body.get("fundamentals") or None

        if not sym:
            return jsonify({"ok": False, "error": "symbol required"}), 400

        ohlcv = _get_ohlcv_norm(sym)
        if not ohlcv:
            return jsonify({"ok": False, "error": f"無法取得 {sym} 的 K 線資料"}), 404

        bench_ohlcv = None
        if bench_sym and bench_sym != sym:
            try:
                bench_ohlcv = _get_ohlcv_norm(bench_sym)
            except Exception:
                bench_ohlcv = None

        result = _fde.compute(
            ohlcv,
            bench_ohlcv=bench_ohlcv,
            news_items=news_items,
            fundamentals=fundamentals,
            risk_profile=risk_profile,
        )

        return jsonify({"ok": True, "symbol": sym, "cached": False, **result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Phase 14: Portfolio Optimizer ────────────────────────────────────────────
import portfolio_optimizer as _poe

_optimizer_cache: dict = {"result": None, "ts": 0}


@app.route("/api/portfolio-optimize", methods=["POST"])
def api_portfolio_optimize():
    """
    Compute optimal portfolio allocation across all engines.
    Body: {
      account_value  : float,
      current_cash   : float,
      holdings       : [{symbol, shares, cost, sector?}],
      watchlist      : [symbol, ...],
      risk_profile   : "conservative" | "balanced" | "aggressive"
    }
    """
    auth = _require_auth()
    if auth:
        return auth
    try:
        body = request.json or {}
        result = _poe.run_portfolio_optimize(
            account_value = float(body.get("account_value", 0) or 0),
            current_cash  = float(body.get("current_cash",  0) or 0),
            holdings      = body.get("holdings",  []) or [],
            watchlist     = body.get("watchlist",  []) or [],
            risk_profile  = str(body.get("risk_profile", "balanced") or "balanced"),
            ohlcv_fn      = _get_ohlcv_norm,
        )
        _optimizer_cache["result"] = result
        _optimizer_cache["ts"]     = _time.time()
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/portfolio-optimize/latest")
def api_portfolio_optimize_latest():
    """Return the most recent portfolio optimization result (in-memory cache)."""
    auth = _require_auth()
    if auth:
        return auth
    if not _optimizer_cache.get("result"):
        return jsonify({
            "ok": False,
            "error": "尚無優化結果，請先呼叫 POST /api/portfolio-optimize",
        }), 404
    age = round(_time.time() - _optimizer_cache["ts"])
    return jsonify({"ok": True, "cached_seconds_ago": age, **_optimizer_cache["result"]})


# ── 一鍵選股報告 (/scan) ──────────────────────────────────────────────────────
_scan_cache: dict = {"html": None, "ts": 0}
_SCAN_TTL = 3600  # 快取 1 小時（掃描較重）


def _scan_fetch_many(syms):
    """平行抓多檔 OHLCV，回傳 {sym: ohlcv}（跳過失敗）。"""
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_get_ohlcv_norm, s): s for s in syms}
        for f in as_completed(futs):
            try:
                o = f.result()
                if o and o.get("closes"):
                    out[futs[f]] = o
            except Exception:
                pass
    return out


def _scan_chase_risk(o):
    """伺服器端追高風險（與前端動能爆發同邏輯的日線版）：回傳 (分數, 等級)。"""
    closes = o.get("closes", [])
    if len(closes) < 5:
        return 0, "低"
    prev = closes[-2] or closes[-1]
    chg = (closes[-1] / prev - 1) * 100 if prev else 0
    win = closes[-60:]
    hi = max(win) if win else closes[-1]
    pctFH = (closes[-1] / hi - 1) * 100 if hi else -50
    try:
        rsi_list = _bt._rsi(closes)
        rsi = next((v for v in reversed(rsi_list) if v is not None), 50)
    except Exception:
        rsi = 50
    vols = o.get("volumes", [])
    vr = 1.0
    if len(vols) >= 21:
        avg = sum(vols[-21:-1]) / 20
        vr = vols[-1] / avg if avg else 1.0
    risk = 0
    if chg > 9: risk += 45
    elif chg > 7: risk += 35
    elif chg > 4: risk += 18
    elif chg > 2: risk += 7
    if pctFH >= -3: risk += 25
    elif pctFH >= -8: risk += 12
    if rsi > 80: risk += 18
    elif rsi > 72: risk += 9
    if vr > 4: risk += 12
    elif vr > 2.5: risk += 6
    risk = min(round(risk), 100)
    level = "高" if risk >= 60 else "中" if risk >= 35 else "低"
    return risk, level


@app.route("/scan")
def scan_report():
    """一鍵選股報告：大盤 → 產業輪動 → 強勢產業選股（7模組）→ 追高風險。"""
    now = _time.time()
    if _scan_cache["html"] and now - _scan_cache["ts"] < _SCAN_TTL:
        return Response(_scan_cache["html"], mimetype="text/html")

    # universe：每個產業取前 5 檔 + 大盤基準
    universe = []
    for syms in _smap.SECTOR_SYMBOLS.values():
        universe += syms[:5]
    universe = list(dict.fromkeys(universe + ["QQQ", "SPY"]))
    data = _scan_fetch_many(universe)
    bench = data.get("QQQ") or data.get("SPY")

    try:
        ms = _dp.market_state()
        market_txt = f"{ms.get('overall', '?')}（{ms.get('regime', '?')}）"
        demo_seen = bool(ms.get("is_demo"))
    except Exception:
        market_txt, demo_seen = "未知", False

    # 產業輪動排名
    sector_rank = []
    for sector, syms in _smap.SECTOR_SYMBOLS.items():
        stocks = {s: data[s] for s in syms[:5] if s in data}
        if not stocks:
            continue
        try:
            r = _de.run_sector_leadership(sector, stocks)
            sector_rank.append((sector, r.get("score", 0), r.get("level_label", "")))
        except Exception:
            pass
    sector_rank.sort(key=lambda x: -x[1])
    strong = [s for s in sector_rank if s[1] >= 60][:5]
    weak = [s for s in sector_rank if s[1] <= 40][-3:]
    top_sectors = strong or sector_rank[:3]

    # 選股：強勢產業成分股跑 7 模組決策
    picks, seen = [], set()
    for sector, _sc, _lv in top_sectors:
        for s in _smap.get_sector_symbols(sector)[:5]:
            if s in seen or s not in data:
                continue
            seen.add(s)
            if not _HAS_FDE:
                break
            try:
                r = _fde.compute(data[s], bench_ohlcv=bench, risk_profile="balanced")
            except Exception:
                continue
            demo_seen = demo_seen or bool(r.get("is_demo"))
            if r.get("action_code") == "BUY" or r.get("grade") in ("A+", "A", "B"):
                risk, level = _scan_chase_risk(data[s])
                picks.append({
                    "symbol": s, "sector": sector,
                    "score": round(r.get("total_score", 0), 1),
                    "grade": r.get("grade", "?"),
                    "action": r.get("action_label", r.get("action_code", "")),
                    "stop": r.get("stop_loss"),
                    "risk": risk, "level": level,
                })
    picks.sort(key=lambda p: -(p["score"] or 0))
    picks = picks[:12]

    date_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    demo_note = "（示範資料）" if demo_seen else ""

    css = """
<style>
*{box-sizing:border-box}body{margin:0;background:#0f1419;color:#e6edf3;
 font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:14px}
a.back{color:#8b98a5;font-size:.8rem;text-decoration:none}
h1{font-size:1.15rem;margin:6px 0 2px}.sub{font-size:.75rem;color:#8b98a5;margin-bottom:12px}
.sec{background:#1a212b;border-radius:12px;padding:12px 14px;margin-bottom:12px}
.sec h2{font-size:.85rem;margin:0 0 8px;color:#58a6ff}
.row{font-size:.8rem;padding:5px 0;border-bottom:1px solid #232a35;line-height:1.5}
.row:last-child{border-bottom:none}
.tag{font-size:.62rem;padding:1px 7px;border-radius:4px;margin-left:5px}
.strong{color:#3fb950}.weak{color:#f85149}.muted{color:#8b98a5}
.rl-高{color:#f85149;font-weight:700}.rl-中{color:#e3b341;font-weight:700}.rl-低{color:#3fb950;font-weight:700}
.card{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.78rem;min-width:520px}
th{background:#232a35;color:#fff;padding:7px 9px;text-align:right;white-space:nowrap;position:sticky;top:0}
th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
td{padding:7px 9px;border-bottom:1px solid #232a35;white-space:nowrap}
.disc{font-size:.66rem;color:#8b98a5;margin-top:10px;line-height:1.6}
</style>
"""
    parts = [
        css, '<div class="wrap">',
        '<a class="back" href="/">← 返回首頁</a>',
        f"<h1>🎯 一鍵選股報告{demo_note}</h1>",
        f'<div class="sub">大盤研判：{market_txt}　｜　更新：{date_str}（快取1小時）</div>',
    ]

    # 產業輪動
    parts.append('<div class="sec"><h2>🔄 產業輪動</h2>')
    if strong:
        parts.append('<div class="row"><span class="strong">🔥 資金流入：</span>'
                     + "、".join(f"{s}({sc:.0f})" for s, sc, _ in strong) + "</div>")
    if weak:
        parts.append('<div class="row"><span class="weak">❄️ 轉弱避開：</span>'
                     + "、".join(f"{s}({sc:.0f})" for s, sc, _ in weak) + "</div>")
    if not strong and not weak:
        parts.append('<div class="row muted">目前產業強弱不明顯</div>')
    parts.append("</div>")

    # 選股清單
    parts.append('<div class="sec"><h2>✅ 強勢產業選股（7模組決策）</h2>')
    if picks:
        parts.append('<div class="card"><table><thead><tr>'
                     '<th>代號</th><th>產業</th><th>評級</th><th>分數</th>'
                     '<th>行動</th><th>停損</th><th>追高風險</th></tr></thead><tbody>')
        for p in picks:
            stop = f"{p['stop']:.2f}" if isinstance(p["stop"], (int, float)) else "—"
            parts.append(
                f"<tr><td><b>{p['symbol']}</b></td><td class='muted'>{p['sector']}</td>"
                f"<td>{p['grade']}</td><td>{p['score']}</td><td>{p['action']}</td>"
                f"<td>{stop}</td><td class='rl-{p['level']}'>{p['level']}</td></tr>"
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append('<div class="row muted">目前強勢產業中無符合條件（BUY／A~B級）的標的——寧可空手等更好的機會</div>')
    parts.append("</div>")

    parts.append('<div class="disc">⚠️ 本報告為機械式量化彙整（大盤研判 + 產業領導力 + 7模組決策 + 追高風險），'
                 '僅供參考、不構成投資建議。追高風險「高」者不宜直接追價，宜等回測或分時承接；請嚴守停損。</div>')
    parts.append("</div>")

    html = ('<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>一鍵選股報告</title>" + "".join(parts) + "</body></html>")

    _scan_cache["html"] = html
    _scan_cache["ts"] = now
    return Response(html, mimetype="text/html")


# ── 台股動能排行榜 (/momentum) ────────────────────────────────────────────────

def _mom_num(s):
    """安全轉數值：處理 '--'、空字串、逗號；無法轉換回傳 None。"""
    try:
        t = str(s).replace(",", "").strip()
        if t in ("", "--", "---", "—", "X", "N/A", "null", "None"):
            return None
        return float(t)
    except (ValueError, TypeError):
        return None


def _mom_pct_rank(vals):
    """把一串數值轉成 0~100 的百分位排名（值越大分數越高）。"""
    n = len(vals)
    if n == 0:
        return []
    if n == 1:
        return [100.0]
    order = sorted(range(n), key=lambda i: vals[i])  # 由小到大
    pct = [0.0] * n
    for rank, i in enumerate(order):
        pct[i] = rank / (n - 1) * 100.0
    return pct


@app.route("/momentum")
def momentum_ranking():
    """台股強勢股動能排行榜（HTML 表格，手機友善）。"""
    try:
        data = get_twse_stock_day_all()
        # get_twse_stock_day_all() 回傳 pandas DataFrame；轉成 list[dict] 方便逐檔處理。
        items = data.to_dict("records") if hasattr(data, "to_dict") else (data or [])
    except Exception:
        traceback.print_exc()
        items = []

    rows = []
    for it in items:
        code = str(it.get("Code", "")).strip()
        # 只保留 4 位數字股票代號；排除 ETF(00 開頭)、權證(非 4 碼)、特別股(含英文字母)
        if not (code.isdigit() and len(code) == 4 and not code.startswith("00")):
            continue
        name = str(it.get("Name", "")).strip()
        o = _mom_num(it.get("OpeningPrice"))
        h = _mom_num(it.get("HighestPrice"))
        lo = _mom_num(it.get("LowestPrice"))
        c = _mom_num(it.get("ClosingPrice"))
        tv = _mom_num(it.get("TradeValue"))
        if None in (o, h, lo, c, tv) or o <= 0 or (h - lo) <= 0:
            continue

        intraday = (c - o) / o * 100.0            # 日內漲幅%
        amplitude = (h - lo) / o * 100.0          # 振幅%
        near_high = (c - lo) / (h - lo) * 100.0   # 收盤近高%
        tv_yi = tv / 100000000.0                  # 成交金額（億）

        rows.append({
            "code": code, "name": name,
            "open": o, "high": h, "low": lo, "close": c,
            "intraday": intraday, "amplitude": amplitude,
            "near_high": near_high, "tv_yi": tv_yi,
        })

    # ── 動能分數：成交金額35% + 日內漲幅35% + 收盤近高20% + 振幅10%（皆百分位排名）──
    if rows:
        p_val = _mom_pct_rank([r["tv_yi"] for r in rows])
        p_int = _mom_pct_rank([r["intraday"] for r in rows])
        p_nh = _mom_pct_rank([r["near_high"] for r in rows])
        p_amp = _mom_pct_rank([r["amplitude"] for r in rows])
        for i, r in enumerate(rows):
            r["score"] = round(
                p_val[i] * 0.35 + p_int[i] * 0.35 + p_nh[i] * 0.20 + p_amp[i] * 0.10, 1
            )
        rows.sort(key=lambda r: r["score"], reverse=True)
    top = rows[:30]

    data_date = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")

    css = """
<style>
*{box-sizing:border-box}
body{margin:0;background:#0f1419;color:#1a1a1a;
  font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:14px}
.head{color:#e6edf3;margin-bottom:12px}
.head h1{font-size:1.18rem;margin:0 0 4px}
.head .sub{font-size:.78rem;color:#8b98a5;line-height:1.6}
.card{background:#fff;border-radius:12px;padding:4px;
  overflow-x:auto;-webkit-overflow-scrolling:touch;box-shadow:0 2px 10px rgba(0,0,0,.3)}
table{border-collapse:collapse;width:100%;font-size:.8rem;min-width:820px}
thead th{position:sticky;top:0;z-index:2;background:#1a212b;color:#fff;
  padding:9px 10px;text-align:right;white-space:nowrap;font-weight:600}
thead th:nth-child(-n+3){text-align:left}
tbody td{padding:8px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid #eee}
tbody td:nth-child(-n+3){text-align:left}
tbody tr:nth-child(even){background:#fafafa}
tbody tr.hot{background:#ffe0e0}
.up{color:#d11418;font-weight:700}
.down{color:#0a8f3c;font-weight:700}
.score{font-weight:800;color:#c1121f}
.rk{color:#8b98a5;font-weight:700}
.empty{background:#fff;border-radius:12px;padding:24px;text-align:center;color:#666}
</style>
"""

    head = (
        '<div class="head">'
        '<a href="/" style="color:#8b98a5;font-size:.8rem;text-decoration:none">← 返回首頁</a>'
        '<h1>🚀 台股動能排行榜 Top 30</h1>'
        '<div class="sub">資料來源：TWSE OpenAPI　｜　更新日期：' + data_date + "</div></div>"
    )

    if not top:
        body = '<div class="empty">目前無法取得 TWSE 資料，請稍後再試。</div>'
    else:
        header_cells = "".join(
            f"<th>{h}</th>" for h in [
                "排名", "代號", "名稱", "開盤", "最高", "最低", "收盤",
                "日內漲幅%", "振幅%", "收盤近高%", "成交金額億", "動能分數",
            ]
        )
        body_rows = []
        for i, r in enumerate(top, 1):
            hot = " class=\"hot\"" if r["score"] >= 95 else ""
            ic = "up" if r["intraday"] > 0 else "down" if r["intraday"] < 0 else ""
            body_rows.append(
                f"<tr{hot}>"
                f'<td class="rk">{i}</td>'
                f"<td>{r['code']}</td>"
                f"<td>{r['name']}</td>"
                f"<td>{r['open']:.2f}</td>"
                f"<td>{r['high']:.2f}</td>"
                f"<td>{r['low']:.2f}</td>"
                f"<td>{r['close']:.2f}</td>"
                f'<td class="{ic}">{r["intraday"]:+.2f}</td>'
                f"<td>{r['amplitude']:.2f}</td>"
                f"<td>{r['near_high']:.1f}</td>"
                f"<td>{r['tv_yi']:.2f}</td>"
                f'<td class="score">{r["score"]:.1f}</td>'
                "</tr>"
            )
        body = (
            '<div class="card"><table><thead><tr>'
            + header_cells
            + "</tr></thead><tbody>"
            + "".join(body_rows)
            + "</tbody></table></div>"
        )

    html = (
        '<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>台股動能排行榜 Top 30</title>"
        + css
        + '</head><body><div class="wrap">'
        + head
        + body
        + "</div></body></html>"
    )
    return Response(html, mimetype="text/html")


# ── Mobile AI agent chat ────────────────────────────────────────────────────────

@app.route("/agent")
def agent_page():
    """手機/網頁版 AI 助理聊天頁（受登入保護，沿用全域 before_request）。"""
    return render_template("agent.html")


@app.route("/api/agent-chat", methods=["POST"])
def api_agent_chat():
    """跑一輪 Agent 對話（含工具迴圈），回傳助理回覆與更新後的歷史。"""
    if _web_agent is None:
        return jsonify({"ok": False, "error": "Agent 模組未載入"}), 500
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
    try:
        limited = _rate_limit("agent_chat", 12, 60)
        if limited is not None:
            return limited
        payload = request.json or {}
        history = payload.get("messages") or []
        if not isinstance(history, list):
            return jsonify({"ok": False, "error": "messages 必須是陣列"}), 400
        message_raw = payload.get("message") or ""
        if not isinstance(message_raw, str):
            return jsonify({"ok": False, "error": "message 必須是文字"}), 400
        message = message_raw.strip()
        if not message:
            return jsonify({"ok": False, "error": "訊息不可為空"}), 400
        if len(message) > 4000:
            return jsonify({"ok": False, "error": "訊息過長（最多 4000 字元）"}), 400
        result = _web_agent.run_turn(history[-40:], message)
        return jsonify({"ok": True, **result})
    except Exception as e:  # noqa: BLE001
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
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
