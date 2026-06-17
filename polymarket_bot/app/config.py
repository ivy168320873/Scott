"""Central configuration loaded from environment / .env file.

All tunables live here so signals are reproducible: a backtest can reload the
exact parameter set that produced a historical signal.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Safety -------------------------------------------------------------
    live_trading_enabled: bool = Field(False, alias="LIVE_TRADING_ENABLED")

    # --- Database -----------------------------------------------------------
    database_url: str = Field("postgresql://postgres:postgres@localhost:5432/polymarket_bot", alias="DATABASE_URL")

    # --- Polymarket ---------------------------------------------------------
    clob_rest_url: str = Field("https://clob.polymarket.com", alias="CLOB_REST_URL")
    clob_ws_url: str = Field("wss://ws-subscribe-clob.polymarket.com/ws/market", alias="CLOB_WS_URL")
    gamma_api_url: str = Field("https://gamma-api.polymarket.com", alias="GAMMA_API_URL")
    # Stored as raw CSV strings: pydantic-settings JSON-decodes List-typed env
    # vars, which would reject a plain comma-separated value. We split manually
    # via the properties below.
    tracked_markets_raw: str = Field("", alias="TRACKED_MARKETS")
    auto_track_top_n: int = Field(10, alias="AUTO_TRACK_TOP_N")

    # --- News ---------------------------------------------------------------
    news_rss_feeds_raw: str = Field("", alias="NEWS_RSS_FEEDS")
    news_poll_seconds: int = Field(30, alias="NEWS_POLL_SECONDS")

    # --- LLM ----------------------------------------------------------------
    llm_provider: str = Field("stub", alias="LLM_PROVIDER")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-opus-4-8", alias="ANTHROPIC_MODEL")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")

    # --- Fair value ---------------------------------------------------------
    fair_alpha: float = Field(0.15, alias="FAIR_ALPHA")
    fair_lambda: float = Field(0.02, alias="FAIR_LAMBDA")

    # --- Signal -------------------------------------------------------------
    minimum_edge: float = Field(0.03, alias="MINIMUM_EDGE")
    confidence_threshold: float = Field(0.6, alias="CONFIDENCE_THRESHOLD")

    # --- Paper trading ------------------------------------------------------
    paper_starting_cash: float = Field(10_000, alias="PAPER_STARTING_CASH")
    paper_slippage_bps: float = Field(20, alias="PAPER_SLIPPAGE_BPS")
    paper_fee_bps: float = Field(0, alias="PAPER_FEE_BPS")
    paper_order_size_usd: float = Field(100, alias="PAPER_ORDER_SIZE_USD")

    # --- Risk ---------------------------------------------------------------
    max_risk_per_trade_usd: float = Field(100, alias="MAX_RISK_PER_TRADE_USD")
    max_market_exposure_usd: float = Field(400, alias="MAX_MARKET_EXPOSURE_USD")
    max_daily_loss_usd: float = Field(500, alias="MAX_DAILY_LOSS_USD")
    max_holding_seconds: float = Field(90, alias="MAX_HOLDING_SECONDS")

    # --- Server -------------------------------------------------------------
    host: str = Field("0.0.0.0", alias="HOST")
    port: int = Field(8000, alias="PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @staticmethod
    def _split_csv(raw: str) -> List[str]:
        return [item.strip() for item in (raw or "").split(",") if item.strip()]

    @property
    def tracked_markets(self) -> List[str]:
        return self._split_csv(self.tracked_markets_raw)

    @property
    def news_rss_feeds(self) -> List[str]:
        return self._split_csv(self.news_rss_feeds_raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
