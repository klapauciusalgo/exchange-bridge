# MEXC ↔ Telegram Trading Bridge

A secure, high-performance bridge connecting **Telegram** with **MEXC Futures** for real-time monitoring, risk-controlled manual execution, SL/TP management, and proactive liquidation alerts.

---

## 🚀 Key Features

### 🛡️ 1. Security & Risk First (#1 Priority)
- **Telegram User Whitelist:** Commands from non-whitelisted user IDs are strictly rejected.
- **Pre-Trade Confirmation Cards:** Every state-changing order (`/open`, `/close`, `/setsl`, `/settp`, `/panic`) presents a detailed preview with inline **Confirm** & **Cancel** buttons.
- **Race Condition & Double-Click Protection:** Atomic token locking ensures an action cannot be submitted twice.
- **Encrypted Secret Store:** API credentials can be encrypted at rest with Fernet symmetric encryption.
- **Redacting Logging Filter:** API keys, secrets, tokens, and PINs are stripped from all log outputs.
- **Risk Engine Validation:**
  - Max leverage limits (e.g. max 20x).
  - Max position size / equity allocation percentage.
  - Daily loss limit (realized loss + unrealized drawdown lockout until 00:00 UTC).
  - Assessment Zone symbol blocking.
  - SL/TP direction sanity validation (Long SL < entry < TP; Short TP < entry < SL).
- **Emergency Kill Switch (`/panic` or `/closeall`):** Instantly cancels all limit & trigger plan orders and market-closes all active positions.

### 📊 2. Real-Time Market & Account Data
- **Official WebSocket Integration:** Real-time ticker streaming, order fills, position tracking, and account asset caching.
- **State Reconciliation:** Automatic resynchronization via REST when WebSocket reconnects.
- **Internal Rate Limiter:** Sliding-window limiter prevents exceeding MEXC API limits (20 req / 2s).

### 🔔 3. Proactive Notifications & Alerts
- Order fill & cancellation alerts.
- **Liquidation Risk Warning:** Alerts when mark price comes within 15% of liquidation price.
- **High Funding Rate Alerts:** Alerts on open positions subject to extreme funding fees.
- **Watchlist Price Alerts:** Custom price triggers (`/watch <symbol> <above|below> <price>`).
- Fallback Webhook support for critical liquidation alerts if Telegram is unreachable.

---

## 📋 Telegram Commands

| Command | Description | Example |
|---|---|---|
| `/balance` | View total equity, available margin, and today's PnL | `/balance` |
| `/price <symbol>` | Real-time price, 24h change, high/low & volume | `/price BTC` |
| `/market <symbol>` | Funding rate, settlement countdown & mark price | `/market ETH` |
| `/orderbook <symbol>` | Top bid and ask orderbook depth | `/orderbook SOL` |
| `/positions` | List open positions with unrealized PnL & SL/TP | `/positions` |
| `/orders` | List open limit orders and plan trigger orders | `/orders` |
| `/open ...` | Open new position with preview & confirmation | `/open BTC long 100 10 market sl=64000 tp=72000` |
| `/close <symbol> [%]` | Close position (partial or 100%) | `/close BTC 50` |
| `/setsl <symbol> <price>` | Place/update Stop Loss trigger order | `/setsl BTC 63500` |
| `/settp <symbol> <price>` | Place/update Take Profit trigger order | `/settp BTC 71000` |
| `/cancel <order_id>` | Cancel specific open or plan order | `/cancel ord_123` |
| `/panic` or `/closeall` | 🚨 Emergency kill switch (close all & cancel all) | `/panic confirm` |
| `/watch <sym> <cond> <px>` | Add price alert to watchlist | `/watch BTC above 70000` |
| `/watchlist` | View all active price alerts | `/watchlist` |
| `/unwatch <id>` | Delete active price alert | `/unwatch 1` |
| `/risklimit` | View or adjust risk limits | `/risklimit 20 30 100` |
| `/dryrun [on\|off]` | Toggle simulation / dry-run mode | `/dryrun on` |
| `/auth <pin>` | Unlock trading session if PIN is enabled | `/auth 1234` |
| `/help` | Display command usage guide | `/help` |

---

## 🛠️ Installation & Setup

### 1. Prerequisites
- Python 3.12+
- MEXC Futures API Key & Secret (with **Futures Trading** permission only, **NO Withdrawal**)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Telegram User ID (from [@userinfobot](https://t.me/userinfobot))

### 2. Clone & Install Dependencies
```bash
git clone <repo-url> mexc-tg
cd mexc-tg
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_WHITELISTED_USERS=123456789
MEXC_API_KEY=your_mexc_futures_api_key
MEXC_SECRET_KEY=your_mexc_futures_secret_key
DRY_RUN=false
MAX_LEVERAGE=20
MAX_POSITION_EQUITY_PCT=30.0
DAILY_LOSS_LIMIT_USDT=100.0
```

### 4. Running the Bot
```bash
python3 main.py
```

---

## 🧪 Testing & Automated Iteration

The repository includes a comprehensive test suite covering unit tests, risk engine edge cases, security encryption, rate limiting, and end-to-end multi-step simulation loops:

```bash
# Run all tests
python3 -m pytest -v

# Run iterative trading simulation loop test
python3 -m pytest tests/test_simulation_loop.py -v -s
```

---

## 🏗️ Architecture

```
[Telegram User] 
       │ (Commands / Inline Confirmation Callbacks)
       ▼
[Telegram Bot API (python-telegram-bot async)]
       │
[Middleware & Whitelist Filter] ───► [SQLite Audit Log / Orders Log]
       │
[Risk Management Engine] (Leverage, Margin %, Daily Loss Limit, SL/TP checks)
       │
[MEXC Client Layer]
  ├── REST Client (HMAC-SHA256 Signed, Token Bucket Rate Limiter)
  ├── WebSocket Client (Public Tickers & Private Fills/Positions/Assets)
  └── State Reconciler (Auto-sync on reconnect)
       │
[Notification Service] ───► [Telegram Alerts + Fallback Webhook]
  (Order Fills, SL/TP triggers, Liquidation Risk Warning, Funding Warnings)
```

---

## 🔒 Security Best Practices
1. **Never grant Withdrawal permissions** to the MEXC API Key.
2. **Whitelist Server IP** in the MEXC API Key settings.
3. Keep `.env` out of version control (`.gitignore`).
4. Keep `TELEGRAM_WHITELISTED_USERS` restricted to your Telegram User ID only.
