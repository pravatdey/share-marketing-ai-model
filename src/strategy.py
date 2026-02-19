"""
Trading Strategy Module – Opening Range Breakout (ORB) with EMA + RSI confirmation.

Logic:
  1. Observe the first 3 candles (9:15–9:30 AM) to establish OR High and OR Low.
  2. After 9:30 AM, look for a BUY signal:
       - Current close > OR High  (bullish breakout)
       - EMA(9) > EMA(21)         (uptrend confirmed)
       - RSI between 45 and 70    (not overbought; has momentum)
       - Volume > 1.5× 10-period average volume  (genuine breakout volume)
  3. Once in a position, manage with:
       - Stop loss  : entry_price × (1 – STOP_LOSS_PCT)
       - Take profit: entry_price × (1 + TARGET_PCT)
  4. Exit signal (to call from order manager):
       - Current price <= stop_loss  → stop-loss exit
       - Current price >= target     → profit target exit
       - Time >= FORCE_EXIT_TIME     → forced time-based exit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

import config.settings as cfg

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    action: str          # "BUY" | "SELL" | "HOLD" | "EXIT"
    instrument_key: str
    reason: str
    ltp: float           # Last traded price at signal time
    quantity: int        # Suggested order quantity (0 if HOLD)
    stop_loss: float = 0.0
    target: float    = 0.0


@dataclass
class Position:
    instrument_key: str
    entry_price: float
    quantity: int
    stop_loss: float
    target: float
    entry_time: datetime = field(default_factory=datetime.now)
    side: str = "BUY"    # Only BUY (long) trades – no shorting

    @property
    def position_value(self) -> float:
        return self.entry_price * self.quantity

    def unrealised_pnl(self, ltp: float) -> float:
        if self.side == "BUY":
            return (ltp - self.entry_price) * self.quantity
        return (self.entry_price - ltp) * self.quantity


class ORBStrategy:
    """
    Opening Range Breakout strategy with EMA + RSI confirmation.
    One instance per stock being monitored.
    """

    def __init__(self, instrument_key: str):
        self.instrument_key  = instrument_key
        self.or_high: Optional[float] = None
        self.or_low:  Optional[float] = None
        self.or_established = False
        self.position: Optional[Position] = None

    # ── Opening Range Setup ───────────────────────────────────────────────────

    def set_opening_range(self, or_high: float, or_low: float) -> None:
        self.or_high        = or_high
        self.or_low         = or_low
        self.or_established = True
        logger.info(
            "[%s] Opening Range set → High: %.2f | Low: %.2f | Range: %.2f",
            self.instrument_key, or_high, or_low, or_high - or_low,
        )

    # ── Signal Generation ─────────────────────────────────────────────────────

    def generate_signal(self, df: pd.DataFrame, available_capital: float) -> Signal:
        """
        Evaluate the latest candle data and return a trading Signal.

        Args:
            df: enriched candle DataFrame (must have ema_9, ema_21, rsi, vol_ma columns)
            available_capital: remaining usable capital (after positions)
        """
        instrument_key = self.instrument_key
        hold = Signal("HOLD", instrument_key, "no action", 0, 0)

        if df.empty or len(df) < cfg.EMA_SLOW + 5:
            return hold

        latest = df.iloc[-1]
        close   = float(latest["close"])
        ema9    = float(latest.get(f"ema_{cfg.EMA_FAST}", 0) or 0)
        ema21   = float(latest.get(f"ema_{cfg.EMA_SLOW}", 0) or 0)
        rsi     = float(latest.get("rsi", 50) or 50)
        volume  = float(latest.get("volume", 0) or 0)
        vol_ma  = float(latest.get("vol_ma", 1) or 1)

        # ── Manage existing position ──────────────────────────────────────────
        if self.position:
            p   = self.position
            pnl = p.unrealised_pnl(close)

            if close <= p.stop_loss:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Stop-loss hit @ {close:.2f} (entry {p.entry_price:.2f}, loss {pnl:.2f})",
                    close, p.quantity,
                )

            if close >= p.target:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Target hit @ {close:.2f} (entry {p.entry_price:.2f}, profit {pnl:.2f})",
                    close, p.quantity,
                )

            # EMA reversal exit (trend turned against us)
            if ema9 < ema21 and rsi < 40:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"EMA reversal exit @ {close:.2f} (ema9={ema9:.2f} < ema21={ema21:.2f})",
                    close, p.quantity,
                )

            return Signal(
                "HOLD", instrument_key,
                f"In position | LTP={close:.2f} | P&L={pnl:.2f}",
                close, 0,
            )

        # ── Look for BUY entry (no open position) ─────────────────────────────
        if not self.or_established:
            return hold

        breakout_up = close > self.or_high
        ema_uptrend = ema9 > ema21
        rsi_ok      = cfg.RSI_BUY_MIN <= rsi <= cfg.RSI_BUY_MAX
        vol_ok      = volume >= vol_ma * cfg.VOLUME_MULTIPLIER

        logger.debug(
            "[%s] Signal check → close=%.2f | OR_H=%.2f | breakout=%s | "
            "ema_up=%s | rsi=%.1f | vol_ok=%s",
            instrument_key, close, self.or_high or 0,
            breakout_up, ema_uptrend, rsi, vol_ok,
        )

        if breakout_up and ema_uptrend and rsi_ok and vol_ok:
            qty = _calc_quantity(close, available_capital)
            if qty < 1:
                return Signal(
                    "HOLD", instrument_key,
                    f"Signal valid but insufficient capital ({available_capital:.0f}) for {close:.2f}",
                    close, 0,
                )

            stop_loss = round(close * (1 - cfg.STOP_LOSS_PCT), 2)
            target    = round(close * (1 + cfg.TARGET_PCT), 2)

            self.position = Position(
                instrument_key=instrument_key,
                entry_price=close,
                quantity=qty,
                stop_loss=stop_loss,
                target=target,
            )

            return Signal(
                "BUY", instrument_key,
                (
                    f"ORB breakout above {self.or_high:.2f} | "
                    f"EMA9={ema9:.2f} EMA21={ema21:.2f} | RSI={rsi:.1f}"
                ),
                close, qty, stop_loss, target,
            )

        return hold

    def force_exit_signal(self, ltp: float) -> Optional[Signal]:
        """Generate an EXIT signal regardless of P&L (used at 3:10 PM)."""
        if self.position:
            p   = self.position
            pnl = p.unrealised_pnl(ltp)
            self.position = None
            return Signal(
                "EXIT", self.instrument_key,
                f"Force exit at market close @ {ltp:.2f} | P&L={pnl:.2f}",
                ltp, p.quantity,
            )
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _calc_quantity(price: float, available_capital: float) -> int:
    """
    Calculate how many shares to buy so that the risk (RISK_PER_TRADE)
    maps to the STOP_LOSS_PCT move below entry.

    Risk per trade = qty × price × STOP_LOSS_PCT
    → qty = RISK_PER_TRADE / (price × STOP_LOSS_PCT)

    Also capped by available_capital / price.
    """
    if price <= 0:
        return 0

    qty_by_risk    = int(cfg.RISK_PER_TRADE / (price * cfg.STOP_LOSS_PCT))
    qty_by_capital = int(available_capital / price)
    qty            = min(qty_by_risk, qty_by_capital)

    return max(qty, 0)
