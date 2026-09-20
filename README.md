# TradingView Signal Relay & Multi-EA MT5 Server

FastAPI server that receives TradingView / Pine Script webhook alerts, forwards signals to multiple MetaTrader 5 Expert Advisors (EAs) with **minimal latency**, sends Telegram notifications, and logs trade execution to CSV / Google Sheets.

---

## ⚡ Architecture: Minimal Latency Client-Server Setup

Instead of requiring MetaTrader 5 to be installed on the same machine as Python, this system uses a **Client-Server Trade Queue architecture**:

1. **Python Relay Server**: Receives webhooks, normalizes signals, enqueues trades in an in-memory queue, and exposes a high-performance **HTTP Long-Polling API**.
2. **MT5 Client EA (`mql5/TradingViewRelayClient.mq5`)**: Pure MQL5 Expert Advisor running on MT5 terminals (local or remote VPS). Uses native `WebRequest()` to long-poll the Python server (< 1 ms signal delivery latency). Supports **multiple concurrent EAs** on different accounts or brokers.

```
                                      ┌───► MT5 EA #1 (Account A - Broker X)
TradingView ──► Python FastAPI ──────┼───► MT5 EA #2 (Account B - Broker Y)
  Webhook       Server (Queue)        └───► MT5 EA #N (Account N - VPS)
                      │
                      ├───► Telegram Alerts (Aggregated Execution Status)
                      └───► CSV & Google Sheets Logging
```

---

## 🚀 Quick Setup Guide

### 1. Python Server Installation

```bash
# Clone repository and create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Create environment configuration
cp .env.example .env
```

Set configuration in `.env`:
```ini
MT5_ENABLED=true
LONG_POLL_TIMEOUT=20.0
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Start the server:
```bash
python main.py
# Server runs on http://0.0.0.0:8000
```

---

### 2. MetaTrader 5 Expert Advisor Setup

1. Open MetaTrader 5 terminal.
2. Enable WebRequest permissions:
   - Go to **Tools -> Options -> Expert Advisors**.
   - Check **Allow WebRequest for listed URL**.
   - Add your server URL: `http://127.0.0.1:8000` (or `http://YOUR_SERVER_IP:8000`).
3. Open MetaEditor (**F4** in MT5).
4. Copy `mql5/TradingViewRelayClient.mq5` into MetaEditor `Experts/` directory.
5. Click **Compile** (**F7**). Verify zero errors.
6. Drag `TradingViewRelayClient` onto any chart in MT5.
7. Configure EA inputs:
   - `InpServerURL`: `http://127.0.0.1:8000`
   - `InpClientID`: `"AUTO"` (uses Account Login automatically) or custom string.
   - `InpSymbolMap`: `"BTCUSD:BTCUSD.a,XAUUSD:GOLD"` (maps TV symbol to broker symbol).
8. Ensure **Algo Trading** button is enabled in MT5 toolbar.

---

## 📡 API Endpoints Summary

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Server health check and active EA count |
| POST | `/webhook` | Accepts raw TradingView JSON webhooks |
| POST | `/webhook/signal` | Structured PineScript envelope webhook |
| GET | `/api/v1/trades/pending` | Async HTTP long-polling endpoint for MT5 EAs |
| POST | `/api/v1/trades/ack` | Execution ACK postback from MT5 EAs |
| POST | `/api/v1/clients/heartbeat` | EA client heartbeat & latency monitor |
| GET | `/api/v1/clients` | View list of all connected MT5 EA clients |

---

## 🎯 Targeted or Broadcast Webhook Routing

By default, every incoming webhook is **broadcast to all connected MT5 EAs** (`target_client="all"`).

If you want a trade to be executed only on a specific account or EA instance, pass `target_client` or `account_id` in your webhook JSON payload:

```json
{
  "ticker": "BTCUSD",
  "side": "LONG",
  "action": "buy",
  "order_type": "limit",
  "quantity": 0.05,
  "entry_price": 64500.0,
  "sl": 64000.0,
  "tp_main": 70000.0,
  "target_client": "MT5_1002938"
}
```

### ⚡ Order Execution Modes (`order_type`)

Signals can explicitly control how MT5 executes the order via the `"order_type"` field:

- `"market"`: Immediate market execution at current Bid/Ask.
- `"pending"`: Forces pending order at `entry_price` (auto Limit or Stop based on price level).
- `"limit"`: Places explicit Buy/Sell Limit order at `entry_price`.
- `"stop"`: Places explicit Buy/Sell Stop order at `entry_price`.
- *(Omitted)*: EA auto-decides — market order unless `entry_price` distance exceeds `InpPendingOrderThresholdPoints` (default 50 pts).

---

## 📊 Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `MT5_ENABLED` | `true` | Enable MT5 signal routing |
| `LONG_POLL_TIMEOUT` | `20.0` | Long-poll duration in seconds |
| `WEBHOOK_TOKEN` | `""` | Shared secret token for webhook & API security |
| `ENFORCE_TOKEN` | `false` | Enforce header/body token verification |
| `TELEGRAM_BOT_TOKEN` | `""` | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | `""` | Telegram chat ID for trade notifications |
| `GOOGLE_SHEET_URL` | `""` | Optional Google Apps Script Web App URL |

---

## 🧪 Running Unit Tests

Run the complete test suite:
```powershell
python -m unittest discover -s tests
```