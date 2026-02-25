"""
Central configuration for the Upstox Auto Trading Bot.
All environment variables are loaded here. Never hardcode secrets.
"""

import os
from dotenv import load_dotenv
import pytz
import requests

load_dotenv()

# ── Upstox API Credentials ──────────────────────────────────────────────────
UPSTOX_API_KEY      = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET   = os.getenv("UPSTOX_API_SECRET", "")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1/upstox-callback")
UPSTOX_MOBILE       = os.getenv("UPSTOX_MOBILE", "")
UPSTOX_PIN          = os.getenv("UPSTOX_PIN", "")
UPSTOX_TOTP_SECRET   = os.getenv("UPSTOX_TOTP_SECRET", "")
# Pre-stored access token (set this in GitHub Secrets to skip Selenium login)
UPSTOX_ACCESS_TOKEN  = os.getenv("UPSTOX_ACCESS_TOKEN", "")

# Upstox API v2 base URL
UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_AUTH_URL = (
    f"https://api.upstox.com/v2/login/authorization/dialog"
    f"?response_type=code&client_id={UPSTOX_API_KEY}"
    f"&redirect_uri={UPSTOX_REDIRECT_URI}"
)

# ── Trading Parameters ──────────────────────────────────────────────────────
TRADING_CAPITAL      = float(os.getenv("TRADING_CAPITAL", "2500"))   # INR
DAILY_PROFIT_TARGET  = float(os.getenv("DAILY_PROFIT_TARGET", "50")) # INR – stop trading when reached
DAILY_MAX_LOSS       = float(os.getenv("DAILY_MAX_LOSS", "50"))      # INR – stop trading when exceeded

# MIS (Margin Intraday Square-off) leverage multiplier from Upstox (typically 5x)
MIS_LEVERAGE = 5
EFFECTIVE_CAPITAL = TRADING_CAPITAL * MIS_LEVERAGE   # usable buying power

# Position sizing: risk 2% of effective capital per trade
# This scales with your actual capital so qty is never zero for affordable stocks.
# e.g. ₹1000 × 5x = ₹5000 effective → RISK_PER_TRADE = ₹100
RISK_PER_TRADE = EFFECTIVE_CAPITAL * 0.02

# ── Strategy Parameters ─────────────────────────────────────────────────────
# Opening Range Breakout on 5-min candles (resampled from 1-min API data)
ORB_CANDLES         = 4       # 4 × 5-min candles = 20 min (9:15–9:35) for stable OR
CANDLE_INTERVAL     = "1minute"   # fetched from API; resampled to 5-min in code
EMA_FAST            = 9
EMA_SLOW            = 21
RSI_PERIOD          = 14

# BUY signal: RSI must be between these values (momentum, not overbought)
RSI_BUY_MIN         = 45
RSI_BUY_MAX         = 70

# SELL/Short signal: RSI must be between these values (weakness, not oversold)
RSI_SELL_MIN        = 30
RSI_SELL_MAX        = 55

# Volume confirmation: current candle volume >= VOLUME_MULTIPLIER × 10-period avg
VOLUME_MULTIPLIER   = 1.5

# Per-trade fallback stop loss and target (as % of entry price)
# Used when ATR data is unavailable; otherwise ATR-based stops are preferred
STOP_LOSS_PCT   = 0.005   # 0.5%
TARGET_PCT      = 0.0075  # 0.75% → 1.5:1 reward:risk (higher win rate)

# ATR-based dynamic stop loss (preferred over fixed %)
ATR_STOP_MULTIPLIER   = 1.5   # stop = 1.5 × ATR from entry
ATR_TARGET_MULTIPLIER = 2.5   # target = 2.5 × ATR from entry (~1.67:1 R:R)

# Trailing stop: after trade moves 1R in profit, trail stop at this ATR multiple
TRAILING_ATR_MULTIPLIER = 1.5

# VWAP filter: only long above VWAP, only short below VWAP
USE_VWAP_FILTER = True

# How many top stocks to monitor simultaneously
TOP_N_STOCKS = 50

# Maximum open positions at a time
MAX_OPEN_POSITIONS = 1   # keep to 1 to stay within capital limits

# ── Risk Controls ──────────────────────────────────────────────────────────
MAX_TRADES_PER_DAY     = 3    # stop trading after 3 trades regardless of P&L
MAX_CONSECUTIVE_LOSSES = 3    # kill switch: stop after 3 consecutive losses

# ── Market Timing (IST) ─────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN_TIME   = "09:15"   # Trading begins
ORB_END_TIME       = "09:35"   # ORB observation window ends (4 candles); signals start
TRADING_STOP_TIME  = "14:30"   # No new entries after 2:30 PM (avoid EOD volatility)
FORCE_EXIT_TIME    = "15:10"   # Close ALL positions at this time
MARKET_CLOSE_TIME  = "15:30"
MID_DAY_PAUSE_START = "12:00"  # No new entries during mid-day lull
MID_DAY_PAUSE_END   = "13:30"  # Resume scanning after 1:30 PM

