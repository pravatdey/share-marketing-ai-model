"""
Upstox Auto Trading Bot – Main Entry Point
==========================================

Goal: Earn ₹50 profit per day with minimal risk. Exit immediately on target.

Execution flow (runs inside GitHub Actions every weekday):

  9:10 AM IST  – Bot starts, authenticates with Upstox
  9:15 AM IST  – Market opens
  9:15–9:35 AM – Collects first 4 five-minute candles → builds Opening Range
  9:35 AM+     – Scans ALL 50 stocks every 60 seconds for BUY or SHORT signals
                 ∙ Multi-layer confirmation: ORB + EMA + RSI + Volume + VWAP
                 ∙ ATR-based dynamic stop loss and position sizing
                 ∙ Trailing stop after 1R profit (moves to breakeven, then trails)
                 ∙ Exits on stop / target / EMA-reversal / ₹50 daily profit
  12:00–1:30 PM – Mid-day pause (no new entries during low-volume period)
  2:30 PM IST  – No new entries after this time
  3:10 PM IST  – Force-exit ALL open positions
  3:15 PM IST  – Sends daily email summary and exits

Safety rules:
  ∙ ₹50 daily profit target → stop trading
  ∙ ₹50 daily max loss → stop trading
  ∙ Max 3 trades per day → prevent overtrading
  ∙ 3 consecutive losses → kill switch (strategy misaligned)
  ∙ Never hold positions overnight (Intraday order type)
  ∙ Max 1 open position at a time
"""

import logging
import sys
import time
from datetime import date, datetime

import colorlog
import pytz

