"""汽車業務 CRM 的商業邏輯。

與路由分離，讓所有規則（熱門客戶評分、逾期判定、報價版本遞增）
都能在不啟動 HTTP 的情況下被測試。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from db import OPEN_STAGES, STAGE_KEYS, now_iso, today_str

# 熱門客戶評分權重
_STAGE_SCORE = {
    "NEW": 5, "CONTACTED": 15, "TEST_DRIVE": 35,
    "QUOTED": 45, "NEGOTIATING": 60, "WON": 0, "LOST": 0,
}
_TIMING_SCORE = {"一個月內": 25, "三個月內": 15, "半年內": 5, "觀望": 0}


def _to_float(value) -> float | None:
    """把表單字串轉成 float；空值、非數字、NaN、無限大一律回 None。

    必須用 isfinite 而非 `x == x`——後者只排除 NaN，`inf` 會通過，
    再交給 int() 就會拋 OverflowError 讓整個請求 500。
    """
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _to_int(value) -> int | None:
    result = _to_float(value)
    return int(result) if result is not None else None


def _to_date(value, *, field: str) -> str:
    """驗證並正規化 YYYY-MM-DD 日期。

    不可默默改成今天——待辦日期若被靜默竄改，業務會在錯誤的日子看到提醒。
    格式錯誤直接拋 ValueError，由路由層回報給使用者。
    """
    text = (value or "").strip()
    if not text:
        return today_str()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field}格式須為 YYYY-MM-DD（收到「{text}」）") from exc


# ── 客戶 ─────────────────────────────────────────────────────────────────────

def create_customer(con: sqlite3.Connection, data: dict) -> int:
    """建立客戶。name 為必填，其餘欄位缺漏時以空字串／None 收斂。"""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("客戶姓名為必填")

    stage = data.get("stage") or "NEW"
    if stage not in STAGE_KEYS:
        stage = "NEW"

    ts = now_iso()
    cur = con.execute(
        """INSERT INTO customers
           (name, phone, email, source, stage, note,
            want_model, want_trim, want_color, budget_min, budget_max,
            buy_timing, payment_type, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name,
            (data.get("phone") or "").strip(),
            (data.get("email") or "").strip(),
            (data.get("source") or "").strip(),
            stage,
            (data.get("note") or "").strip(),
            (data.get("want_model") or "").strip(),
            (data.get("want_trim") or "").strip(),
            (data.get("want_color") or "").strip(),
            _to_float(data.get("budget_min")),
            _to_float(data.get("budget_max")),
            (data.get("buy_timing") or "").strip(),
            (data.get("payment_type") or "").strip(),
            ts, ts,
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def update_customer(con: sqlite3.Connection, customer_id: int, data: dict) -> bool:
    """更新客戶欄位。只更新有出現在 data 裡的欄位。"""
    if not get_customer(con, customer_id):
        return False

    editable = {
        "name": str, "phone": str, "email": str, "source": str, "note": str,
        "want_model": str, "want_trim": str, "want_color": str,
        "buy_timing": str, "payment_type": str,
        "budget_min": float, "budget_max": float, "stage": str,
    }
    sets, values = [], []
    for field, kind in editable.items():
        if field not in data:
            continue
        raw = data[field]
        if field == "stage":
            if raw not in STAGE_KEYS:
                continue
            value = raw
        elif kind is float:
            value = _to_float(raw)
        else:
            value = (raw or "").strip()
            if field == "name" and not value:
                continue                       # 不允許把姓名清空
        sets.append(f"{field} = ?")
        values.append(value)

    if not sets:
        return False
    sets.append("updated_at = ?")
    values.extend([now_iso(), customer_id])
    con.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id = ?", values)
    con.commit()
    return True


def get_customer(con: sqlite3.Connection, customer_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()


def list_customers(con: sqlite3.Connection, *, stage: str | None = None,
                   keyword: str = "") -> list[sqlite3.Row]:
    sql = "SELECT * FROM customers"
    where, params = [], []
    if stage in STAGE_KEYS:
        where.append("stage = ?")
        params.append(stage)
    if keyword.strip():
        # 跳脫 LIKE 萬用字元，否則搜尋 "%" 或 "_" 會回傳全部客戶
        escaped = (keyword.strip()
                   .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
        where.append("(name LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\' "
                     "OR want_model LIKE ? ESCAPE '\\')")
        like = f"%{escaped}%"
        params.extend([like, like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC"
    return con.execute(sql, params).fetchall()


# ── 跟進待辦 ─────────────────────────────────────────────────────────────────

def add_follow_up(con: sqlite3.Connection, customer_id: int,
                  due_date: str, content: str) -> int:
    content = (content or "").strip()
    if not content:
        raise ValueError("跟進內容為必填")
    due = _to_date(due_date, field="預定日期")
    cur = con.execute(
        """INSERT INTO follow_ups (customer_id, due_date, content, done, created_at)
           VALUES (?,?,?,0,?)""",
        (customer_id, due, content, now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def complete_follow_up(con: sqlite3.Connection, follow_up_id: int) -> bool:
    """完成待辦。成功時把客戶推進到「已聯繫」——打過電話就是聯繫過了。"""
    row = con.execute(
        "SELECT customer_id FROM follow_ups WHERE id = ? AND done = 0", (follow_up_id,)
    ).fetchone()
    if not row:
        return False
    con.execute(
        "UPDATE follow_ups SET done = 1, done_at = ? WHERE id = ?",
        (now_iso(), follow_up_id),
    )
    _advance_stage(con, row["customer_id"], "CONTACTED")
    con.commit()
    return True


def due_follow_ups(con: sqlite3.Connection) -> dict:
    """今日與逾期的未完成待辦。

    逾期＝due_date < 今天且尚未完成；今日＝due_date == 今天。
    兩者分開回傳，讓首頁可以優先突顯逾期。
    """
    today = today_str()
    rows = con.execute(
        """SELECT f.*, c.name AS customer_name, c.phone AS customer_phone,
                  c.stage AS customer_stage
           FROM follow_ups f JOIN customers c ON c.id = f.customer_id
           WHERE f.done = 0 AND f.due_date <= ?
           ORDER BY f.due_date ASC, f.id ASC""",
        (today,),
    ).fetchall()
    return {
        "overdue": [r for r in rows if r["due_date"] < today],
        "today":   [r for r in rows if r["due_date"] == today],
    }


def upcoming_follow_ups(con: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT f.*, c.name AS customer_name
           FROM follow_ups f JOIN customers c ON c.id = f.customer_id
           WHERE f.done = 0 AND f.due_date > ?
           ORDER BY f.due_date ASC LIMIT ?""",
        (today_str(), limit),
    ).fetchall()


def customer_follow_ups(con: sqlite3.Connection, customer_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM follow_ups WHERE customer_id = ? ORDER BY done ASC, due_date ASC",
        (customer_id,),
    ).fetchall()


# ── 試乘 ─────────────────────────────────────────────────────────────────────

def add_test_drive(con: sqlite3.Connection, customer_id: int, data: dict) -> int:
    rating = _to_int(data.get("rating"))
    if rating is not None:
        rating = max(1, min(5, rating))          # 夾在 1-5
    cur = con.execute(
        """INSERT INTO test_drives (customer_id, drive_date, model, rating, feedback, created_at)
           VALUES (?,?,?,?,?,?)""",
        (
            customer_id,
            _to_date(data.get("drive_date"), field="試乘日期"),
            (data.get("model") or "").strip(),
            rating,
            (data.get("feedback") or "").strip(),
            now_iso(),
        ),
    )
    # 試乘後階段自動推進（不倒退已成交／議價中的客戶）
    _advance_stage(con, customer_id, "TEST_DRIVE")
    con.commit()
    return int(cur.lastrowid)


def customer_test_drives(con: sqlite3.Connection, customer_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM test_drives WHERE customer_id = ? ORDER BY drive_date DESC, id DESC",
        (customer_id,),
    ).fetchall()


# ── 報價 ─────────────────────────────────────────────────────────────────────

def add_quote(con: sqlite3.Connection, customer_id: int, data: dict) -> int:
    """新增報價，版本號自動遞增（同客戶保留完整歷史，不覆蓋舊版）。

    版本號由 DB 在 INSERT 當下計算，而非先 SELECT MAX 再 INSERT——
    後者在並行送出時（手機訊號不穩重複點擊很常見）會產生多筆 v1，
    導致「最新」標籤貼錯版本，業務照著畫面報出錯誤價格。
    """
    list_price  = _to_float(data.get("list_price"))  or 0.0
    discount    = _to_float(data.get("discount"))    or 0.0
    accessories = _to_float(data.get("accessories")) or 0.0
    final_price = _to_float(data.get("final_price"))
    if final_price is None:
        final_price = list_price - discount + accessories

    params = (
        customer_id,
        (data.get("model") or "").strip(),
        list_price, discount, accessories, final_price,
        (data.get("note") or "").strip(),
        now_iso(),
        customer_id,
    )
    sql = """INSERT INTO quotes
             (customer_id, version, model, list_price, discount, accessories,
              final_price, note, created_at)
             SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ?, ?, ?, ?, ?, ?
             FROM quotes WHERE customer_id = ?"""

    for attempt in range(3):
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(sql, params)
            _advance_stage(con, customer_id, "QUOTED")
            con.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            # UNIQUE(customer_id, version) 擋下並行插入 → 重試取新版本號
            con.rollback()
            if attempt == 2:
                raise
    raise RuntimeError("報價建立失敗")   # pragma: no cover - 迴圈必定 return 或 raise


def customer_quotes(con: sqlite3.Connection, customer_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM quotes WHERE customer_id = ? ORDER BY version DESC",
        (customer_id,),
    ).fetchall()


# ── 舊車估價 ─────────────────────────────────────────────────────────────────

def add_trade_in(con: sqlite3.Connection, customer_id: int, data: dict) -> int:
    cur = con.execute(
        """INSERT INTO trade_ins
           (customer_id, brand, model, year, mileage, condition, estimated, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            customer_id,
            (data.get("brand") or "").strip(),
            (data.get("model") or "").strip(),
            _to_int(data.get("year")),
            _to_int(data.get("mileage")),
            (data.get("condition") or "").strip(),
            _to_float(data.get("estimated")) or 0.0,
            (data.get("note") or "").strip(),
            now_iso(),
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def customer_trade_ins(con: sqlite3.Connection, customer_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM trade_ins WHERE customer_id = ? ORDER BY id DESC",
        (customer_id,),
    ).fetchall()


# ── 階段推進 ─────────────────────────────────────────────────────────────────

def _advance_stage(con: sqlite3.Connection, customer_id: int, target: str) -> None:
    """只往前推進，不倒退。

    已成交／已戰敗的客戶不再被自動改動——那需要業務手動決定。
    """
    row = get_customer(con, customer_id)
    if not row:
        return
    current = row["stage"]
    if current in ("WON", "LOST"):
        return
    if STAGE_KEYS.index(target) > STAGE_KEYS.index(current):
        con.execute(
            "UPDATE customers SET stage = ?, updated_at = ? WHERE id = ?",
            (target, now_iso(), customer_id),
        )


# ── 首頁儀表板 ───────────────────────────────────────────────────────────────

def hot_customers(con: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """熱門客戶：依銷售階段、購車時程、試乘與報價次數綜合評分。

    評分僅為「優先聯繫順序」的排序依據，不是成交機率預測——
    不得對外呈現成百分比機率。
    """
    placeholders = ",".join("?" * len(OPEN_STAGES))
    rows = con.execute(
        f"""SELECT c.*,
                   (SELECT COUNT(*) FROM test_drives t WHERE t.customer_id = c.id) AS drive_count,
                   (SELECT COUNT(*) FROM quotes q      WHERE q.customer_id = c.id) AS quote_count
            FROM customers c
            WHERE c.stage IN ({placeholders})""",
        OPEN_STAGES,
    ).fetchall()

    scored = []
    for row in rows:
        score = _STAGE_SCORE.get(row["stage"], 0)
        score += _TIMING_SCORE.get(row["buy_timing"], 0)
        score += min(row["drive_count"], 2) * 10
        score += min(row["quote_count"], 3) * 5
        scored.append({
            "customer": row,
            "score": min(score, 100),
            "drive_count": row["drive_count"],
            "quote_count": row["quote_count"],
        })
    scored.sort(key=lambda item: (-item["score"], item["customer"]["name"]))
    return scored[:limit]


def stage_funnel(con: sqlite3.Connection) -> list[dict]:
    """各階段客戶數，供首頁顯示成交進度。"""
    counts = {key: 0 for key in STAGE_KEYS}
    for row in con.execute("SELECT stage, COUNT(*) AS n FROM customers GROUP BY stage"):
        if row["stage"] in counts:
            counts[row["stage"]] = row["n"]
    return [{"key": key, "count": counts[key]} for key in STAGE_KEYS]


def dashboard(con: sqlite3.Connection) -> dict:
    """首頁資料：今天該聯絡誰、熱門客戶、成交進度。"""
    due = due_follow_ups(con)
    funnel = stage_funnel(con)
    total = sum(item["count"] for item in funnel)
    won = next((item["count"] for item in funnel if item["key"] == "WON"), 0)
    lost = next((item["count"] for item in funnel if item["key"] == "LOST"), 0)
    closed = won + lost
    return {
        "today": today_str(),
        "overdue": due["overdue"],
        "due_today": due["today"],
        "upcoming": upcoming_follow_ups(con),
        "hot": hot_customers(con),
        "funnel": funnel,
        "total_customers": total,
        "won_count": won,
        "lost_count": lost,
        # 成交率分母只算已結案的客戶，進行中的不列入，避免低估
        "win_rate": round(won / closed * 100, 1) if closed else None,
    }
