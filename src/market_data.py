"""
Market Data Module – fetches OHLCV candles from Upstox API v2
and computes technical indicators (EMA, RSI, ATR, Volume MA).
"""

import logging
from datetime import datetime

import pandas as pd
import pandas_ta as ta
import requests

import config.settings as cfg

logger = logging.getLogger(__name__)


class MarketData:
    """Handles all market data fetching and indicator computation."""

    def __init__(self, access_token: str):
        self._token   = access_token
        self._headers = {
            "accept":        "application/json",
            "Authorization": f"Bearer {access_token}",
            "Api-Version":   "2.0",
        }

    # ── OHLCV Candles ────────────────────────────────────────────────────────

    def get_candles(
        self,
        instrument_key: str,
        interval: str = cfg.CANDLE_INTERVAL,
    ) -> pd.DataFrame:
        """
        Fetch today's intraday OHLCV candles from Upstox.

        Args:
            instrument_key: e.g. "NSE_EQ|INE040A01034"
            interval: "1minute" | "5minute" | "30minute" etc.

        Returns:
            DataFrame with columns: [datetime, open, high, low, close, volume]
            Sorted oldest → newest.
        """
        # The intraday endpoint returns today's candles automatically.
        # It does NOT accept from_date/to_date query parameters (causes 400).
        url = (
            f"{cfg.UPSTOX_BASE_URL}/historical-candle/intraday"
            f"/{instrument_key}/{interval}"
        )

        try:
            resp = requests.get(url, headers=self._headers, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Failed to fetch candles for %s: %s", instrument_key, exc)
            return pd.DataFrame()

        data = resp.json().get("data", {}).get("candles", [])
        if not data:
            logger.warning("No candle data returned for %s", instrument_key)
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=["datetime", "open", "high", "low", "close", "volume", "oi"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        df = df[["datetime", "open", "high", "low", "close", "volume"]]
        df[["open", "high", "low", "close", "volume"]] = df[
            ["open", "high", "low", "close", "volume"]
        ].apply(pd.to_numeric)
        return df

    # ── Live Quote ────────────────────────────────────────────────────────────

    def get_ltp(self, instrument_key: str) -> Optional[float]:
        """Return the Last Traded Price (LTP) for an instrument."""
        url = f"{cfg.UPSTOX_BASE_URL}/market-quote/ltp"
        params = {"instrument_key": instrument_key}
        try:
            resp = requests.get(url, headers=self._headers, params=params, timeout=10)
            resp.raise_for_status()
            ltp_data = resp.json().get("data", {})
            # Key format in response: "NSE_EQ:INFY" – just take first value
            for key, val in ltp_data.items():
                return float(val.get("last_price", 0))
        except Exception as exc:
            logger.error("LTP fetch failed for %s: %s", instrument_key, exc)
        return None

    def get_quote(self, instrument_key: str) -> dict:
        """Return full quote data including OHLC, volume, and LTP."""
        url = f"{cfg.UPSTOX_BASE_URL}/market-quote/quotes"
        params = {"instrument_key": instrument_key}
        try:
            resp = requests.get(url, headers=self._headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            for key, val in data.items():
                return val
        except Exception as exc:
            logger.error("Quote fetch failed for %s: %s", instrument_key, exc)
        return {}

    # ── Technical Indicators ─────────────────────────────────────────────────

    @staticmethod
    def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Add EMA(9), EMA(21), RSI(14), ATR(14), and Volume MA(10) to a candle DataFrame.
        Returns the enriched DataFrame (in-place modification).
        """
        if df.empty or len(df) < cfg.EMA_SLOW + 5:
            return df

        df[f"ema_{cfg.EMA_FAST}"] = ta.ema(df["close"], length=cfg.EMA_FAST)
        df[f"ema_{cfg.EMA_SLOW}"] = ta.ema(df["close"], length=cfg.EMA_SLOW)
        df["rsi"]    = ta.rsi(df["close"], length=cfg.RSI_PERIOD)
        df["atr"]    = ta.atr(df["high"], df["low"], df["close"], length=14)
        df["vol_ma"] = df["volume"].rolling(window=10).mean()
        return df

    # ── Opening Range ─────────────────────────────────────────────────────────

    @staticmethod
    def get_opening_range(df: pd.DataFrame, n_candles: int = cfg.ORB_CANDLES) -> dict:
        """
        Compute the Opening Range (OR) from the first `n_candles` 5-min candles.
        Returns {"or_high": float, "or_low": float, "or_range": float}
        """
        if df.empty or len(df) < n_candles:
            return {}

        or_df = df.iloc[:n_candles]
        or_high  = float(or_df["high"].max())
        or_low   = float(or_df["low"].min())
        or_range = round(or_high - or_low, 2)
        return {"or_high": or_high, "or_low": or_low, "or_range": or_range}

    # ── Stock Screening ───────────────────────────────────────────────────────

    def get_top_volatile_stocks(
        self, instrument_keys: list[str], top_n: int = 5
    ) -> list[dict]:
        """
        From the provided instrument_keys, return the top_n stocks ranked
        by today's intraday ATR (volatility).  Each entry includes:
          { instrument_key, ltp, atr, vol_ma }
        """
        candidates = []
        for key in instrument_keys:
            df = self.get_candles(key)
            if df.empty or len(df) < cfg.EMA_SLOW + 5:
                continue
            df = self.add_indicators(df)
            latest = df.iloc[-1]
            ltp    = latest["close"]
            atr    = latest.get("atr", 0) or 0
            vol_ma = latest.get("vol_ma", 1) or 1

            # Skip stocks where even the smallest lot (1 share) exceeds our capital
            max_affordable_price = cfg.EFFECTIVE_CAPITAL
            if ltp > max_affordable_price:
                continue

            candidates.append(
                {
                    "instrument_key": key,
                    "ltp":            round(ltp, 2),
                    "atr":            round(atr, 2),
                    "atr_pct":        round((atr / ltp) * 100, 3) if ltp else 0,
                    "vol_ma":         round(vol_ma, 0),
                }
            )

        # Sort by ATR% descending (most volatile first)
        candidates.sort(key=lambda x: x["atr_pct"], reverse=True)
        return candidates[:top_n]
