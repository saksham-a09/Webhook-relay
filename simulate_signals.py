#!/usr/bin/env python3
"""
TradingView Signal Simulator & Expected Outcomes Recorder

Simulates various TradingView webhook signal patterns over time against the
TradingView Relay Server and logs each sent signal along with its expected MT5
trade outcome into a CSV file.
"""

import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SERVER_URL = f"http://127.0.0.1:{os.getenv('PORT', '8000')}"
DEFAULT_TOKEN = os.getenv("WEBHOOK_TOKEN", "tok1122")
DEFAULT_CSV_PATH = Path(os.getenv("DATA_DIR", "data")) / "simulated_expected_trades.csv"

CSV_HEADERS = [
    "timestamp",
    "pattern_name",
    "step_description",
    "strategy",
    "symbol",
    "action",
    "side",
    "entry_price",
    "sl",
    "tp_main",
    "magic_number",
    "trade_id",
    "expected_mt5_outcome",
    "server_http_status",
    "server_response_status",
    "server_trade_id",
    "payload_json",
]


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = {
        "INFO": "\033[94m[INFO]\033[0m",
        "SUCCESS": "\033[92m[SUCCESS]\033[0m",
        "WARN": "\033[93m[WARN]\033[0m",
        "ERROR": "\033[91m[ERROR]\033[0m",
        "STEP": "\033[96m[STEP]\033[0m",
    }.get(level, f"[{level}]")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def init_csv(file_path: Path):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not file_path.exists():
        with file_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()


def append_csv_row(file_path: Path, row: Dict[str, Any]):
    with file_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writerow(row)


