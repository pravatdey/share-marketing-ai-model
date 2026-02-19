"""
Risk Manager – tracks daily Profit & Loss (P&L), enforces:
  • Daily profit target  (₹50 → stop all new trades)
  • Daily max loss limit (₹50 → stop all new trades, protect capital)
  • Position sizing guard
"""

from __future__ import annotations

import logging
from datetime import date

import config.settings as cfg

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self.trade_date        = date.today()
        self.realised_pnl      = 0.0      # booked P&L from closed trades
        self.open_pnl          = 0.0      # current unrealised P&L
        self.trades: list[dict] = []      # log of completed trades
        self.total_trades      = 0
        self.winning_trades    = 0

    # ── P&L Tracking ─────────────────────────────────────────────────────────

    def record_trade(
        self,
        instrument_key: str,
        side: str,            # "BUY"
        entry_price: float,
        exit_price: float,
        quantity: int,
        reason: str,
    ) -> float:
        """Book a completed trade and return its P&L."""
        if side == "BUY":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        self.realised_pnl += pnl
        self.total_trades  += 1
        if pnl > 0:
            self.winning_trades += 1

        trade = {
            "instrument_key": instrument_key,
            "side":           side,
            "entry_price":    entry_price,
            "exit_price":     exit_price,
            "quantity":       quantity,
            "pnl":            round(pnl, 2),
            "reason":         reason,
        }
        self.trades.append(trade)

        logger.info(
            "Trade closed → %s %s × %d | entry=%.2f exit=%.2f | P&L=%.2f | "
            "Total P&L=%.2f",
            side, instrument_key, quantity,
            entry_price, exit_price, pnl, self.realised_pnl,
        )
        return pnl

    def update_open_pnl(self, pnl: float) -> None:
        """Update the unrealised P&L of open positions (called each poll cycle)."""
        self.open_pnl = pnl

    @property
    def total_pnl(self) -> float:
        return round(self.realised_pnl + self.open_pnl, 2)

    # ── Guard Checks ──────────────────────────────────────────────────────────

    def is_profit_target_hit(self) -> bool:
        """Return True if realised P&L >= daily profit target (₹50)."""
        hit = self.realised_pnl >= cfg.DAILY_PROFIT_TARGET
        if hit:
            logger.info(
                "*** PROFIT TARGET HIT *** Realised P&L=%.2f >= target=%.2f. "
                "No more new trades today.",
                self.realised_pnl, cfg.DAILY_PROFIT_TARGET,
            )
        return hit

    def is_max_loss_hit(self) -> bool:
        """Return True if total P&L (realised + unrealised) <= -(daily max loss)."""
        hit = self.total_pnl <= -cfg.DAILY_MAX_LOSS
        if hit:
            logger.warning(
                "*** MAX LOSS HIT *** Total P&L=%.2f <= -%.2f. "
                "Stopping all trading to protect capital.",
                self.total_pnl, cfg.DAILY_MAX_LOSS,
            )
        return hit

    def can_trade(self) -> bool:
        """Return True if neither the profit target nor max loss has been reached."""
        return not self.is_profit_target_hit() and not self.is_max_loss_hit()

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "date":            self.trade_date.isoformat(),
            "realised_pnl":    round(self.realised_pnl, 2),
            "open_pnl":        round(self.open_pnl, 2),
            "total_pnl":       self.total_pnl,
            "total_trades":    self.total_trades,
            "winning_trades":  self.winning_trades,
            "trades":          self.trades,
            "profit_hit":      self.is_profit_target_hit(),
            "loss_hit":        self.is_max_loss_hit(),
        }

    def summary_text(self) -> str:
        s = self.summary()
        lines = [
            f"Date           : {s['date']}",
            f"Realised P&L   : ₹{s['realised_pnl']:.2f}",
            f"Open P&L       : ₹{s['open_pnl']:.2f}",
            f"Total P&L      : ₹{s['total_pnl']:.2f}",
            f"Total Trades   : {s['total_trades']}",
            f"Winning Trades : {s['winning_trades']}",
            f"Profit Target  : {'HIT ✓' if s['profit_hit'] else 'Not yet'}",
            f"Max Loss       : {'HIT ✗' if s['loss_hit'] else 'Safe'}",
        ]
        if s["trades"]:
            lines.append("\nTrade Details:")
            for t in s["trades"]:
                lines.append(
                    f"  {t['side']} {t['instrument_key']} × {t['quantity']} | "
                    f"entry={t['entry_price']} exit={t['exit_price']} | "
                    f"P&L=₹{t['pnl']} | {t['reason']}"
                )
        return "\n".join(lines)
