"""汽車業務客戶追蹤 CRM — Flask 入口。

啟動：
    cd car_crm && python app.py          # 預設 http://0.0.0.0:5001

刻意使用 5001 埠，避免與上層 Scott 投資分析系統（5000）衝突。
"""
from __future__ import annotations

import hmac
import os
import secrets

from flask import (Flask, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)

import service as svc
from db import (DEFAULT_DB_PATH, STAGE_LABELS, STAGES, connect, init_db,
                today_str)

app = Flask(__name__)
# 未設定時每次啟動產生隨機值：session 會失效但不會用可預測的金鑰簽章。
app.secret_key = os.environ.get("CAR_CRM_SECRET_KEY") or secrets.token_hex(32)
app.config["DB_PATH"] = os.environ.get("CAR_CRM_DB", DEFAULT_DB_PATH)
app.config["ACCESS_CODE"] = os.environ.get("CAR_CRM_ACCESS_CODE", "")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# 不需要通過存取碼即可取用的端點（PWA 資源與登入頁本身）
_PUBLIC_ENDPOINTS = {"login", "manifest", "service_worker", "static"}


# ── 連線管理 ─────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = connect(app.config["DB_PATH"])
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.context_processor
def inject_globals():
    return {
        "STAGES": STAGES,
        "STAGE_LABELS": STAGE_LABELS,
        "today": today_str(),
        "csrf_token": _csrf_token,
    }


# ── 存取控制 ─────────────────────────────────────────────────────────────────

def _csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _access_required() -> bool:
    """未設定存取碼＝單機模式，不強制登入（見 run_server 的綁定限制）。"""
    return bool(app.config.get("ACCESS_CODE"))


@app.before_request
def _guard():
    # CSRF：所有變更操作都必須帶有本 session 的 token。
    # 先比對再產生——否則新 session 會拿到剛產生的 token 而誤判通過。
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            return render_template("forbidden.html",
                                   reason="表單已過期或來源不正確，請重新整理後再試"), 403
    else:
        _csrf_token()      # 確保頁面渲染前 token 已存在（含沒有表單的空頁面）

    if not _access_required() or session.get("authed"):
        return None
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _access_required():
        return redirect(url_for("index"))
    if request.method == "POST":
        supplied = request.form.get("code", "")
        if hmac.compare_digest(supplied, app.config["ACCESS_CODE"]):
            session["authed"] = True
            return redirect(_safe_next(request.form.get("next")))
        flash("存取碼錯誤", "error")
        return render_template("login.html", next=request.form.get("next", "")), 401
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def _safe_next(target: str | None) -> str:
    """只允許站內相對路徑，避免開放轉址被用於釣魚。"""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


@app.after_request
def _security_headers(response):
    # 含客戶姓名與手機的頁面不得被瀏覽器寫入磁碟快取
    if not request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.template_filter("money")
