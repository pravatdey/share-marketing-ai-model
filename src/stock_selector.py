"""
Stock Selector – picks and ranks the best stocks to trade today.

Selection criteria (evaluated once after market open):
  1. Price affordable with our leveraged capital
  2. Highest intraday ATR% (most volatile → easiest to hit ₹50 target)
  3. High relative volume (active, liquid market)

The selector returns a ranked list of all candidates so that the main loop
can monitor all of them simultaneously and switch instantly when one stock
gives a signal while others are quiet.
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

    def get_ranked_stocks(self, top_n: int = cfg.TOP_N_STOCKS) -> list[dict]:
        """
        Analyse all candidates in INSTRUMENT_KEYS and return the top_n
        ranked by ATR% (volatility), each as:
          { instrument_key, ltp, atr, atr_pct, vol_ma }

        Returns an empty list if none are suitable.
        """
        logger.info("Screening stocks for today's trade …")
        candidates = self._md.get_top_volatile_stocks(
            cfg.INSTRUMENT_KEYS, top_n=top_n
        )

        if not candidates:
            logger.warning("No suitable stocks found after screening.")
            return []

        logger.info("Top %d stocks selected for monitoring:", len(candidates))
        for rank, stock in enumerate(candidates, start=1):
            logger.info(
                "  #%d %s | LTP=%.2f | ATR=%.2f (%.3f%%) | VolMA=%.0f",
                rank,
                stock["instrument_key"],
                stock["ltp"],
                stock["atr"],
                stock["atr_pct"],
                stock["vol_ma"],
            )

        return candidates

    def affordable_check(self, ltp: float) -> bool:
        """Return True if we can buy at least 1 share with our capital."""
        return ltp <= cfg.EFFECTIVE_CAPITAL
