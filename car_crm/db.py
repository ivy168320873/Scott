"""汽車業務 CRM 的資料層。

使用標準函式庫的 sqlite3，不引入 ORM——本系統的查詢單純，
且與上層 Scott 專案一致（`app.py` 也直接用 sqlite3）。

所有時間一律以台北時區（Asia/Taipei）的日期字串儲存，
避免業務在手機上看到的「今日待辦」與伺服器 UTC 日期不一致。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

# 台北時區（UTC+8，無日光節約時間，可用固定偏移）
TAIPEI_TZ = timezone(timedelta(hours=8))

DEFAULT_DB_PATH = os.environ.get("CAR_CRM_DB", "./car_crm.db")

# ── 銷售階段 ─────────────────────────────────────────────────────────────────
# 依實際汽車銷售流程排序；WON／LOST 為終點狀態。
STAGES: list[tuple[str, str]] = [
    ("NEW",         "新客戶"),
    ("CONTACTED",   "已聯繫"),
    ("TEST_DRIVE",  "已試乘"),
    ("QUOTED",      "已報價"),
    ("NEGOTIATING", "議價中"),
    ("WON",         "已成交"),
    ("LOST",        "已戰敗"),
]
STAGE_KEYS = [key for key, _ in STAGES]
STAGE_LABELS = dict(STAGES)
OPEN_STAGES = [key for key in STAGE_KEYS if key not in ("WON", "LOST")]

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT    NOT NULL DEFAULT '',
    email       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',   -- 來源：來店/網路/介紹/車展…
    stage       TEXT    NOT NULL DEFAULT 'NEW',
    note        TEXT    NOT NULL DEFAULT '',
    -- 購車需求
    want_model    TEXT NOT NULL DEFAULT '',
    want_trim     TEXT NOT NULL DEFAULT '',
    want_color    TEXT NOT NULL DEFAULT '',
    budget_min    REAL,
    budget_max    REAL,
    buy_timing    TEXT NOT NULL DEFAULT '',    -- 一個月內/三個月內/半年內/觀望
    payment_type  TEXT NOT NULL DEFAULT '',    -- 現金/貸款/租賃
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS follow_ups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    due_date    TEXT    NOT NULL,              -- YYYY-MM-DD
    content     TEXT    NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    done_at     TEXT,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS test_drives (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    drive_date  TEXT    NOT NULL,
    model       TEXT    NOT NULL DEFAULT '',
    rating      INTEGER,                        -- 1-5 滿意度
    feedback    TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,              -- 同一客戶的報價版本，從 1 遞增
    model        TEXT    NOT NULL DEFAULT '',
    list_price   REAL    NOT NULL DEFAULT 0,
    discount     REAL    NOT NULL DEFAULT 0,
    accessories  REAL    NOT NULL DEFAULT 0,
    final_price  REAL    NOT NULL DEFAULT 0,
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    -- 同一客戶的版本號不得重複。並行送出時由 DB 擋下，
    -- 應用層再重試，避免出現兩筆 v1 而「最新報價」標錯版本。
    UNIQUE (customer_id, version)
);

CREATE TABLE IF NOT EXISTS trade_ins (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    brand        TEXT    NOT NULL DEFAULT '',
    model        TEXT    NOT NULL DEFAULT '',
    year         INTEGER,
    mileage      INTEGER,
    condition    TEXT    NOT NULL DEFAULT '',   -- 優/良/普通/待整理
    estimated    REAL    NOT NULL DEFAULT 0,    -- 估價金額
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customers_stage    ON customers(stage);
CREATE INDEX IF NOT EXISTS idx_followups_customer ON follow_ups(customer_id);
CREATE INDEX IF NOT EXISTS idx_followups_due      ON follow_ups(due_date, done);
CREATE INDEX IF NOT EXISTS idx_testdrives_cust    ON test_drives(customer_id);
CREATE INDEX IF NOT EXISTS idx_quotes_cust        ON quotes(customer_id);
CREATE INDEX IF NOT EXISTS idx_tradeins_cust      ON trade_ins(customer_id);
"""


def now_iso() -> str:
    """目前時間（台北時區）的 ISO 字串。"""
    return datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")


def today_str() -> str:
    """今天日期（台北時區）YYYY-MM-DD。

    刻意不用 date.today()——伺服器可能跑在 UTC，會讓台灣早上 8 點前
    的「今日待辦」算成昨天。
    """
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """建立連線並啟用 Row factory 與外鍵約束。"""
    path = db_path or DEFAULT_DB_PATH
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(db_path: str | None = None) -> None:
    """建立資料表（可重複執行）。"""
    con = connect(db_path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()