import config.settings as cfg
from src.auth          import get_access_token
from src.market_data   import MarketData
from src.order_manager import OrderManager
from src.risk_manager  import RiskManager
from src.stock_selector import StockSelector
from src.strategy      import ORBStrategy, Signal
import src.notifier    as notifier


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    fmt = "%(log_color)s%(asctime)s [%(levelname)s] %(message)s%(reset)s"
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(fmt))

    file_handler = logging.FileHandler(cfg.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(file_handler)


logger = logging.getLogger(__name__)


# ── Time helpers ──────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(cfg.IST)


def _time_str(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _is_market_holiday() -> bool:
    today = date.today().isoformat()
    return today in cfg.NSE_HOLIDAYS


def _wait_until(target_time_str: str) -> None:
    """Block until the clock reaches target_time_str (HH:MM) in IST."""
    now = _now_ist()
    target_h, target_m = map(int, target_time_str.split(":"))
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    if now >= target:
        return
    wait_secs = (target - now).total_seconds()
    logger.info("Waiting %.0f seconds until %s IST …", wait_secs, target_time_str)
    time.sleep(max(wait_secs, 0))


def _past_time(time_str: str) -> bool:
    now = _now_ist()
    h, m = map(int, time_str.split(":"))
    return now.hour > h or (now.hour == h and now.minute >= m)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _setup_logging()
    logger.info("=" * 60)
    logger.info("Upstox Auto Trading Bot – %s", date.today().isoformat())
    logger.info("Target: ₹%.0f profit | Max loss: ₹%.0f", cfg.DAILY_PROFIT_TARGET, cfg.DAILY_MAX_LOSS)
    logger.info("=" * 60)

    # ── Holiday / weekend check ────────────────────────────────────────────────
    if _is_market_holiday():
        logger.info("Today is an NSE market holiday. Bot will not trade.")
        sys.exit(0)

    if datetime.now().weekday() >= 5:
        logger.info("Today is a weekend. Markets are closed.")
        sys.exit(0)

    # ── Authentication ────────────────────────────────────────────────────────
    logger.info("Authenticating with Upstox …")
    try:
        access_token = get_access_token()
    except Exception as exc:
        logger.error("Authentication failed: %s", exc)
        notifier.notify_error(f"Authentication failed:\n{exc}")
        sys.exit(1)

    # ── Resolve instrument keys from Upstox master file ────────────────────────
    logger.info("Resolving instrument keys from Upstox instruments master …")
    resolved = cfg.resolve_instrument_keys()
    if not resolved:
        logger.error("Failed to resolve any instrument keys. Exiting.")
        notifier.notify_error("Failed to resolve instrument keys from Upstox master file.")
        sys.exit(1)
    logger.info("Resolved %d instrument keys for trading", len(resolved))

    # ── Component initialisation ──────────────────────────────────────────────
    market_data    = MarketData(access_token)
    order_manager  = OrderManager(access_token)
    risk_manager   = RiskManager()
    stock_selector = StockSelector(market_data)

    notifier.notify_bot_started()

    # ── Wait for market open ──────────────────────────────────────────────────
    logger.info("Waiting for market open at %s IST …", cfg.MARKET_OPEN_TIME)
    _wait_until(cfg.MARKET_OPEN_TIME)

    # ── Select top-N stocks to monitor ────────────────────────────────────────
    logger.info("Selecting top %d stocks to monitor …", cfg.TOP_N_STOCKS)
    ranked_stocks = stock_selector.get_ranked_stocks(top_n=cfg.TOP_N_STOCKS)
    if not ranked_stocks:
        logger.error("No suitable stocks found. Exiting.")
        notifier.notify_error("No suitable Nifty 50 stocks found for today. No trades placed.")
        sys.exit(0)

    # Create one ORBStrategy instance per stock
    instrument_keys = [s["instrument_key"] for s in ranked_stocks]
    strategies: dict[str, ORBStrategy] = {
        key: ORBStrategy(key) for key in instrument_keys
    }
    or_established: dict[str, bool] = {key: False for key in instrument_keys}

    # Track entry details per stock (for P&L recording on exit)
    entry_price: dict[str, float] = {key: 0.0 for key in instrument_keys}
    entry_qty:   dict[str, int]   = {key: 0   for key in instrument_keys}

    # Track which stock currently holds an open position
    active_key: str | None = None

    # ── Main trading loop ─────────────────────────────────────────────────────
    logger.info("Entering main trading loop – monitoring %d stocks …", len(instrument_keys))

    while True:
        now_str = _time_str(_now_ist())

        # ── Force exit at 3:10 PM ─────────────────────────────────────────────
        if _past_time(cfg.FORCE_EXIT_TIME):
            logger.info("Force exit time reached (%s IST). Closing all positions.", cfg.FORCE_EXIT_TIME)
            order_manager.exit_all_positions()
            # Book any remaining open position
            if active_key and strategies[active_key].position:
                ltp = market_data.get_ltp(active_key) or entry_price[active_key]
                pnl = risk_manager.record_trade(
                    active_key, strategies[active_key].position.side,
                    entry_price[active_key], ltp, entry_qty[active_key],
                    "force-exit at close",
                )
                notifier.notify_trade_exit(
                    active_key, entry_price[active_key], ltp,
                    entry_qty[active_key], pnl,
                    "Force-exit at close", risk_manager.realised_pnl,
                )
            break

        # ── Check daily risk limits ───────────────────────────────────────────
        if not risk_manager.can_trade():
            if risk_manager.is_max_loss_hit():
                logger.warning("Max daily loss hit. Exiting all positions and stopping.")
                order_manager.exit_all_positions()
                # Book the open position so the loss is realised and reported
                if active_key and strategies[active_key].position:
                    ltp = market_data.get_ltp(active_key) or entry_price[active_key]
                    pnl = risk_manager.record_trade(
                        active_key, strategies[active_key].position.side,
                        entry_price[active_key], ltp, entry_qty[active_key],
                        "max-loss exit",
                    )
                    notifier.notify_trade_exit(
                        active_key, entry_price[active_key], ltp,
                        entry_qty[active_key], pnl,
                        "Max loss exit", risk_manager.realised_pnl,
                    )
                    strategies[active_key].position = None
                    active_key = None
                notifier.notify_max_loss_hit(risk_manager.total_pnl)
                break
            if risk_manager.is_profit_target_hit():
                logger.info(
                    "Daily profit target ₹%.2f reached! Done for today.",
                    risk_manager.realised_pnl,
                )
                notifier.notify_profit_target_hit(risk_manager.realised_pnl)
                break
            if risk_manager.is_max_trades_hit():
                logger.info("Max trades per day reached (%d). Done for today.", cfg.MAX_TRADES_PER_DAY)
                break
            if risk_manager.is_consecutive_loss_hit():
                logger.warning(
                    "Kill switch: %d consecutive losses. Strategy misaligned. Stopping.",
                    risk_manager.consecutive_losses,
                )
                order_manager.exit_all_positions()
                # Book the open position
                if active_key and strategies[active_key].position:
                    ltp = market_data.get_ltp(active_key) or entry_price[active_key]
                    pnl = risk_manager.record_trade(
                        active_key, strategies[active_key].position.side,
                        entry_price[active_key], ltp, entry_qty[active_key],
                        "kill-switch exit",
                    )
                    notifier.notify_trade_exit(
                        active_key, entry_price[active_key], ltp,
                        entry_qty[active_key], pnl,
                        "Kill switch exit", risk_manager.realised_pnl,
                    )
                    strategies[active_key].position = None
                    active_key = None
                break

        # ── Establish Opening Range for each stock (after 9:30 AM) ────────────
        if _past_time(cfg.ORB_END_TIME):
            for key in instrument_keys:
                if not or_established[key]:
                    df = market_data.get_candles(key)
                    if not df.empty:
                        or_data = market_data.get_opening_range(df, n_candles=cfg.ORB_CANDLES)
                        if or_data:
                            strategies[key].set_opening_range(
                                or_data["or_high"], or_data["or_low"]
                            )
                            or_established[key] = True

        # ── Update unrealised P&L for active position ─────────────────────────
        if active_key and strategies[active_key].position:
            ltp = market_data.get_ltp(active_key)
            if ltp:
                open_pnl = strategies[active_key].position.unrealised_pnl(ltp)
                risk_manager.update_open_pnl(open_pnl)

                # ── Real-time stop-loss check using live LTP ────────────────
                # The strategy checks stops on 5-min candle close, but price
                # can gap past the stop between candles. Check LTP directly.
                p = strategies[active_key].position
                should_exit = False
                if p.side == "BUY" and ltp <= p.stop_loss:
                    should_exit = True
                elif p.side == "SHORT" and ltp >= p.stop_loss:
                    should_exit = True

                if should_exit:
                    logger.warning(
                        "LIVE STOP-LOSS HIT for %s | LTP=%.2f | SL=%.2f | Exiting immediately.",
                        active_key, ltp, p.stop_loss,
                    )
                    close_side = "SELL" if p.side == "BUY" else "BUY"
                    exit_signal = Signal(
                        "EXIT", active_key,
                        f"Live stop-loss hit @ LTP={ltp:.2f} (SL={p.stop_loss:.2f})",
                        ltp, p.quantity, side=p.side,
                    )
                    order_id = order_manager.place_order(exit_signal, close_side)
                    if order_id:
                        fill_price = order_manager.wait_for_fill(order_id)
                        if fill_price is None:
                            fill_price = ltp  # use LTP as fallback
                        pnl = risk_manager.record_trade(
                            active_key, p.side,
                            entry_price[active_key], fill_price,
                            entry_qty[active_key], "live stop-loss exit",
                        )
                        risk_manager.update_open_pnl(0)
                        notifier.notify_trade_exit(
                            active_key, entry_price[active_key], fill_price,
                            entry_qty[active_key], pnl,
                            "Live stop-loss exit", risk_manager.realised_pnl,
                        )
                    else:
                        # Order failed – force exit via bulk API
                        logger.error("Live SL exit order failed – using bulk exit.")
                        order_manager.exit_all_positions()
                        pnl = risk_manager.record_trade(
                            active_key, p.side,
                            entry_price[active_key], ltp,
                            entry_qty[active_key], "emergency stop-loss exit",
                        )
                        risk_manager.update_open_pnl(0)

                    strategies[active_key].position = None
                    entry_price[active_key] = 0.0
                    entry_qty[active_key] = 0
                    active_key = None
                    # Re-check risk limits after exit
                    if not risk_manager.can_trade():
                        continue  # will hit risk-limit check at top of loop

        # ── No new entries after TRADING_STOP_TIME or during mid-day pause ────
        in_mid_day_pause = (
            _past_time(cfg.MID_DAY_PAUSE_START)
            and not _past_time(cfg.MID_DAY_PAUSE_END)
        )
        if in_mid_day_pause and active_key is None:
            logger.info("[%s] Mid-day pause (12:00–13:30). Skipping new entries.", now_str)

        can_enter = (
            not _past_time(cfg.TRADING_STOP_TIME)
            and not in_mid_day_pause
            and risk_manager.can_trade()
        )

        # Evaluated ONCE per cycle – only changes when an order is actually placed.
        has_open_position = active_key is not None and strategies[active_key].position is not None

        # ── Scan each stock for signals ───────────────────────────────────────
        for key in instrument_keys:
            # Re-check force exit time inside the loop (scanning 50 stocks is slow)
            if _past_time(cfg.FORCE_EXIT_TIME):
                logger.info("Force exit time reached mid-scan. Breaking out.")
                break

            strat = strategies[key]

            if not or_established[key]:
                continue  # OR not ready yet for this stock

            df = market_data.get_candles(key)
            if df.empty:
                continue

            df = market_data.add_indicators(df)

            available_capital = cfg.EFFECTIVE_CAPITAL if (can_enter and not has_open_position) else 0.0

            signal: Signal = strat.generate_signal(df, available_capital)

            logger.info(
                "[%s] [%s] Signal=%s | %s",
                now_str, key.split("|")[-1][:12], signal.action, signal.reason,
            )

            # ── Execute BUY ───────────────────────────────────────────────────
            if signal.action == "BUY" and can_enter and not has_open_position and signal.quantity > 0:
                order_id = order_manager.place_order(signal, "BUY")
                if order_id:
                    fill_price = order_manager.wait_for_fill(order_id)
                    if fill_price is None:
                        logger.warning("BUY order %s not filled/rejected. Clearing position.", order_id)
                        strat.position = None
                        continue
                    entry_price[key] = fill_price
                    entry_qty[key]   = signal.quantity
                    active_key       = key
                    if strat.position:
                        # Recalculate ATR-based stops from actual fill price
                        from src.strategy import _calc_stop_target
                        atr_val = float(df.iloc[-1].get("atr", 0) or 0)
                        sl, tgt, risk = _calc_stop_target(fill_price, atr_val, "BUY")
                        strat.position.entry_price = fill_price
                        strat.position.stop_loss   = sl
                        strat.position.target      = tgt
                        strat.position.initial_risk = risk
                        strat.position.peak_price  = fill_price
                    notifier.notify_trade_entry(
                        key, fill_price, signal.quantity,
                        signal.stop_loss, signal.target, signal.reason,
                    )
                else:
                    logger.warning("BUY order placement failed. Clearing position.")
                    strat.position = None
                    continue
                break  # one position at a time – stop scanning other stocks

            # ── Execute SHORT ─────────────────────────────────────────────────
            elif signal.action == "SHORT" and can_enter and not has_open_position and signal.quantity > 0:
                order_id = order_manager.place_order(signal, "SELL")
                if order_id:
                    fill_price = order_manager.wait_for_fill(order_id)
                    if fill_price is None:
                        logger.warning("SHORT order %s not filled/rejected. Clearing position.", order_id)
                        strat.position = None
                        continue
                    entry_price[key] = fill_price
                    entry_qty[key]   = signal.quantity
                    active_key       = key
                    if strat.position:
                        from src.strategy import _calc_stop_target
                        atr_val = float(df.iloc[-1].get("atr", 0) or 0)
                        sl, tgt, risk = _calc_stop_target(fill_price, atr_val, "SHORT")
                        strat.position.entry_price = fill_price
                        strat.position.stop_loss   = sl
                        strat.position.target      = tgt
                        strat.position.initial_risk = risk
                        strat.position.peak_price  = fill_price
                    notifier.notify_trade_entry(
                        key, fill_price, signal.quantity,
                        signal.stop_loss, signal.target, signal.reason,
                    )
                else:
                    logger.warning("SHORT order placement failed. Clearing position.")
                    strat.position = None
                    continue
                break  # one position at a time – stop scanning other stocks

            # ── Execute EXIT (stop-loss / target / EMA reversal) ──────────────
            elif signal.action == "EXIT" and key == active_key:
                # Determine close side: BUY position → SELL to close; SHORT → BUY to close
                close_side = "SELL" if signal.side == "BUY" else "BUY"
                order_id = order_manager.place_order(signal, close_side)
                if order_id:
                    fill_price = order_manager.wait_for_fill(order_id)
                    if fill_price is None:
                        logger.warning(
                            "EXIT order %s not filled/rejected. Restoring position for %s.",
                            order_id, key,
                        )
                        # Restore the position that generate_signal() already cleared
                        from src.strategy import Position
                        strat.position = Position(
                            instrument_key=key,
                            entry_price=entry_price[key],
                            quantity=entry_qty[key],
                            stop_loss=signal.stop_loss,
                            target=signal.target,
                            side=signal.side,
                        )
                        continue
                    pnl = risk_manager.record_trade(
                        key, signal.side,
                        entry_price[key], fill_price, signal.quantity, signal.reason,
                    )
                    risk_manager.update_open_pnl(0)
                    notifier.notify_trade_exit(
                        key, entry_price[key], fill_price,
                        signal.quantity, pnl, signal.reason,
                        risk_manager.realised_pnl,
                    )
                    entry_price[key] = 0.0
                    entry_qty[key]   = 0
                    active_key       = None

                    # ── Check if ₹50 target reached after this trade ──────────
                    if risk_manager.is_profit_target_hit():
                        logger.info(
                            "Daily profit target ₹%.2f reached after trade! Stopping.",
                            risk_manager.realised_pnl,
                        )
                        notifier.notify_profit_target_hit(risk_manager.realised_pnl)
                        break  # exit for-loop; outer while will also break

                    # ── Check if max loss hit ─────────────────────────────────
                    if risk_manager.is_max_loss_hit():
                        logger.warning("Max loss ₹%.2f hit. Stopping.", abs(risk_manager.total_pnl))
                        order_manager.exit_all_positions()
                        notifier.notify_max_loss_hit(risk_manager.total_pnl)
                        break  # exit for-loop; outer while will also break

        # ── Check if we should stop before sleeping ────────────────────────
        if _past_time(cfg.FORCE_EXIT_TIME):
            logger.info("Force exit time – exiting main loop.")
            break
        if not risk_manager.can_trade():
            logger.info("Risk limit reached – exiting main loop.")
            break

        # ── Sleep until next poll ─────────────────────────────────────────────
        logger.info("Next check in %d seconds …", cfg.POLL_INTERVAL_SECS)
        time.sleep(cfg.POLL_INTERVAL_SECS)

    # ── End of day summary ────────────────────────────────────────────────────
    summary = risk_manager.summary_text()
    logger.info("\n%s", summary)
    notifier.notify_daily_summary(summary)
    logger.info("Bot finished for the day. Goodbye!")
    sys.exit(0)


if __name__ == "__main__":
    main()
