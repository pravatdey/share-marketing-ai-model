"""
Trading Strategy Module – Opening Range Breakout (ORB) with EMA + RSI + Volume confirmation.

Logic:
  1. Observe the first 3 candles (9:15–9:30 AM) to establish OR High and OR Low.
  2. After 9:30 AM, scan for signals every 5 minutes:

     BUY signal (bullish breakout):
       - close > OR High          (price breaks above opening range)
       - EMA(9) > EMA(21)         (uptrend confirmed)
       - RSI between 45 and 70    (momentum, not overbought)
       - volume >= 1.5× vol_ma    (genuine breakout volume)

     SELL/Short signal (bearish breakdown):
       - close < OR Low           (price breaks below opening range)
       - EMA(9) < EMA(21)         (downtrend confirmed)
       - RSI between 30 and 55    (weakness, not oversold)
       - volume >= 1.5× vol_ma    (genuine breakdown volume)

  3. Position management:
       - Stop loss  : 0.5% against entry
       - Take profit: 1.0% in favour  (2:1 reward:risk)
       - Force exit : 3:10 PM

Goal: ₹50 profit per day. Exit the moment target is hit.
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
    action: str          # "BUY" | "SHORT" | "HOLD" | "EXIT"
    instrument_key: str
    reason: str
    ltp: float           # Last traded price at signal time
    quantity: int        # Suggested order quantity (0 if HOLD)
    stop_loss: float = 0.0
    target: float    = 0.0
    side: str        = "BUY"   # "BUY" or "SHORT"


@dataclass
class Position:
    instrument_key: str
    entry_price: float
    quantity: int
    stop_loss: float
    target: float
    side: str = "BUY"          # "BUY" (long) or "SHORT" (short)
    entry_time: datetime = field(default_factory=datetime.now)

    @property
    def position_value(self) -> float:
        return self.entry_price * self.quantity

    def unrealised_pnl(self, ltp: float) -> float:
        if self.side == "BUY":
            return (ltp - self.entry_price) * self.quantity
        else:  # SHORT
            return (self.entry_price - ltp) * self.quantity


class ORBStrategy:
    """
    Opening Range Breakout strategy with EMA + RSI + Volume confirmation.
    Supports both BUY (long) and SHORT (sell) trades.
    One instance per stock being monitored.
    """

    def __init__(self, instrument_key: str):
        self.instrument_key   = instrument_key
        self.or_high: Optional[float] = None
        self.or_low:  Optional[float] = None
        self.or_established   = False
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
        Evaluate the latest candle and return a Signal.
        Checks for BUY (bullish breakout) and SHORT (bearish breakdown).
        """
        instrument_key = self.instrument_key
        hold = Signal("HOLD", instrument_key, "no action", 0, 0)

        if df.empty or len(df) < cfg.EMA_SLOW + 5:
            return hold

        latest  = df.iloc[-1]
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

            # Stop-loss hit
            if p.side == "BUY" and close <= p.stop_loss:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Stop-loss hit @ {close:.2f} (entry {p.entry_price:.2f}, loss ₹{pnl:.2f})",
                    close, p.quantity, side=p.side,
                )
            if p.side == "SHORT" and close >= p.stop_loss:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Stop-loss hit @ {close:.2f} (entry {p.entry_price:.2f}, loss ₹{pnl:.2f})",
                    close, p.quantity, side=p.side,
                )

            # Target hit
            if p.side == "BUY" and close >= p.target:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Target hit @ {close:.2f} (entry {p.entry_price:.2f}, profit ₹{pnl:.2f})",
                    close, p.quantity, side=p.side,
                )
            if p.side == "SHORT" and close <= p.target:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Target hit @ {close:.2f} (entry {p.entry_price:.2f}, profit ₹{pnl:.2f})",
                    close, p.quantity, side=p.side,
                )

            # EMA reversal exit (trend turned against us)
            if p.side == "BUY" and ema9 < ema21 and rsi < 40:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"EMA reversal exit @ {close:.2f} (ema9={ema9:.2f} < ema21={ema21:.2f})",
                    close, p.quantity, side=p.side,
                )
            if p.side == "SHORT" and ema9 > ema21 and rsi > 60:
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"EMA reversal exit @ {close:.2f} (ema9={ema9:.2f} > ema21={ema21:.2f})",
                    close, p.quantity, side=p.side,
                )

            return Signal(
                "HOLD", instrument_key,
                f"In {p.side} position | LTP={close:.2f} | P&L=₹{pnl:.2f}",
                close, 0, side=p.side,
            )

        # ── Look for new entry (no open position) ─────────────────────────────
        if not self.or_established:
            return hold

        vol_ok = volume >= vol_ma * cfg.VOLUME_MULTIPLIER

        # ── BUY: bullish breakout above OR High ───────────────────────────────
        breakout_up  = close > (self.or_high or 0)
        ema_uptrend  = ema9 > ema21
        rsi_buy_ok   = cfg.RSI_BUY_MIN <= rsi <= cfg.RSI_BUY_MAX

        logger.debug(
            "[%s] BUY check → close=%.2f OR_H=%.2f breakout=%s ema_up=%s rsi=%.1f vol_ok=%s",
            instrument_key, close, self.or_high or 0,
            breakout_up, ema_uptrend, rsi, vol_ok,
        )

        if breakout_up and ema_uptrend and rsi_buy_ok and vol_ok:
            qty = _calc_quantity(close, available_capital)
            if qty < 1:
                return Signal(
                    "HOLD", instrument_key,
                    f"BUY signal valid but insufficient capital ({available_capital:.0f})",
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
                side="BUY",
            )
            return Signal(
                "BUY", instrument_key,
                (
                    f"ORB breakout above {self.or_high:.2f} | "
                    f"EMA9={ema9:.2f} EMA21={ema21:.2f} | RSI={rsi:.1f} | Vol OK"
                ),
                close, qty, stop_loss, target, side="BUY",
            )

        # ── SHORT: bearish breakdown below OR Low ─────────────────────────────
        breakdown_dn  = close < (self.or_low or 0)
        ema_downtrend = ema9 < ema21
        rsi_sell_ok   = cfg.RSI_SELL_MIN <= rsi <= cfg.RSI_SELL_MAX

        logger.debug(
            "[%s] SHORT check → close=%.2f OR_L=%.2f breakdown=%s ema_dn=%s rsi=%.1f vol_ok=%s",
            instrument_key, close, self.or_low or 0,
            breakdown_dn, ema_downtrend, rsi, vol_ok,
        )

        if breakdown_dn and ema_downtrend and rsi_sell_ok and vol_ok:
            qty = _calc_quantity(close, available_capital)
            if qty < 1:
                return Signal(
                    "HOLD", instrument_key,
                    f"SHORT signal valid but insufficient capital ({available_capital:.0f})",
                    close, 0,
                )
            stop_loss = round(close * (1 + cfg.STOP_LOSS_PCT), 2)   # above entry for shorts
            target    = round(close * (1 - cfg.TARGET_PCT), 2)       # below entry for shorts
            self.position = Position(
                instrument_key=instrument_key,
                entry_price=close,
                quantity=qty,
                stop_loss=stop_loss,
                target=target,
                side="SHORT",
            )
            return Signal(
                "SHORT", instrument_key,
                (
                    f"ORB breakdown below {self.or_low:.2f} | "
                    f"EMA9={ema9:.2f} EMA21={ema21:.2f} | RSI={rsi:.1f} | Vol OK"
                ),
                close, qty, stop_loss, target, side="SHORT",
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
                f"Force exit at market close @ {ltp:.2f} | P&L=₹{pnl:.2f}",
                ltp, p.quantity, side=p.side,
            )
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _calc_quantity(price: float, available_capital: float) -> int:
    """
    Calculate shares to buy/short so that the risk (RISK_PER_TRADE)
    maps to the STOP_LOSS_PCT move against entry.

      qty = RISK_PER_TRADE / (price × STOP_LOSS_PCT)

    Also capped by available_capital / price.
    """
    if price <= 0:
        return 0
    qty_by_risk    = int(cfg.RISK_PER_TRADE / (price * cfg.STOP_LOSS_PCT))
    qty_by_capital = int(available_capital / price)
    return max(min(qty_by_risk, qty_by_capital), 0)
