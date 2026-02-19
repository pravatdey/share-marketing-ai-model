"""
Stock Selector – picks the best Nifty 50 stock to trade today.

Selection criteria (evaluated once after the Opening Range is established):
  1. Price affordable with our leveraged capital
  2. Highest intraday ATR% (most volatile → easiest to hit ₹50 target)
  3. Strong upward pre-market momentum (open > previous close)
  4. High relative volume (active traders)
"""

from __future__ import annotations

import logging
from typing import Optional

import config.settings as cfg
from src.market_data import MarketData

logger = logging.getLogger(__name__)


class StockSelector:
    def __init__(self, market_data: MarketData):
        self._md = market_data

    def select_stock(self, top_n: int = 5) -> Optional[dict]:
        """
        Analyse all candidates in NIFTY50_INSTRUMENT_KEYS and return
        the single best candidate as a dict:
          { instrument_key, ltp, atr, atr_pct, vol_ma }

        Returns None if no suitable candidate is found.
        """
        logger.info("Screening Nifty 50 stocks for today's trade …")
        candidates = self._md.get_top_volatile_stocks(
            cfg.NIFTY50_INSTRUMENT_KEYS, top_n=top_n
        )

        if not candidates:
            logger.warning("No suitable stocks found after screening.")
            return None

        best = candidates[0]
        logger.info(
            "Selected stock → %s | LTP=%.2f | ATR=%.2f (%.3f%%) | VolMA=%.0f",
            best["instrument_key"], best["ltp"],
            best["atr"], best["atr_pct"], best["vol_ma"],
        )
        return best

    def affordable_check(self, ltp: float) -> bool:
        """Return True if we can buy at least 1 share with our capital."""
        return ltp <= cfg.EFFECTIVE_CAPITAL
