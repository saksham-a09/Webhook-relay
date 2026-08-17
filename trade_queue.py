import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trade_queue")


@dataclass
class ClientInfo:
    client_id: str
    account_login: Optional[str] = None
    broker: Optional[str] = None
    server: Optional[str] = None
    magic_number: Optional[int] = None
    last_seen: float = field(default_factory=time.time)
    ping_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_id": self.client_id,
            "account_login": self.account_login,
            "broker": self.broker,
            "server": self.server,
            "magic_number": self.magic_number,
            "last_seen": self.last_seen,
            "ping_ms": self.ping_ms,
            "seconds_since_last_seen": round(time.time() - self.last_seen, 2),
        }


@dataclass
class PendingTrade:
    trade_id: str
    payload: Dict[str, Any]
    target_client: str = "all"  # "all" or specific client_id
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = 60.0
    acknowledged_clients: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    seen_by_clients: Dict[str, float] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "payload": self.payload,
            "target_client": self.target_client,
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "is_expired": self.is_expired,
            "acknowledged_clients": self.acknowledged_clients,
        }


class TradeQueueManager:
    def __init__(self):
        self._clients: Dict[str, ClientInfo] = {}
        self._trades: Dict[str, PendingTrade] = {}
        self._trade_order: List[str] = []
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()

    def register_client(
        self,
        client_id: str,
        account_login: Optional[str] = None,
        broker: Optional[str] = None,
        server: Optional[str] = None,
        magic_number: Optional[int] = None,
        ping_ms: float = 0.0,
    ) -> ClientInfo:
        client = ClientInfo(
            client_id=client_id,
            account_login=account_login,
            broker=broker,
            server=server,
            magic_number=magic_number,
            last_seen=time.time(),
            ping_ms=ping_ms,
        )
        self._clients[client_id] = client
        logger.info(f"Registered MT5 EA client: {client_id} (Account: {account_login}, Broker: {broker})")
        return client

    def update_heartbeat(self, client_id: str, ping_ms: float = 0.0) -> bool:
        if client_id in self._clients:
            self._clients[client_id].last_seen = time.time()
            if ping_ms > 0:
                self._clients[client_id].ping_ms = ping_ms
            return True
        else:
            self.register_client(client_id, ping_ms=ping_ms)
            return True

    def get_active_clients(self, ttl_seconds: float = 60.0) -> List[Dict[str, Any]]:
        now = time.time()
        active = []
        for c in self._clients.values():
            if (now - c.last_seen) <= ttl_seconds:
                active.append(c.to_dict())
        return active

    async def enqueue_trade(self, payload: Dict[str, Any], target_client: str = "all", ttl_seconds: float = 60.0) -> str:
        trade_id = payload.get("trade_id") or str(uuid.uuid4())
        payload["trade_id"] = trade_id

        async with self._lock:
            trade = PendingTrade(
                trade_id=trade_id,
                payload=payload,
                target_client=target_client,
                created_at=time.time(),
                ttl_seconds=ttl_seconds,
            )
            self._trades[trade_id] = trade
            self._trade_order.append(trade_id)
            self._event.set()

        logger.info(f"Enqueued trade {trade_id} (Target: {target_client}, Side: {payload.get('side')}, Ticker: {payload.get('ticker') or payload.get('symbol')})")
        return trade_id

    def _get_unseen_trades_for_client(self, client_id: str) -> List[Dict[str, Any]]:
        result = []
        now = time.time()
        for trade_id in list(self._trade_order):
            trade = self._trades.get(trade_id)
            if not trade or trade.is_expired:
                continue
            # Check target filtering
            if trade.target_client != "all":
                target_list = [t.strip() for t in trade.target_client.split(",")]
                if client_id not in target_list:
                    continue
            # Check if this client already saw or acknowledged this trade
            if client_id in trade.seen_by_clients or client_id in trade.acknowledged_clients:
                continue
            trade.seen_by_clients[client_id] = now
            result.append(trade.payload)
        return result

    async def get_pending_trades(self, client_id: str, timeout: float = 20.0) -> List[Dict[str, Any]]:
        self.update_heartbeat(client_id)

        # First check if there's already an unseen trade available
        async with self._lock:
            unseen = self._get_unseen_trades_for_client(client_id)
            if unseen:
                return unseen

        # Otherwise, long-poll until a trade arrives or timeout occurs
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            remaining = timeout - (time.time() - start_time)
            if remaining <= 0:
                break

            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                pass

            self.update_heartbeat(client_id)
            async with self._lock:
                unseen = self._get_unseen_trades_for_client(client_id)
                if unseen:
                    return unseen

        return []

    async def acknowledge_trade(self, trade_id: str, client_id: str, ack_payload: Dict[str, Any]) -> Optional[PendingTrade]:
        async with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                logger.warning(f"ACK received for unknown or expired trade {trade_id} from client {client_id}")
                return None

            ack_entry = {
                "client_id": client_id,
                "status": ack_payload.get("status", "success"),
                "ticket": ack_payload.get("ticket"),
                "deal": ack_payload.get("deal"),
                "symbol": ack_payload.get("symbol"),
                "volume": ack_payload.get("volume"),
                "price": ack_payload.get("price"),
                "closed_tickets": ack_payload.get("closed_tickets", []),
                "error": ack_payload.get("error") or ack_payload.get("message"),
                "ack_at": time.time(),
            }

            trade.acknowledged_clients[client_id] = ack_entry
            logger.info(f"Received ACK for trade {trade_id} from {client_id}: status={ack_entry['status']}, ticket={ack_entry['ticket']}")
            return trade

    def get_trade(self, trade_id: str) -> Optional[PendingTrade]:
        return self._trades.get(trade_id)

    async def cleanup_expired(self, max_age_seconds: float = 600.0) -> int:
        now = time.time()
        removed = 0
        async with self._lock:
            to_remove = []
            for trade_id, trade in self._trades.items():
                if (now - trade.created_at) > max_age_seconds:
                    to_remove.append(trade_id)
            for tid in to_remove:
                del self._trades[tid]
                if tid in self._trade_order:
                    self._trade_order.remove(tid)
                removed += 1
        return removed


# Global trade queue manager instance
global_queue = TradeQueueManager()
