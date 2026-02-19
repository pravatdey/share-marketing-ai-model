# Upstox Auto Trading Bot

Automated intraday stock trading bot for the Indian market using the **Upstox API v2**.

- **Strategy**: Opening Range Breakout (ORB) + EMA + RSI
- **Daily profit target**: ₹50 (bot stops all trading once reached)
- **Daily max loss guard**: ₹50 (bot stops and exits if total loss hits limit)
- **Exit time**: All positions force-closed at 3:10 PM IST (before 3:30 PM market close)
- **Runs via**: GitHub Actions – fully automatic every weekday

---

## How It Works

```
9:10 AM IST  →  GitHub Actions starts, bot authenticates with Upstox
9:15 AM IST  →  Market opens – bot observes first 3 candles (9:15–9:30 AM)
9:30 AM IST  →  Opening Range established – bot scans for breakout every 5 min
↓               BUY signal → MARKET order placed via Upstox API
↓               Position monitored for: Stop loss | Target | EMA reversal
3:00 PM IST  →  No new BUY entries
3:10 PM IST  →  ALL positions force-closed (exits before market close)
3:15 PM IST  →  Daily summary sent to your email
```

**Buy signal requires ALL four conditions to be true:**
1. Price breaks **above** the Opening Range High
2. EMA(9) is above EMA(21) → uptrend confirmed
3. RSI between 45–70 → momentum without overbought risk
4. Volume > 1.5× average → genuine breakout, not a fake-out

---

## Setup Guide

### Step 1 – Create a Free Upstox API App

1. Go to [developer.upstox.com](https://developer.upstox.com)
2. Login with your Upstox account credentials
3. Click **"Create New App"**
4. Fill in:
   - App Name: `AutoTradingBot`
   - Redirect URL: `https://127.0.0.1/upstox-callback`
5. After creation, copy your **API Key** and **API Secret**

### Step 2 – Enable TOTP in Your Upstox App

1. Open the Upstox mobile app
2. Go to: **Profile → My Account → Enable TOTP**
3. You will see a **secret key** (32-character code) – copy this
4. This is your `UPSTOX_TOTP_SECRET`

### Step 3 – Get Gmail App Password (for email notifications)

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Enable **2-Step Verification** (required for App Passwords)
3. Under Security → **App passwords** → create a new one
4. Copy the 16-character password – this is your `EMAIL_APP_PASSWORD`

### Step 4 – Create GitHub Repository

1. Create a **new PUBLIC repository** on GitHub (public = unlimited free Actions minutes)
2. Upload all files from this project to the repository
3. Add a `.gitignore` that includes `.env` (never commit secrets!)

### Step 5 – Add GitHub Secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

Add each of these secrets:

| Secret Name | Value | Where to get it |
|---|---|---|
| `UPSTOX_API_KEY` | Your API Key | Upstox Developer Portal |
| `UPSTOX_API_SECRET` | Your API Secret | Upstox Developer Portal |
| `UPSTOX_REDIRECT_URI` | `https://127.0.0.1/upstox-callback` | Same as what you set in app |
| `UPSTOX_MOBILE` | Your 10-digit mobile number | Your Upstox registered mobile |
| `UPSTOX_PIN` | Your 6-digit Upstox PIN | Your Upstox app PIN |
| `UPSTOX_TOTP_SECRET` | 32-char secret key | From Upstox TOTP setup |
| `TRADING_CAPITAL` | `2500` | Your available capital in ₹ |
| `DAILY_PROFIT_TARGET` | `50` | ₹50 profit target |
| `DAILY_MAX_LOSS` | `50` | ₹50 max loss limit |
| `EMAIL_SENDER` | `yourname@gmail.com` | Your Gmail address |
| `EMAIL_APP_PASSWORD` | 16-char app password | From Google Account |
| `EMAIL_RECEIVER` | `yourname@gmail.com` | Where to receive alerts |

### Step 6 – Enable GitHub Actions

1. Go to your repo → **Actions** tab
2. GitHub Actions is enabled by default on new repos
3. The bot will run automatically every weekday at **9:10 AM IST**
4. You can also click **"Run workflow"** manually to test it

---

## How to Monitor the Bot

### Option A – Upstox App (easiest)
Open your **Upstox mobile app** → **Orders / Positions** tab.
You will see every trade the bot places in real time.

### Option B – Email Notifications
You will receive emails for:
- Bot started
- Stock selected for the day
- Every BUY order placed
- Every SELL order (with profit/loss)
- When ₹50 profit target is hit
- Daily summary at end of day

### Option C – GitHub Actions Logs
1. Go to your GitHub repo → **Actions** tab
2. Click on today's workflow run
3. Expand the **"Run trading bot"** step
4. See every decision the bot made (with timestamps)
5. After the run, download the **trading log artifact** for the full log file

---

## Project Structure

```
share-marketing-ai-model/
├── .github/
│   └── workflows/
│       └── trading.yml        ← GitHub Actions schedule
├── src/
│   ├── auth.py                ← Upstox login (Selenium + TOTP)
│   ├── market_data.py         ← Fetch candles, compute indicators
│   ├── strategy.py            ← ORB + EMA + RSI buy/sell signals
│   ├── order_manager.py       ← Place orders via Upstox API
│   ├── risk_manager.py        ← Track P&L, profit/loss guards
│   ├── stock_selector.py      ← Pick best Nifty 50 stock today
│   └── notifier.py            ← Email notifications
├── config/
│   └── settings.py            ← All configuration (reads .env)
├── logs/
│   └── trading.log            ← Generated at runtime
├── main.py                    ← Entry point
├── requirements.txt
├── .env.example               ← Template (copy to .env for local testing)
└── README.md
```

---

## Important Disclaimer

> **Stock trading involves financial risk. Past performance does not guarantee future results.**
>
> This bot uses a stop loss of ₹50/day to protect your capital. On bad market days,
> the bot may close positions at a small loss rather than hold them overnight.
> This is intentional – protecting your ₹2,000-3,000 capital is more important
> than chasing a ₹50 profit on a losing day.
>
> Start by testing with **Paper Trading** mode in Upstox before using real money.

---

## Local Testing (Optional)

```bash
# 1. Clone the repo
git clone https://github.com/yourusername/share-marketing-ai-model.git
cd share-marketing-ai-model

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your .env file
cp .env.example .env
# Edit .env and fill in your credentials

# 4. Run the bot
python main.py
```
