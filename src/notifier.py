"""
Notifier – sends email alerts for trade events and end-of-day summary.

Uses Gmail SMTP with an App Password (not your regular Gmail password).
Configure in .env:
  EMAIL_SENDER       = yourname@gmail.com
  EMAIL_APP_PASSWORD = <16-char app password from Google>
  EMAIL_RECEIVER     = yourname@gmail.com
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import config.settings as cfg

logger = logging.getLogger(__name__)


def _send_email(subject: str, body: str) -> bool:
    """Internal helper – send a plain-text email via Gmail SMTP."""
    if not cfg.EMAIL_SENDER or not cfg.EMAIL_APP_PASSWORD:
        logger.warning("Email credentials not set – skipping notification.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg.EMAIL_SENDER
        msg["To"]      = cfg.EMAIL_RECEIVER
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.EMAIL_SENDER, cfg.EMAIL_APP_PASSWORD)
            server.sendmail(cfg.EMAIL_SENDER, cfg.EMAIL_RECEIVER, msg.as_string())

        logger.info("Email sent: %s", subject)
        return True

    except smtplib.SMTPException as exc:
        logger.error("Email send failed: %s", exc)
        return False


# ── Public notification functions ────────────────────────────────────────────

def notify_bot_started() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = (
        f"Upstox Auto Trading Bot started at {now}.\n\n"
        f"Capital      : ₹{cfg.TRADING_CAPITAL:.0f}\n"
        f"Leverage     : {cfg.MIS_LEVERAGE}x MIS\n"
        f"Profit Target: ₹{cfg.DAILY_PROFIT_TARGET:.0f}\n"
        f"Max Loss     : ₹{cfg.DAILY_MAX_LOSS:.0f}\n"
        f"Strategy     : Opening Range Breakout + EMA + RSI\n"
        f"Exit by      : {cfg.FORCE_EXIT_TIME} IST\n"
    )
    _send_email("[Auto-Trader] Bot Started", body)


def notify_stock_selected(stock: dict) -> None:
    body = (
        f"Stock selected for today's trading:\n\n"
        f"Instrument : {stock.get('instrument_key', 'N/A')}\n"
        f"LTP        : ₹{stock.get('ltp', 0):.2f}\n"
        f"ATR        : ₹{stock.get('atr', 0):.2f} ({stock.get('atr_pct', 0):.3f}%)\n"
        f"Avg Volume : {stock.get('vol_ma', 0):.0f}\n"
    )
    _send_email("[Auto-Trader] Stock Selected", body)


def notify_trade_entry(
    instrument_key: str,
    entry_price: float,
    quantity: int,
    stop_loss: float,
    target: float,
    reason: str,
) -> None:
    body = (
        f"BUY order placed!\n\n"
        f"Instrument : {instrument_key}\n"
        f"Entry Price: ₹{entry_price:.2f}\n"
        f"Quantity   : {quantity}\n"
        f"Stop Loss  : ₹{stop_loss:.2f} (-{cfg.STOP_LOSS_PCT*100:.1f}%)\n"
        f"Target     : ₹{target:.2f} (+{cfg.TARGET_PCT*100:.1f}%)\n"
        f"Reason     : {reason}\n"
        f"Position   : ₹{entry_price * quantity:.2f}\n"
    )
    _send_email("[Auto-Trader] BUY Order Placed", body)


def notify_trade_exit(
    instrument_key: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    pnl: float,
    reason: str,
    total_pnl: float,
) -> None:
    emoji = "PROFIT" if pnl >= 0 else "LOSS"
    body = (
        f"SELL order placed ({emoji})!\n\n"
        f"Instrument : {instrument_key}\n"
        f"Entry      : ₹{entry_price:.2f}\n"
        f"Exit       : ₹{exit_price:.2f}\n"
        f"Quantity   : {quantity}\n"
        f"Trade P&L  : ₹{pnl:.2f}\n"
        f"Total P&L  : ₹{total_pnl:.2f}\n"
        f"Reason     : {reason}\n"
    )
    _send_email(f"[Auto-Trader] SELL – {emoji} ₹{pnl:.2f}", body)


def notify_profit_target_hit(total_pnl: float) -> None:
    body = (
        f"Daily profit target of ₹{cfg.DAILY_PROFIT_TARGET:.0f} has been reached!\n\n"
        f"Total P&L today: ₹{total_pnl:.2f}\n\n"
        f"The bot will not place any more orders today.\n"
        f"All positions will be exited by {cfg.FORCE_EXIT_TIME} IST.\n"
    )
    _send_email("[Auto-Trader] Profit Target Reached!", body)


def notify_max_loss_hit(total_pnl: float) -> None:
    body = (
        f"Daily max loss limit of ₹{cfg.DAILY_MAX_LOSS:.0f} has been reached.\n\n"
        f"Total P&L today: ₹{total_pnl:.2f}\n\n"
        f"The bot has stopped all trading to protect your capital.\n"
        f"All positions will be exited immediately.\n"
    )
    _send_email("[Auto-Trader] Max Loss Limit Hit – Trading Stopped", body)


def notify_daily_summary(summary_text: str) -> None:
    body = (
        "Auto Trading Bot – End-of-Day Summary\n"
        "=" * 40 + "\n\n"
        + summary_text
        + "\n\n"
        + "Check your Upstox app for full trade history.\n"
    )
    _send_email("[Auto-Trader] Daily Summary", body)


def notify_error(error_msg: str) -> None:
    body = (
        f"An error occurred in the Auto Trading Bot:\n\n"
        f"{error_msg}\n\n"
        f"Please check the GitHub Actions logs for details.\n"
    )
    _send_email("[Auto-Trader] ERROR – Check Logs", body)