# Polling interval in seconds (check every 5 minutes)
POLL_INTERVAL_SECS = 60

# ── NSE Holidays 2025-2026 ──────────────────────────────────────────────────
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr (Ramzan Eid)
    "2025-04-10",  # Shri Mahavir Jayanti
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Gandhi Jayanti / Dussehra
    "2025-10-21",  # Diwali Laxmi Pujan (Muhurat Trading – half day, skip)
    "2025-10-22",  # Diwali Balipratipada
    "2025-11-05",  # Prakash Gurpurb Sri Guru Nanak Dev Ji
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-03-20",  # Holi
    "2026-04-02",  # Ramzan Eid
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-09-17",  # Ganesh Chaturthi
    "2026-10-02",  # Gandhi Jayanti
    "2026-11-11",  # Diwali
    "2026-12-25",  # Christmas
}

# ── Email Notifications ──────────────────────────────────────────────────────
EMAIL_SENDER       = os.getenv("EMAIL_SENDER", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")
EMAIL_RECEIVER     = os.getenv("EMAIL_RECEIVER", "")
SMTP_HOST          = "smtp.gmail.com"
SMTP_PORT          = 587

# ── Stock Watchlist (Trading Symbols) ──────────────────────────────────────
# These trading symbols are resolved to Upstox instrument keys at startup
# via the Upstox instruments master file. This avoids hardcoding ISINs which
# can change due to corporate actions (mergers, demergers, renaming).
WATCHLIST_SYMBOLS = [
    # ── Nifty 50 large-caps ──────────────────────────────────────────────────
    "ADANIENT", "RELIANCE", "HDFCBANK", "INFY", "SBIN", "AXISBANK",
    "BAJFINANCE", "WIPRO", "TATASTEEL", "MARUTI", "SUNPHARMA", "POWERGRID",
    "ONGC", "DRREDDY", "ITC", "ICICIBANK", "KOTAKBANK", "HINDUNILVR",
    "ASIANPAINT", "LTIM", "TECHM", "BHARTIARTL", "HCLTECH", "ULTRACEMCO",
    "BPCL", "BAJAJFINSV", "COALINDIA", "GRASIM", "NTPC", "JSWSTEEL",
    "HINDALCO", "BAJAJ-AUTO",
    # ── Tata Motors demerged into TMCV + TMPV ────────────────────────────────
    "TMCV", "TMPV",
    # ── Mid-cap / high-volatility stocks ─────────────────────────────────────
    "ETERNAL",  # formerly ZOMATO
    "IRFC", "NHPC", "IDEA", "TATAPOWER", "PNB", "IRCTC",
    "NETWEB", "DIXON", "HDFCLIFE", "SBILIFE", "VEDL", "GAIL", "SAIL",
    "RECLTD", "INDUSINDBK", "TITAN",
]

# Instrument keys are populated at startup by resolve_instrument_keys()
INSTRUMENT_KEYS: list[str] = []


def resolve_instrument_keys() -> list[str]:
    """
    Download the Upstox NSE instruments master file and resolve
    WATCHLIST_SYMBOLS to their current instrument keys (NSE_EQ|<ISIN>).
    Updates INSTRUMENT_KEYS in-place and returns the list.
    """
    import gzip
    import json
    import logging

    _logger = logging.getLogger(__name__)

    instruments_url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    try:
        resp = requests.get(instruments_url, timeout=30)
        resp.raise_for_status()
        data = json.loads(gzip.decompress(resp.content))
    except Exception as exc:
        _logger.error("Failed to download Upstox instruments file: %s", exc)
        return []

    # Build symbol → instrument_key lookup for NSE equities
    symbol_to_key = {}
    for item in data:
        if item.get("segment") == "NSE_EQ" and item.get("instrument_type") == "EQ":
            symbol_to_key[item["trading_symbol"]] = item["instrument_key"]

    resolved = []
    for sym in WATCHLIST_SYMBOLS:
        key = symbol_to_key.get(sym)
        if key:
            resolved.append(key)
        else:
            _logger.warning("Symbol %s not found in Upstox instruments file — skipped", sym)

    _logger.info("Resolved %d / %d watchlist symbols to instrument keys", len(resolved), len(WATCHLIST_SYMBOLS))

    # Update the module-level list in-place
    global INSTRUMENT_KEYS
    INSTRUMENT_KEYS.clear()
    INSTRUMENT_KEYS.extend(resolved)
    return resolved

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE  = "logs/trading.log"
LOG_LEVEL = "INFO"