def send_signal(server_url: str, token: str, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    if token and "token" not in payload:
        payload["token"] = token

    url = f"{server_url.rstrip('/')}/webhook"
    try:
        res = requests.post(url, json=payload, timeout=15)
        try:
            res_json = res.json()
        except Exception:
            res_json = {"raw_text": res.text}
        return res.status_code, res_json
    except Exception as exc:
        return -1, {"error": str(exc)}


class PatternRunner:
    def __init__(self, server_url: str, token: str, csv_path: Path, step_delay: float):
        self.server_url = server_url
        self.token = token
        self.csv_path = csv_path
        self.step_delay = step_delay
        init_csv(self.csv_path)

    def execute_step(
        self,
        pattern_name: str,
        step_desc: str,
        payload: Dict[str, Any],
        expected_outcome: str,
    ):
        log(f"Running '{pattern_name}': {step_desc}", "STEP")
        log(f"Payload: {json.dumps(payload)}", "INFO")
        log(f"Expected MT5 Outcome: {expected_outcome}", "INFO")

        http_code, res_data = send_signal(self.server_url, self.token, payload)
        res_status = res_data.get("status", "unknown")
        returned_trade_id = res_data.get("trade_id", "")

        if http_code == 200:
            log(f"Server acknowledged signal (HTTP {http_code}) -> status: {res_status}, trade_id: {returned_trade_id}", "SUCCESS")
        else:
            log(f"Server returned HTTP {http_code}: {res_data}", "ERROR")

        now_iso = datetime.now(timezone.utc).isoformat()
        row = {
            "timestamp": now_iso,
            "pattern_name": pattern_name,
            "step_description": step_desc,
            "strategy": payload.get("strategy", ""),
            "symbol": payload.get("symbol", payload.get("ticker", "")),
            "action": payload.get("action", ""),
            "side": payload.get("side", ""),
            "entry_price": payload.get("entry_price", payload.get("price", "")),
            "sl": payload.get("sl", payload.get("stop_loss", "")),
            "tp_main": payload.get("tp_main", payload.get("tp", "")),
            "magic_number": payload.get("magic_number", payload.get("magic", "")),
            "trade_id": payload.get("trade_id", returned_trade_id),
            "expected_mt5_outcome": expected_outcome,
            "server_http_status": http_code,
            "server_response_status": res_status,
            "server_trade_id": returned_trade_id,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }
        append_csv_row(self.csv_path, row)

        time.sleep(self.step_delay)

    # -------------------------------------------------------------------------
    # Test Patterns
    # -------------------------------------------------------------------------

    def pattern_1_market_buy_and_exit(self):
        """Pattern 1: Market Buy Entry followed by Target Exit for Prena Trend Strategy."""
        strat = "Prena Trend"
        sym = "BTCUSD"
        base_price = 65000.0 + random.randint(-100, 100)

        # Step 1: Market Buy
        self.execute_step(
            pattern_name="Pattern 1 - Market Buy & Exit",
            step_desc="Open Market Buy on BTCUSD with SL/TP",
            payload={
                "strategy": strat,
                "ticker": sym,
                "action": "buy",
                "side": "LONG",
                "order_type": "market",
                "quantity": 0.01,
                "price": base_price,
                "sl": round(base_price - 800.0, 2),
                "tp_main": round(base_price + 1200.0, 2),
                "comment": f"{strat} Buy Signal",
            },
            expected_outcome=f"MT5 opens 0.01 BUY for {sym}, tagged with Strategy '{strat}' and hash magic.",
        )

        # Step 2: Exit Signal
        self.execute_step(
            pattern_name="Pattern 1 - Market Buy & Exit",
            step_desc="Exit Signal for BTCUSD",
            payload={
                "strategy": strat,
                "ticker": sym,
                "action": "close",
                "side": "EXIT",
                "reason": "Target 1 Hit",
                "comment": f"{strat} TP Hit",
            },
            expected_outcome=f"MT5 closes open positions for {sym} matching Strategy '{strat}'.",
        )

    def pattern_2_pending_order_and_cancel(self):
        """Pattern 2: Pending Buy Limit followed by Cancel All Orders for Breakout Pro Strategy."""
        strat = "Breakout Pro"
        sym = "XAUUSD"
        base_price = 2400.0 + random.randint(-10, 10)

        # Step 1: Pending Buy Limit below market
        self.execute_step(
            pattern_name="Pattern 2 - Pending Order & Cancel",
            step_desc="Place Pending Buy Limit on XAUUSD",
            payload={
                "strategy": strat,
                "symbol": sym,
                "action": "buy",
                "side": "BUY",
                "order_type": "limit",
                "quantity": 0.02,
                "entry_price": round(base_price - 25.0, 2),
                "sl": round(base_price - 50.0, 2),
                "tp_main": round(base_price + 40.0, 2),
                "comment": f"{strat} Limit",
            },
            expected_outcome=f"MT5 places Buy Limit order on {sym} @ {base_price - 25.0} tagged with Strategy '{strat}'.",
        )

        # Step 2: Cancel Pending Orders
        self.execute_step(
            pattern_name="Pattern 2 - Pending Order & Cancel",
            step_desc="Cancel All Pending Orders for Breakout Pro on XAUUSD",
            payload={
                "strategy": strat,
                "symbol": sym,
                "action": "cancel_all",
                "comment": f"{strat} Cancel Pending",
            },
            expected_outcome=f"MT5 cancels pending orders on {sym} for Strategy '{strat}' without touching other strategies.",
        )

    def pattern_3_multi_strategy_isolation(self):
        """Pattern 3: Two separate strategies on the same symbol, cancelling only one."""
        sym = "BTCUSD"
        strat_a = "Alpha Momentum"
        strat_b = "Beta Reversal"
        base_price = 65500.0

        # Step 1: Strategy Alpha places Buy Limit
        self.execute_step(
            pattern_name="Pattern 3 - Multi-Strategy Isolation",
            step_desc=f"Strategy '{strat_a}' places Buy Limit on {sym}",
            payload={
                "strategy": strat_a,
                "ticker": sym,
                "action": "buy",
                "side": "LONG",
                "order_type": "limit",
                "quantity": 0.01,
                "entry_price": base_price - 100.0,
                "sl": base_price - 600.0,
                "tp_main": base_price + 800.0,
                "comment": f"{strat_a} Buy Limit",
            },
            expected_outcome=f"MT5 places Buy Limit for Strategy '{strat_a}' with Alpha's magic number.",
        )

        # Step 2: Strategy Beta places Buy Limit on SAME symbol
        self.execute_step(
            pattern_name="Pattern 3 - Multi-Strategy Isolation",
            step_desc=f"Strategy '{strat_b}' places Buy Limit on {sym}",
            payload={
                "strategy": strat_b,
                "ticker": sym,
                "action": "buy",
                "side": "LONG",
                "order_type": "limit",
                "quantity": 0.01,
                "entry_price": base_price - 150.0,
                "sl": base_price - 700.0,
                "tp_main": base_price + 900.0,
                "comment": f"{strat_b} Buy Limit",
            },
            expected_outcome=f"MT5 places Buy Limit for Strategy '{strat_b}' with Beta's distinct magic number.",
        )

        # Step 3: Strategy Alpha issues CANCEL_ALL
        self.execute_step(
            pattern_name="Pattern 3 - Multi-Strategy Isolation",
            step_desc=f"Cancel all orders specifically for '{strat_a}'",
            payload={
                "strategy": strat_a,
                "ticker": sym,
                "action": "cancel_all",
            },
            expected_outcome=f"MT5 cancels ONLY Strategy '{strat_a}' orders. Strategy '{strat_b}' order remains ACTIVE!",
        )

        # Step 4: Clean up Strategy Beta
        self.execute_step(
            pattern_name="Pattern 3 - Multi-Strategy Isolation",
            step_desc=f"Cancel remaining orders for '{strat_b}'",
            payload={
                "strategy": strat_b,
                "ticker": sym,
                "action": "cancel_all",
            },
            expected_outcome=f"MT5 cancels Strategy '{strat_b}' pending order.",
        )

    def pattern_4_numeric_magic_isolation(self):
        """Pattern 4: Using explicit numeric magic numbers in payload."""
        sym = "EURUSD"
        magic_num = 778899

        # Step 1: Sell Limit with explicit magic
        self.execute_step(
            pattern_name="Pattern 4 - Explicit Numeric Magic Number",
            step_desc=f"Place Sell Limit with magic_number={magic_num}",
            payload={
                "symbol": sym,
                "action": "sell",
                "side": "SHORT",
                "order_type": "limit",
                "quantity": 0.05,
                "entry_price": 1.1250,
                "sl": 1.1350,
                "tp_main": 1.1050,
                "magic_number": magic_num,
                "comment": "Numeric Magic Order",
            },
            expected_outcome=f"MT5 places Sell Limit on {sym} with exact magic number {magic_num}.",
        )

        # Step 2: Cancel orders with matching magic
        self.execute_step(
            pattern_name="Pattern 4 - Explicit Numeric Magic Number",
            step_desc=f"Cancel all orders matching magic_number={magic_num}",
            payload={
                "symbol": sym,
                "action": "cancel_all_orders",
                "magic_number": magic_num,
            },
            expected_outcome=f"MT5 deletes orders matching magic number {magic_num}.",
        )

    def pattern_5_close_all_positions_across_symbols(self):
        """Pattern 5: Close all positions across symbols for a given strategy."""
        strat = "Portfolio Scalper"

        # Step 1: Buy on BTCUSD
        self.execute_step(
            pattern_name="Pattern 5 - Global Strategy Close All",
            step_desc=f"Strategy '{strat}' opens Market Buy on BTCUSD",
            payload={
                "strategy": strat,
                "symbol": "BTCUSD",
                "action": "buy",
                "side": "BUY",
                "quantity": 0.01,
                "comment": f"{strat} BTC",
            },
            expected_outcome=f"MT5 opens BTCUSD position for Strategy '{strat}'.",
        )

        # Step 2: Buy on XAUUSD
        self.execute_step(
            pattern_name="Pattern 5 - Global Strategy Close All",
            step_desc=f"Strategy '{strat}' opens Market Buy on XAUUSD",
            payload={
                "strategy": strat,
                "symbol": "XAUUSD",
                "action": "buy",
                "side": "BUY",
                "quantity": 0.01,
                "comment": f"{strat} GOLD",
            },
            expected_outcome=f"MT5 opens XAUUSD position for Strategy '{strat}'.",
        )

        # Step 3: Global Close All for this strategy across ALL symbols
        self.execute_step(
            pattern_name="Pattern 5 - Global Strategy Close All",
            step_desc=f"Global CLOSE_ALL for Strategy '{strat}' across all symbols",
            payload={
                "strategy": strat,
                "symbol": "ALL",
                "action": "close_all",
            },
            expected_outcome=f"MT5 closes all open positions across all symbols for Strategy '{strat}'.",
        )

    def run_all_patterns(self, iteration: int = 1):
        log(f"=== Starting Test Cycle #{iteration} ===", "INFO")
        self.pattern_1_market_buy_and_exit()
        self.pattern_2_pending_order_and_cancel()
        self.pattern_3_multi_strategy_isolation()
        self.pattern_4_numeric_magic_isolation()
        self.pattern_5_close_all_positions_across_symbols()
        log(f"=== Completed Test Cycle #{iteration} ===", "SUCCESS")


def main():
    parser = argparse.ArgumentParser(description="TradingView Signal Simulator & CSV Expected Outcomes Recorder")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help=f"Server base URL (default: {DEFAULT_SERVER_URL})")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help=f"Webhook authentication token (default: {DEFAULT_TOKEN})")
    parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH), help=f"Output CSV path (default: {DEFAULT_CSV_PATH})")
    parser.add_argument("--interval", type=float, default=3.0, help="Delay in seconds between signal steps (default: 3.0s)")
    parser.add_argument("--cycle-delay", type=float, default=8.0, help="Delay in seconds between complete cycles (default: 8.0s)")
    parser.add_argument("--cycles", type=int, default=0, help="Number of complete pattern cycles to run (0 = continuous, default: 0)")

    args = parser.parse_args()

    csv_path = Path(args.csv)
    log("===================================================================", "INFO")
    log("  TradingView Signal Simulator & Strategy Outcomes Recorder", "INFO")
    log(f"  Target Server: {args.server}", "INFO")
    log(f"  Webhook Token: {args.token}", "INFO")
    log(f"  CSV Output:    {csv_path.resolve()}", "INFO")
    log(f"  Step Delay:    {args.interval}s", "INFO")
    log(f"  Cycles:        {'Continuous (Infinite)' if args.cycles <= 0 else args.cycles}", "INFO")
    log("===================================================================", "INFO")

    runner = PatternRunner(
        server_url=args.server,
        token=args.token,
        csv_path=csv_path,
        step_delay=args.interval,
    )

    cycle = 1
    try:
        while True:
            runner.run_all_patterns(iteration=cycle)
            if args.cycles > 0 and cycle >= args.cycles:
                log(f"Finished running {args.cycles} cycles. Output saved to {csv_path}", "SUCCESS")
                break
            cycle += 1
            log(f"Waiting {args.cycle_delay}s before starting next cycle...", "INFO")
            time.sleep(args.cycle_delay)
    except KeyboardInterrupt:
        log("Simulation stopped by user. CSV results preserved.", "WARN")


if __name__ == "__main__":
    main()
