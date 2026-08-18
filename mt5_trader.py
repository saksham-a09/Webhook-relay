import os
import logging
import asyncio
from typing import Any, Dict
import trade_queue

logger = logging.getLogger("mt5_trader")
logging.basicConfig(level=logging.INFO)


def is_mt5_enabled() -> bool:
    val = os.getenv("MT5_ENABLED", "true").lower()
    return val in ("true", "1", "yes")


def init_mt5() -> bool:
    if not is_mt5_enabled():
        logger.info("MT5 execution is disabled in configuration.")
        return False

    logger.info("MT5 Relay running in Server Mode (EA clients poll trade queue).")
    return True


def format_telegram_mt5_summary(mt5_result: Dict[str, Any]) -> str:
    """Formats MT5 execution results for Telegram alerts."""
    if not mt5_result or mt5_result.get("status") == "disabled":
        return ""

    status = mt5_result.get("status")
    if status == "queued":
        return f"\n\n[MT5 Status]: Signal queued for EA execution (ID: {mt5_result.get('trade_id')})"

    if status == "success":
        acks = mt5_result.get("client_acks")
        if acks and isinstance(acks, dict):
            lines = ["\n\n[MT5 Trade Executed Across Clients]"]
            for cid, ack in acks.items():
                if ack.get("ticket"):
                    lines.append(f"• Client {cid}: Ticket #{ack.get('ticket')} @ {ack.get('price', 'N/A')}")
                elif ack.get("closed_tickets"):
                    lines.append(f"• Client {cid}: Closed Tickets {ack.get('closed_tickets')}")
                elif ack.get("status") == "error":
                    lines.append(f"• Client {cid}: Error: {ack.get('error')}")
            return "\n".join(lines)

    elif status == "error":
        return f"\n\n[MT5 Execution Error]: {mt5_result.get('message')}"

    return ""


def execute_trade(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not is_mt5_enabled():
        return {"status": "disabled", "message": "MT5 is disabled in settings"}

    target_raw = payload.get("target_client") if payload.get("target_client") is not None else payload.get("account_id")
    if target_raw is None or target_raw == "":
        target_client = "all"
    elif isinstance(target_raw, (list, tuple, set)):
        target_client = ",".join(str(x).strip() for x in target_raw if str(x).strip())
    else:
        target_client = str(target_raw).strip()

    default_vol = float(os.getenv("MT5_DEFAULT_VOLUME", "0.01"))
    if "quantity" not in payload and "volume" not in payload:
        payload["quantity"] = default_vol

    trade_id = payload.get("trade_id")
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(trade_queue.global_queue.enqueue_trade(payload, target_client=target_client))
    except RuntimeError:
        # Fallback if outside event loop
        asyncio.run(trade_queue.global_queue.enqueue_trade(payload, target_client=target_client))

    return {
        "status": "queued",
        "trade_id": trade_id,
        "target_client": target_client,
        "message": "Signal queued for MT5 EA clients",
    }
