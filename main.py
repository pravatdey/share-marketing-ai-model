"""
Upstox Auto Trading Bot – Main Entry Point
==========================================

Execution flow (runs inside GitHub Actions every weekday):

  9:10 AM IST  – Bot starts, authenticates with Upstox
  9:15 AM IST  – Market opens; bot waits in observation mode
  9:15–9:30 AM – Collects first 3 five-minute candles → builds Opening Range
  9:30 AM+     – Scans for ORB breakout signals every 5 minutes
                 ∙ Buys on valid signal (max 1 open position)
                 ∙ Exits on stop-loss / target / EMA-reversal
  3:00 PM IST  – No new entries after this time
  3:10 PM IST  – Force-exit ALL open positions
  3:15 PM IST  – Sends daily email summary and exits

Safety rules enforced throughout:
  ∙ Stop trading once ₹50 profit is realised
  ∙ Stop trading (and exit positions) if total loss >= ₹50
  ∙ Never hold positions overnight (MIS order type)
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
    logger.info("=" * 60)

    # ── Holiday check ─────────────────────────────────────────────────────────
    if _is_market_holiday():
        logger.info("Today is an NSE market holiday. Bot will not trade.")
        sys.exit(0)

    today_weekday = datetime.now().weekday()  # 0=Mon … 4=Fri
    if today_weekday >= 5:
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

    # ── Component initialisation ──────────────────────────────────────────────
    market_data   = MarketData(access_token)
    order_manager = OrderManager(access_token)
    risk_manager  = RiskManager()
    stock_selector = StockSelector(market_data)

    notifier.notify_bot_started()

    # ── Wait for market open ──────────────────────────────────────────────────
    logger.info("Waiting for market open at %s IST …", cfg.MARKET_OPEN_TIME)
    _wait_until(cfg.MARKET_OPEN_TIME)

    # ── Select the best stock to trade today ──────────────────────────────────
    logger.info("Selecting best stock to trade …")
    selected = stock_selector.select_stock(top_n=5)
    if not selected:
        logger.error("No suitable stock found. Exiting.")
        notifier.notify_error("No suitable Nifty 50 stock found for today. No trades placed.")
        sys.exit(0)

    instrument_key = selected["instrument_key"]
    notifier.notify_stock_selected(selected)

    strategy      = ORBStrategy(instrument_key)
    or_established = False

    # State tracking for entry/exit book-keeping
    entry_price: float = 0.0
    entry_qty:   int   = 0

    # ── Main trading loop ─────────────────────────────────────────────────────
    logger.info("Entering main trading loop …")

    while True:
        now_str = _time_str(_now_ist())

        # ── Force exit at 3:10 PM ─────────────────────────────────────────────
        if _past_time(cfg.FORCE_EXIT_TIME):
            logger.info("Force exit time reached (%s IST). Closing all positions.", cfg.FORCE_EXIT_TIME)
            order_manager.exit_all_positions()
            # Book any remaining open position as realised P&L
            if strategy.position:
                ltp = market_data.get_ltp(instrument_key) or entry_price
                pnl = risk_manager.record_trade(
                    instrument_key, "BUY",
                    entry_price, ltp, entry_qty, "force-exit at close",
                )
                notifier.notify_trade_exit(
                    instrument_key, entry_price, ltp, entry_qty, pnl,
                    "Force-exit at close", risk_manager.realised_pnl,
                )
            break

        # ── Establish Opening Range (9:15–9:30 AM) ────────────────────────────
        if not or_established and _past_time(cfg.ORB_END_TIME):
            df = market_data.get_candles(instrument_key)
            if not df.empty:
                or_data = market_data.get_opening_range(df, n_candles=cfg.ORB_CANDLES)
                if or_data:
                    strategy.set_opening_range(or_data["or_high"], or_data["or_low"])
                    or_established = True
                    logger.info(
                        "Opening Range: High=%.2f Low=%.2f Range=%.2f",
                        or_data["or_high"], or_data["or_low"], or_data["or_range"],
                    )

        # ── Update open P&L ───────────────────────────────────────────────────
        if strategy.position:
            ltp = market_data.get_ltp(instrument_key)
            if ltp:
                open_pnl = strategy.position.unrealised_pnl(ltp)
                risk_manager.update_open_pnl(open_pnl)

        # ── Check risk limits ─────────────────────────────────────────────────
        if not risk_manager.can_trade():
            if risk_manager.is_max_loss_hit():
                logger.warning("Max loss hit. Exiting all positions and stopping.")
                order_manager.exit_all_positions()
                notifier.notify_max_loss_hit(risk_manager.total_pnl)
                break
            if risk_manager.is_profit_target_hit():
                logger.info("Profit target hit (₹%.2f). No more new trades.", risk_manager.realised_pnl)
                notifier.notify_profit_target_hit(risk_manager.realised_pnl)
                # Wait for force-exit time to close any open position
                # Continue loop but won't generate new BUY signals

        # ── No new entries after TRADING_STOP_TIME ────────────────────────────
        can_enter = not _past_time(cfg.TRADING_STOP_TIME) and risk_manager.can_trade()

        # ── Generate signal ───────────────────────────────────────────────────
        if or_established:
            df = market_data.get_candles(instrument_key)
            if not df.empty:
                df = market_data.add_indicators(df)
                available_capital = (
                    cfg.EFFECTIVE_CAPITAL if not strategy.position else 0.0
                )
                if not can_enter and strategy.position:
                    available_capital = 0.0   # prevent new buys but allow exits

                signal: Signal = strategy.generate_signal(df, available_capital if can_enter else 0.0)

                logger.info(
                    "[%s] Signal=%s | Reason: %s",
                    now_str, signal.action, signal.reason,
                )

                # ── Execute BUY ───────────────────────────────────────────────
                if signal.action == "BUY" and can_enter:
                    order_id = order_manager.place_order(signal, "BUY")
                    if order_id:
                        fill_price = order_manager.wait_for_fill(order_id) or signal.ltp
                        entry_price = fill_price
                        entry_qty   = signal.quantity
                        # Update strategy position with actual fill price
                        if strategy.position:
                            strategy.position.entry_price = fill_price
                            strategy.position.stop_loss   = round(fill_price * (1 - cfg.STOP_LOSS_PCT), 2)
                            strategy.position.target      = round(fill_price * (1 + cfg.TARGET_PCT), 2)
                        notifier.notify_trade_entry(
                            signal.instrument_key, fill_price, signal.quantity,
                            signal.stop_loss, signal.target, signal.reason,
                        )

                # ── Execute EXIT (stop-loss / target / EMA reversal) ──────────
                elif signal.action == "EXIT":
                    order_id = order_manager.place_order(signal, "SELL")
                    if order_id:
                        fill_price = order_manager.wait_for_fill(order_id) or signal.ltp
                        pnl = risk_manager.record_trade(
                            signal.instrument_key, "BUY",
                            entry_price, fill_price, signal.quantity, signal.reason,
                        )
                        risk_manager.update_open_pnl(0)  # position closed
                        notifier.notify_trade_exit(
                            signal.instrument_key, entry_price, fill_price,
                            signal.quantity, pnl, signal.reason,
                            risk_manager.realised_pnl,
                        )
                        entry_price = 0.0
                        entry_qty   = 0

        # ── Sleep until next poll ─────────────────────────────────────────────
        logger.info("Next check in %d seconds …", cfg.POLL_INTERVAL_SECS)
        time.sleep(cfg.POLL_INTERVAL_SECS)

    # ── End of day summary ────────────────────────────────────────────────────
    summary = risk_manager.summary_text()
    logger.info("\n%s", summary)
    notifier.notify_daily_summary(summary)
    logger.info("Bot finished for the day. Goodbye!")


if __name__ == "__main__":
    main()
