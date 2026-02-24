"""
Central configuration for the Upstox Auto Trading Bot.
All environment variables are loaded here. Never hardcode secrets.
"""

import os
from dotenv import load_dotenv
import pytz

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
ORB_CANDLES         = 3       # number of 5-min candles for opening range (9:15–9:30 AM)
CANDLE_INTERVAL     = "1minute"   # fetched from API; resampled to 5-min in code
EMA_FAST            = 9
EMA_SLOW            = 21
RSI_PERIOD          = 14

# BUY signal: RSI must be between these values (momentum, not overbought)
RSI_BUY_MIN         = 40
RSI_BUY_MAX         = 75

# SELL/Short signal: RSI must be between these values (weakness, not oversold)
RSI_SELL_MIN        = 25
RSI_SELL_MAX        = 60

# Volume confirmation: current candle volume >= VOLUME_MULTIPLIER × 10-period avg
VOLUME_MULTIPLIER   = 1.2

# Per-trade stop loss and target (as % of entry price)
STOP_LOSS_PCT   = 0.005   # 0.5%
TARGET_PCT      = 0.010   # 1.0% → 2:1 reward:risk ratio

# Maximum open positions at a time (one per stock, multiple stocks allowed)
MAX_POSITIONS = 3

# How many top stocks to monitor simultaneously
TOP_N_STOCKS = 50

# Maximum open positions at a time
MAX_OPEN_POSITIONS = 1   # keep to 1 to stay within capital limits

# ── Market Timing (IST) ─────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN_TIME   = "09:15"   # Trading begins
ORB_END_TIME       = "09:30"   # ORB observation window ends; signals start
TRADING_STOP_TIME  = "15:02"   # No new positions after this time (grace period for 15:00 candle signals)
FORCE_EXIT_TIME    = "15:10"   # Close ALL positions at this time
MARKET_CLOSE_TIME  = "15:30"

# Polling interval in seconds (check every 5 minutes)
POLL_INTERVAL_SECS = 300

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

# ── Nifty 50 Instruments ────────────────────────────────────────────────────
# Upstox instrument keys for stocks to trade (NSE_EQ segment)
# Format: "NSE_EQ|<ISIN>"  — these are the correct Upstox v2 instrument keys
# Includes Nifty 50 large-caps + volatile mid-caps for more trading opportunities
INSTRUMENT_KEYS = [
    # ── Nifty 50 large-caps ──────────────────────────────────────────────────
    "NSE_EQ|INE467B01029",  # ADANIENT
    "NSE_EQ|INE002A01018",  # RELIANCE
    "NSE_EQ|INE040A01034",  # HDFCBANK
    "NSE_EQ|INE009A01021",  # INFY
    "NSE_EQ|INE062A01020",  # SBIN
    "NSE_EQ|INE585B01010",  # AXISBANK
    "NSE_EQ|INE018A01030",  # BAJFINANCE
    "NSE_EQ|INE244E01016",  # WIPRO
    "NSE_EQ|INE117A01022",  # TATAMOTORS
    "NSE_EQ|INE081A01020",  # TATASTEEL
    "NSE_EQ|INE019A01038",  # MARUTI
    "NSE_EQ|INE158A01026",  # SUNPHARMA
    "NSE_EQ|INE238A01034",  # POWERGRID
    "NSE_EQ|INE242A01010",  # ONGC
    "NSE_EQ|INE101A01026",  # DRREDDY
    "NSE_EQ|INE154A01025",  # ITC
    "NSE_EQ|INE090A01021",  # ICICIBANK
    "NSE_EQ|INE176A01028",  # KOTAKBANK
    "NSE_EQ|INE030A01027",  # HINDUNILVR
    "NSE_EQ|INE021A01026",  # ASIANPAINT
    "NSE_EQ|INE726G01019",  # LTIM (LTIMindtree)
    "NSE_EQ|INE669C01036",  # TECHM
    "NSE_EQ|INE397D01024",  # BHARTIARTL
    "NSE_EQ|INE003A01024",  # HCLTECH
    "NSE_EQ|INE481G01011",  # ULTRACEMCO
    "NSE_EQ|INE029A01011",  # BPCL
    "NSE_EQ|INE213A01029",  # BAJAJFINSV
    "NSE_EQ|INE121A01024",  # COALINDIA
    "NSE_EQ|INE044A01036",  # GRASIM
    "NSE_EQ|INE152A01029",  # NTPC
    "NSE_EQ|INE775A01035",  # JSWSTEEL
    "NSE_EQ|INE047A01021",  # HINDALCO
    "NSE_EQ|INE038A01020",  # BAJAJ-AUTO
    # ── Mid-cap / high-volatility stocks ─────────────────────────────────────
    "NSE_EQ|INE758T01015",  # ZOMATO
    "NSE_EQ|INE053F01010",  # IRFC
    "NSE_EQ|INE848E01016",  # NHPC
    "NSE_EQ|INE669E01016",  # IDEA (Vodafone Idea)
    "NSE_EQ|INE245A01021",  # TATAPOWER
    "NSE_EQ|INE160A01022",  # PNB
    "NSE_EQ|INE335Y01020",  # IRCTC
    "NSE_EQ|INE0NT901020",  # NETWEB
    "NSE_EQ|INE935N01020",  # DIXON
    "NSE_EQ|INE860A01027",  # HDFCLIFE
    "NSE_EQ|INE261F01014",  # SBILIFE
    "NSE_EQ|INE126A01031",  # VEDL
    "NSE_EQ|INE128A01029",  # GAIL
    "NSE_EQ|INE114A01011",  # SAIL
    "NSE_EQ|INE192A01025",  # RECLTD
    "NSE_EQ|INE020B01018",  # INDUSINDBK
    "NSE_EQ|INE774D01024",  # TITAN
]

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE  = "logs/trading.log"
LOG_LEVEL = "INFO"