def money_filter(value) -> str:
    """金額顯示：以萬元為單位，避免手機上數字過長。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number == 0:
        return "0"
    return f"{number:,.0f}"


# ── 首頁 ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html", d=svc.dashboard(get_db()))


# ── 客戶 ─────────────────────────────────────────────────────────────────────

@app.route("/customers")
def customers():
    stage = request.args.get("stage") or None
    keyword = request.args.get("q", "")
    rows = svc.list_customers(get_db(), stage=stage, keyword=keyword)
    return render_template("customers.html", customers=rows,
                           active_stage=stage, keyword=keyword)


@app.route("/customers/new", methods=["GET", "POST"])
def customer_new():
    if request.method == "POST":
        try:
            customer_id = svc.create_customer(get_db(), request.form.to_dict())
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("customer_form.html", customer=None,
                                   form=request.form), 400
        flash("客戶已建立", "ok")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    return render_template("customer_form.html", customer=None, form={})


@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id: int):
    con = get_db()
    customer = svc.get_customer(con, customer_id)
    if not customer:
        return render_template("not_found.html"), 404
    return render_template(
        "customer_detail.html",
        customer=customer,
        follow_ups=svc.customer_follow_ups(con, customer_id),
        test_drives=svc.customer_test_drives(con, customer_id),
        quotes=svc.customer_quotes(con, customer_id),
        trade_ins=svc.customer_trade_ins(con, customer_id),
    )


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def customer_edit(customer_id: int):
    con = get_db()
    customer = svc.get_customer(con, customer_id)
    if not customer:
        return render_template("not_found.html"), 404
    if request.method == "POST":
        if svc.update_customer(con, customer_id, request.form.to_dict()):
            flash("客戶資料已更新", "ok")
        else:
            flash("沒有任何欄位被更新", "error")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    return render_template("customer_form.html", customer=customer, form=customer)


@app.route("/customers/<int:customer_id>/stage", methods=["POST"])
def customer_stage(customer_id: int):
    con = get_db()
    if not svc.get_customer(con, customer_id):
        return render_template("not_found.html"), 404
    # 回傳值必須檢查——無效的 stage 會被 service 靜默跳過，
    # 若照樣顯示「已更新」，業務會以為階段推進了而漏跟客戶。
    if svc.update_customer(con, customer_id, {"stage": request.form.get("stage", "")}):
        flash("銷售階段已更新", "ok")
    else:
        flash("銷售階段未變更（選項無效）", "error")
    return redirect(url_for("customer_detail", customer_id=customer_id))


# ── 跟進待辦 ─────────────────────────────────────────────────────────────────

@app.route("/customers/<int:customer_id>/follow-ups", methods=["POST"])
def follow_up_add(customer_id: int):
    con = get_db()
    if not svc.get_customer(con, customer_id):
        return render_template("not_found.html"), 404
    try:
        svc.add_follow_up(con, customer_id,
                          request.form.get("due_date", ""),
                          request.form.get("content", ""))
        flash("待辦已新增", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("customer_detail", customer_id=customer_id))


@app.route("/follow-ups/<int:follow_up_id>/done", methods=["POST"])
def follow_up_done(follow_up_id: int):
    if svc.complete_follow_up(get_db(), follow_up_id):
        flash("待辦已完成", "ok")
    else:
        flash("找不到這筆待辦，或已完成", "error")
    return redirect(_safe_next(request.form.get("next")))


# ── 試乘 / 報價 / 舊車估價 ───────────────────────────────────────────────────

@app.route("/customers/<int:customer_id>/test-drives", methods=["POST"])
def test_drive_add(customer_id: int):
    con = get_db()
    if not svc.get_customer(con, customer_id):
        return render_template("not_found.html"), 404
    try:
        svc.add_test_drive(con, customer_id, request.form.to_dict())
        flash("試乘紀錄已新增", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("customer_detail", customer_id=customer_id))


@app.route("/customers/<int:customer_id>/quotes", methods=["POST"])
def quote_add(customer_id: int):
    con = get_db()
    if not svc.get_customer(con, customer_id):
        return render_template("not_found.html"), 404
    svc.add_quote(con, customer_id, request.form.to_dict())
    flash("報價已建立新版本", "ok")
    return redirect(url_for("customer_detail", customer_id=customer_id))


@app.route("/customers/<int:customer_id>/trade-ins", methods=["POST"])
def trade_in_add(customer_id: int):
    con = get_db()
    if not svc.get_customer(con, customer_id):
        return render_template("not_found.html"), 404
    svc.add_trade_in(con, customer_id, request.form.to_dict())
    flash("舊車估價已新增", "ok")
    return redirect(url_for("customer_detail", customer_id=customer_id))


# ── PWA ──────────────────────────────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return send_from_directory(app.static_folder, "manifest.json",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    # Service worker 必須由根路徑提供，否則作用範圍受限於 /static/
    response = send_from_directory(app.static_folder, "sw.js",
                                   mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.errorhandler(404)
def not_found(_exc):
    return render_template("not_found.html"), 404


def create_app(db_path: str | None = None, access_code: str | None = None) -> Flask:
    """供測試使用：以指定 DB 與存取碼設定 app。"""
    if db_path:
        app.config["DB_PATH"] = db_path
    if access_code is not None:
        app.config["ACCESS_CODE"] = access_code
    init_db(app.config["DB_PATH"])
    return app


def run_server() -> None:
    """啟動開發伺服器。

    未設定 CAR_CRM_ACCESS_CODE 時只綁 127.0.0.1——本系統存放真實客戶
    姓名與手機，不應在沒有任何存取控制的情況下對整個區網開放。
    要讓手機連線，請設定存取碼，讓「對外開放」成為明確的選擇而非預設。
    """
    init_db(app.config["DB_PATH"])
    port = int(os.environ.get("CAR_CRM_PORT", 5001))
    if _access_required():
        host = "0.0.0.0"          # noqa: S104 - 已有存取碼保護，供手機連線
        print(f"汽車業務 CRM 啟動 → http://localhost:{port}")
        print(f"手機請用電腦區網 IP：http://<電腦IP>:{port}")
        print("已啟用存取碼保護。")
    else:
        host = "127.0.0.1"
        print(f"汽車業務 CRM 啟動 → http://localhost:{port}")
        print("⚠ 未設定 CAR_CRM_ACCESS_CODE，僅本機可連線（手機無法存取）。")
        print("  要用手機操作請設定存取碼：")
        print(f"  CAR_CRM_ACCESS_CODE=你的密碼 CAR_CRM_PORT={port} python app.py")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_server()
