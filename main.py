import csv
import json
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

import requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


load_dotenv()

app = FastAPI(title="TradingView Signal Relay", version="1.0.0")

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CSV_PATH = Path(os.getenv("CSV_PATH", DATA_DIR / "signals.csv"))
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_BASE = "https://api.telegram.org/bot"
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")

import uuid

CSV_COLUMNS = [
    "trade_id",
    "status",
    "received_at",
    "ticker",
    "timeframe",
    "side",
    "trigger_close",
    "entry_price",
    "sl",
    "tp_main",
    "tp1",
    "tp2",
    "tp3",
    "exit_price",
    "exit_reason",
    "exit_time",
    "telegram_sent",
    "telegram_error",
    "payload_json"
]

csv_lock = threading.Lock()


class SignalEnvelope(BaseModel):
    token: str | None = Field(default=None, description="Shared secret for webhook authentication")
    signal_type: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    timeframe: str | None = None
    price: float | None = None
    side: str | None = None
    strategy: str | None = None
    quantity: float | None = None
    message: str | None = None
    source: str | None = Field(default="pinescript")


def utc_now() -> str:
    return datetime.now(timezone(timedelta(hours=-4))).isoformat()


def get_csv_path(msg_type: str) -> Path:
    safe_type = "".join(c for c in msg_type if c.isalnum() or c in ("-", "_")).strip()
    if not safe_type:
        return CSV_PATH
    return DATA_DIR / f"signals_{safe_type.lower()}.csv"


def ensure_csv_file(path: Path) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {"message": str(payload)}


def authenticate(payload: dict[str, Any]) -> None:
    if not WEBHOOK_TOKEN:
        return
    token = str(payload.get("token") or "")
    # if token != WEBHOOK_TOKEN:
    #     raise HTTPException(status_code=401, detail="Invalid webhook token")


def telegram_message(payload: dict[str, Any]) -> str:
    msg_type = payload.get("type", "").upper()
    side = payload.get("side", "").upper()
    action = payload.get("action", "").lower()
    comment = payload.get("comment", "")
    ticker = payload.get("ticker", payload.get("symbol", ""))
    
    is_trigger = msg_type == "TRIGGER"
    is_entry = side == "LONG" or action == "buy"
    is_exit = side == "EXIT" or action in ["sell", "close"] or "Sl" in comment or "Tp" in comment

    if is_trigger:
        return f"🚨 TRIGGER ALERT 🚨\nTicker: {ticker}\nTimeframe: {payload.get('timeframe', '')}\nTrigger Close: {payload.get('trigger_close', '')}"
    elif is_entry:
        return f"🟢 LONG ENTRY 🟢\nTicker: {ticker}\nEntry Price: {payload.get('entry_price', payload.get('price', ''))}\nSL: {payload.get('sl', '')}\nTP Main: {payload.get('tp_main', '')}"
    elif is_exit:
        reason = comment or payload.get('reason', 'Exit signal')
        price = payload.get('price', payload.get('exit_price', ''))
        return f"🔴 EXIT 🔴\nTicker: {ticker}\nExit Price: {price}\nReason: {reason}"
    else:
        parts = ["TradingView signal received"]
        for k, v in payload.items():
            if k not in ["token", "payload_json"]:
                parts.append(f"{k}: {v}")
        return "\n".join(parts)


def send_telegram_alert(payload: dict[str, Any]) -> tuple[bool, str]:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "Telegram not configured"

    url = f"{TELEGRAM_API_BASE}{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": telegram_message(payload),
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if response.ok:
            return True, "sent"
        return False, response.text
    except Exception as exc:
        return False, str(exc)


def send_to_google_sheet(payload: dict[str, Any], telegram_sent: bool, telegram_error: str) -> None:
    if not GOOGLE_SHEET_URL:
        return
    try:
        data = dict(payload)
        data["telegram_sent"] = telegram_sent
        data["telegram_error"] = telegram_error
        requests.post(GOOGLE_SHEET_URL, json=data, timeout=10)
    except Exception:
        pass


