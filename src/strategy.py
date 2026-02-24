"""
Trading Strategy – Opening Range Breakout (ORB) with multi-layer confirmation.

Confirmation stack (ALL must be true for entry):
  1. Price breaks above OR High (BUY) or below OR Low (SHORT)
  2. EMA(9) > EMA(21) for BUY, EMA(9) < EMA(21) for SHORT
  3. RSI in momentum zone (45-70 BUY, 30-55 SHORT)
  4. Volume >= 1.5× average (genuine breakout, not fakeout)
  5. VWAP filter: price above VWAP for BUY, below VWAP for SHORT

Risk management per trade:
  - ATR-based dynamic stop loss (1.5 × ATR from entry)
  - ATR-based target (2.5 × ATR from entry → ~1.67:1 R:R)
  - Trailing stop: after 1R profit, trail at 1.5 × ATR from highest/lowest
  - Fallback to fixed 0.5% stop / 0.75% target when ATR unavailable

Daily risk controls:
  - ₹50 profit target → stop trading
  - ₹50 max loss → stop trading
  - Max 3 trades per day
  - Max 3 consecutive losses → kill switch
  - No entries during mid-day lull (12:00–13:30)
  - No entries after 14:30
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
    initial_risk: float = 0.0  # distance from entry to initial stop (for trailing)
    peak_price: float = 0.0    # highest price since entry (BUY) or lowest (SHORT)
    trailing_active: bool = False  # True once trade reaches 1R profit

    @property
    def position_value(self) -> float:
        return self.entry_price * self.quantity

    def unrealised_pnl(self, ltp: float) -> float:
        if self.side == "BUY":
            return (ltp - self.entry_price) * self.quantity
        else:  # SHORT
            return (self.entry_price - ltp) * self.quantity

    def update_trailing(self, ltp: float, atr: float) -> None:
        """Update trailing stop logic. Called every candle."""
        if self.side == "BUY":
            # Track peak price
            if ltp > self.peak_price:
                self.peak_price = ltp

            # Activate trailing after 1R profit
            if not self.trailing_active and self.initial_risk > 0:
                if ltp >= self.entry_price + self.initial_risk:
                    self.trailing_active = True
                    # Move stop to breakeven + small buffer
                    self.stop_loss = round(self.entry_price + 0.10, 2)
                    logger.info(
                        "[%s] Trailing activated! Stop moved to breakeven %.2f",
                        self.instrument_key, self.stop_loss,
                    )

            # Trail stop at 1.5 × ATR below peak
            if self.trailing_active and atr > 0:
                trail_stop = round(self.peak_price - cfg.TRAILING_ATR_MULTIPLIER * atr, 2)
                if trail_stop > self.stop_loss:
                    self.stop_loss = trail_stop
                    logger.info(
                        "[%s] Trailing stop updated to %.2f (peak=%.2f)",
                        self.instrument_key, self.stop_loss, self.peak_price,
                    )

        else:  # SHORT
            # Track lowest price (peak for shorts)
            if self.peak_price == 0 or ltp < self.peak_price:
                self.peak_price = ltp

            # Activate trailing after 1R profit
            if not self.trailing_active and self.initial_risk > 0:
                if ltp <= self.entry_price - self.initial_risk:
                    self.trailing_active = True
                    self.stop_loss = round(self.entry_price - 0.10, 2)
                    logger.info(
                        "[%s] Trailing activated! Stop moved to breakeven %.2f",
                        self.instrument_key, self.stop_loss,
                    )

            # Trail stop at 1.5 × ATR above lowest
            if self.trailing_active and atr > 0:
                trail_stop = round(self.peak_price + cfg.TRAILING_ATR_MULTIPLIER * atr, 2)
                if trail_stop < self.stop_loss:
                    self.stop_loss = trail_stop
                    logger.info(
                        "[%s] Trailing stop updated to %.2f (peak=%.2f)",
                        self.instrument_key, self.stop_loss, self.peak_price,
                    )


class ORBStrategy:
    """
    Opening Range Breakout strategy with ATR-based stops, VWAP filter,
    trailing stop, and multi-layer confirmation.
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
        Uses ATR-based stops, VWAP filter, and trailing stop management.
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
        atr     = float(latest.get("atr", 0) or 0)
        vwap    = float(latest.get("vwap", 0) or 0)

        # ── Manage existing position ──────────────────────────────────────────
        if self.position:
            p   = self.position
            pnl = p.unrealised_pnl(close)

            # Update trailing stop before checking exits
            if atr > 0:
                p.update_trailing(close, atr)

            # Stop-loss hit
            if p.side == "BUY" and close <= p.stop_loss:
                sl_type = "trailing" if p.trailing_active else "initial"
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Stop-loss ({sl_type}) hit @ {close:.2f} (entry {p.entry_price:.2f}, P&L ₹{pnl:.2f})",
                    close, p.quantity, side=p.side,
                )
            if p.side == "SHORT" and close >= p.stop_loss:
                sl_type = "trailing" if p.trailing_active else "initial"
                self.position = None
                return Signal(
                    "EXIT", instrument_key,
                    f"Stop-loss ({sl_type}) hit @ {close:.2f} (entry {p.entry_price:.2f}, P&L ₹{pnl:.2f})",
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

            # EMA reversal exit (trend turned against us) — only if trailing not active
            if not p.trailing_active:
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
                f"In {p.side} position | LTP={close:.2f} | P&L=₹{pnl:.2f} | "
                f"SL={p.stop_loss:.2f} | Trail={'ON' if p.trailing_active else 'OFF'}",
                close, 0, side=p.side,
            )

        # ── Look for new entry (no open position) ─────────────────────────────
        if not self.or_established:
            return hold

        vol_ok = volume >= vol_ma * cfg.VOLUME_MULTIPLIER

        # VWAP filter
        vwap_ok_buy  = (not cfg.USE_VWAP_FILTER) or (vwap > 0 and close > vwap)
        vwap_ok_sell = (not cfg.USE_VWAP_FILTER) or (vwap > 0 and close < vwap)

        # ── BUY: bullish breakout above OR High ───────────────────────────────
        breakout_up  = close > (self.or_high or 0)
        ema_uptrend  = ema9 > ema21
        rsi_buy_ok   = cfg.RSI_BUY_MIN <= rsi <= cfg.RSI_BUY_MAX

        logger.debug(
            "[%s] BUY check → close=%.2f OR_H=%.2f breakout=%s ema_up=%s rsi=%.1f "
            "vol_ok=%s vwap_ok=%s",
            instrument_key, close, self.or_high or 0,
            breakout_up, ema_uptrend, rsi, vol_ok, vwap_ok_buy,
        )

        if breakout_up and ema_uptrend and rsi_buy_ok and vol_ok and vwap_ok_buy:
            # Calculate ATR-based or fallback stop/target
            stop_loss, target, initial_risk = _calc_stop_target(close, atr, "BUY")
            qty = _calc_quantity(close, available_capital, atr)
            if qty < 1:
                return Signal(
                    "HOLD", instrument_key,
                    f"BUY signal valid but insufficient capital ({available_capital:.0f})",
                    close, 0,
                )
            self.position = Position(
                instrument_key=instrument_key,
                entry_price=close,
                quantity=qty,
                stop_loss=stop_loss,
                target=target,
                side="BUY",
                initial_risk=initial_risk,
                peak_price=close,
            )
            return Signal(
                "BUY", instrument_key,
                (
                    f"ORB breakout above {self.or_high:.2f} | "
                    f"EMA9={ema9:.2f} EMA21={ema21:.2f} | RSI={rsi:.1f} | Vol OK | "
                    f"VWAP={'OK' if vwap_ok_buy else 'N/A'} | "
                    f"SL={stop_loss:.2f} TGT={target:.2f} ATR={atr:.2f}"
                ),
                close, qty, stop_loss, target, side="BUY",
            )

        # ── SHORT: bearish breakdown below OR Low ─────────────────────────────
        breakdown_dn  = close < (self.or_low or 0)
        ema_downtrend = ema9 < ema21
        rsi_sell_ok   = cfg.RSI_SELL_MIN <= rsi <= cfg.RSI_SELL_MAX

        logger.debug(
            "[%s] SHORT check → close=%.2f OR_L=%.2f breakdown=%s ema_dn=%s rsi=%.1f "
            "vol_ok=%s vwap_ok=%s",
            instrument_key, close, self.or_low or 0,
            breakdown_dn, ema_downtrend, rsi, vol_ok, vwap_ok_sell,
        )

        if breakdown_dn and ema_downtrend and rsi_sell_ok and vol_ok and vwap_ok_sell:
            stop_loss, target, initial_risk = _calc_stop_target(close, atr, "SHORT")
            qty = _calc_quantity(close, available_capital, atr)
            if qty < 1:
                return Signal(
                    "HOLD", instrument_key,
                    f"SHORT signal valid but insufficient capital ({available_capital:.0f})",
                    close, 0,
                )
            self.position = Position(
                instrument_key=instrument_key,
                entry_price=close,
                quantity=qty,
                stop_loss=stop_loss,
                target=target,
                side="SHORT",
                initial_risk=initial_risk,
                peak_price=close,
            )
            return Signal(
                "SHORT", instrument_key,
                (
                    f"ORB breakdown below {self.or_low:.2f} | "
                    f"EMA9={ema9:.2f} EMA21={ema21:.2f} | RSI={rsi:.1f} | Vol OK | "
                    f"VWAP={'OK' if vwap_ok_sell else 'N/A'} | "
                    f"SL={stop_loss:.2f} TGT={target:.2f} ATR={atr:.2f}"
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

def _calc_stop_target(
    price: float, atr: float, side: str
) -> tuple[float, float, float]:
    """
    Calculate stop loss, target, and initial risk distance.
    Uses ATR-based calculation when ATR > 0, falls back to fixed %.
    """
    if atr > 0:
        risk_distance   = atr * cfg.ATR_STOP_MULTIPLIER
        target_distance = atr * cfg.ATR_TARGET_MULTIPLIER
    else:
        risk_distance   = price * cfg.STOP_LOSS_PCT
        target_distance = price * cfg.TARGET_PCT

    if side == "BUY":
        stop_loss = round(price - risk_distance, 2)
        target    = round(price + target_distance, 2)
    else:  # SHORT
        stop_loss = round(price + risk_distance, 2)
        target    = round(price - target_distance, 2)

    return stop_loss, target, round(risk_distance, 2)


def _calc_quantity(price: float, available_capital: float, atr: float = 0) -> int:
    """
    Calculate shares to buy/short using ATR-based position sizing.

    ATR-based: qty = RISK_PER_TRADE / (ATR × ATR_STOP_MULTIPLIER)
    Fallback:  qty = RISK_PER_TRADE / (price × STOP_LOSS_PCT)

    Also capped by available_capital / price.
    """
    if price <= 0:
        return 0

    if atr > 0:
        risk_per_share = atr * cfg.ATR_STOP_MULTIPLIER
    else:
        risk_per_share = price * cfg.STOP_LOSS_PCT

    if risk_per_share <= 0:
        return 0

    qty_by_risk    = int(cfg.RISK_PER_TRADE / risk_per_share)
    qty_by_capital = int(available_capital / price)
    return max(min(qty_by_risk, qty_by_capital), 0)
