-- ============================================================================
-- Polymarket Paper Trading Bot — PostgreSQL schema
-- Apply with:  psql "$DATABASE_URL" -f schema.sql
-- The application also auto-applies this on startup (see app/db.py).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- markets: tracked Polymarket CLOB markets and their outcome tokens.
-- A binary market has two tokens (YES / NO). We store one row per market and
-- keep both token_ids so we can value either side.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS markets (
    id              BIGSERIAL PRIMARY KEY,
    condition_id    TEXT UNIQUE NOT NULL,
    question        TEXT NOT NULL,
    slug            TEXT,
    yes_token_id    TEXT,
    no_token_id     TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    closed          BOOLEAN NOT NULL DEFAULT FALSE,
    end_date        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_markets_yes_token ON markets (yes_token_id);
CREATE INDEX IF NOT EXISTS idx_markets_active     ON markets (active) WHERE active;

-- ---------------------------------------------------------------------------
-- news_events: raw ingested news plus LLM classification output.
-- The classifier never decides position size — it only annotates importance,
-- the affected market, direction and the three scores.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_events (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL,
    external_id         TEXT NOT NULL,          -- dedup key (guid / link hash)
    title               TEXT NOT NULL,
    summary             TEXT,
    url                 TEXT,
    published_at        TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- classification (NULL until processed)
    classified          BOOLEAN NOT NULL DEFAULT FALSE,
    is_important        BOOLEAN,
    market_condition_id TEXT,                   -- affected market (FK-ish)
    direction           TEXT,                   -- 'YES' | 'NO' | 'NONE'
    relevance_score     DOUBLE PRECISION,       -- 0..1
    surprise_score      DOUBLE PRECISION,       -- 0..1
    confidence_score    DOUBLE PRECISION,       -- 0..1
    llm_provider        TEXT,
    llm_rationale       TEXT,                   -- human-readable explanation
    classified_at       TIMESTAMPTZ,

    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_news_unclassified ON news_events (classified) WHERE NOT classified;
CREATE INDEX IF NOT EXISTS idx_news_market       ON news_events (market_condition_id);

-- ---------------------------------------------------------------------------
-- orderbook_snapshots: periodic / event-driven top-of-book snapshots.
-- Used for backtesting and to reconstruct what the bot "saw" at signal time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    condition_id  TEXT NOT NULL,
    token_id      TEXT NOT NULL,
    best_bid      DOUBLE PRECISION,
    best_ask      DOUBLE PRECISION,
    mid_price     DOUBLE PRECISION,
    last_trade    DOUBLE PRECISION,
    bid_size      DOUBLE PRECISION,
    ask_size      DOUBLE PRECISION,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ob_token_time ON orderbook_snapshots (token_id, captured_at DESC);

-- ---------------------------------------------------------------------------
-- signals: every signal the engine produced, with full explainability.
-- Stored even when the risk manager vetoes them (status='rejected').
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL PRIMARY KEY,
    news_event_id   BIGINT REFERENCES news_events(id),
    condition_id    TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    side            TEXT NOT NULL,              -- 'BUY_YES' | 'SELL_YES'
    mid_price       DOUBLE PRECISION,
    best_bid        DOUBLE PRECISION,
    best_ask        DOUBLE PRECISION,
    fair_price      DOUBLE PRECISION,
    news_score      DOUBLE PRECISION,
    edge            DOUBLE PRECISION,
    confidence      DOUBLE PRECISION,
    status          TEXT NOT NULL DEFAULT 'generated', -- generated|rejected|executed
    reject_reason   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_signals_time ON signals (created_at DESC);

-- ---------------------------------------------------------------------------
-- paper_trades: simulated fills. No real orders are ever placed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_trades (
    id                BIGSERIAL PRIMARY KEY,
    signal_id         BIGINT REFERENCES signals(id),
    condition_id      TEXT NOT NULL,
    token_id          TEXT NOT NULL,
    side              TEXT NOT NULL,            -- 'BUY_YES' | 'SELL_YES'
    size_usd          DOUBLE PRECISION NOT NULL,
    shares            DOUBLE PRECISION NOT NULL,

    entry_price       DOUBLE PRECISION NOT NULL,  -- simulated fill incl. slippage
    entry_fee         DOUBLE PRECISION NOT NULL DEFAULT 0,
    entry_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    exit_price        DOUBLE PRECISION,
    exit_fee          DOUBLE PRECISION DEFAULT 0,
    exit_at           TIMESTAMPTZ,
    exit_reason       TEXT,                       -- timeout|target|stop|market_close

    pnl               DOUBLE PRECISION,           -- realized, net of fees
    holding_seconds   DOUBLE PRECISION,
    status            TEXT NOT NULL DEFAULT 'open' -- open|closed
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON paper_trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_market ON paper_trades (condition_id);
CREATE INDEX IF NOT EXISTS idx_trades_time   ON paper_trades (entry_at DESC);