def append_signal_row(payload: dict[str, Any], telegram_sent: bool, telegram_error: str) -> None:
    msg_type = (payload.get("type") or payload.get("signal_type") or "").strip()
    csv_path = get_csv_path(msg_type)
    ensure_csv_file(csv_path)
    
    msg_type_upper = msg_type.upper()
    side = payload.get("side", "").upper()
    action = payload.get("action", "").lower()
    comment = payload.get("comment", "")
    ticker = payload.get("ticker", payload.get("symbol", ""))
    
    is_trigger = msg_type_upper == "TRIGGER"
    is_entry = side == "LONG" or action == "buy"
    is_exit = side == "EXIT" or action in ["sell", "close"] or "Sl" in comment or "Tp" in comment
    
    with csv_lock:
        rows = []
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                rows = list(reader)
        
        if is_exit:
            updated = False
            for row in reversed(rows):
                if row.get("ticker") == ticker and row.get("status") == "Open":
                    row["status"] = "Closed"
                    row["exit_price"] = str(payload.get("price", payload.get("exit_price", "")))
                    row["exit_reason"] = comment or payload.get("reason", "Exit signal")
                    row["exit_time"] = utc_now()
                    updated = True
                    break
            
            if not updated:
                new_row = {col: "" for col in CSV_COLUMNS}
                new_row.update({
                    "trade_id": payload.get("trade_id") or str(uuid.uuid4()),
                    "status": "Orphaned Exit",
                    "received_at": utc_now(),
                    "ticker": ticker,
                    "exit_price": str(payload.get("price", payload.get("exit_price", ""))),
                    "exit_reason": comment or payload.get("reason", "Exit signal"),
                    "exit_time": utc_now(),
                    "payload_json": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                    "telegram_sent": str(telegram_sent),
                    "telegram_error": telegram_error
                })
                rows.append(new_row)
        else:
            new_row = {col: "" for col in CSV_COLUMNS}
            status = "Trigger" if is_trigger else ("Open" if is_entry else "Unknown")
            new_row.update({
                "trade_id": payload.get("trade_id") or str(uuid.uuid4()),
                "status": status,
                "received_at": utc_now(),
                "ticker": ticker,
                "timeframe": str(payload.get("timeframe", "")),
                "side": str(payload.get("side", "")),
                "trigger_close": str(payload.get("trigger_close", "")),
                "entry_price": str(payload.get("entry_price", payload.get("price", ""))),
                "sl": str(payload.get("sl", "")),
                "tp_main": str(payload.get("tp_main", "")),
                "tp1": str(payload.get("tp1", "")),
                "tp2": str(payload.get("tp2", "")),
                "tp3": str(payload.get("tp3", "")),
                "payload_json": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                "telegram_sent": str(telegram_sent),
                "telegram_error": telegram_error
            })
            rows.append(new_row)
            
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_pinescript(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")

    if raw_body:
        if "application/json" in content_type.lower():
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc.msg}") from exc
        else:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {"message": raw_body.decode("utf-8", errors="replace")}
    else:
        payload = {}

    normalized = normalize_payload(payload)
    authenticate(normalized)

    if "trade_id" not in normalized:
        normalized["trade_id"] = str(uuid.uuid4())

    if not normalized.get("message"):
        normalized["message"] = normalized.get("signal_type") or "TradingView signal"

    telegram_sent, telegram_status = send_telegram_alert(normalized)
    append_signal_row(normalized, telegram_sent=telegram_sent, telegram_error="" if telegram_sent else telegram_status)
    background_tasks.add_task(send_to_google_sheet, normalized, telegram_sent, "" if telegram_sent else telegram_status)

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "received_at": utc_now(),
            "telegram_sent": telegram_sent,
            "telegram_status": telegram_status,
        },
    )


@app.post("/webhook/signal")
async def webhook_signal(signal: SignalEnvelope, background_tasks: BackgroundTasks) -> JSONResponse:
    payload = signal.model_dump()
    authenticate(payload)
    
    if "trade_id" not in payload:
        payload["trade_id"] = str(uuid.uuid4())
        
    telegram_sent, telegram_status = send_telegram_alert(payload)
    append_signal_row(payload, telegram_sent=telegram_sent, telegram_error="" if telegram_sent else telegram_status)
    background_tasks.add_task(send_to_google_sheet, payload, telegram_sent, "" if telegram_sent else telegram_status)
    
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "received_at": utc_now(),
            "telegram_sent": telegram_sent,
            "telegram_status": telegram_status,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"status": "error", "detail": exc.detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")), reload=True)