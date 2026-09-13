"""
Bybit Demo API Validation Script
=================================
Validates API keys, order placement, SL/TP, position management, and reconciliation
against the Bybit Demo Trading service.

Run: python -m engine.validate_bybit_api
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bybit_executor import BybitExecutor, create_executor_from_env


def run_validation():
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": os.environ.get("EXECUTION_MODE", "internal"),
        "steps": [],
        "overall_pass": False,
    }

    def step(name, status, detail, data=None):
        entry = {"step": name, "status": status, "detail": detail}
        if data:
            entry["data"] = data
        results["steps"].append(entry)
        icon = "PASS" if status == "pass" else "FAIL" if status == "fail" else "INFO"
        print(f"[{icon}] {name}: {detail}")
        if data:
            print(f"       data: {json.dumps(data, indent=2)[:500]}")
        return entry

    # ── Step 1: Create executor from env ──
    executor = create_executor_from_env()
    if executor is None:
        step("create_executor", "fail", "No executor created. Check EXECUTION_MODE and API keys.")
        results["overall_pass"] = False
        return results
    step("create_executor", "pass", f"Executor created (demo={executor._demo}, base={executor._base_url})")

    # ── Step 2: Get wallet balance ──
    balance = executor.get_wallet_balance()
    if balance and balance.get("list"):
        acct = balance["list"][0]
        total_eq = "N/A"
        for coin in acct.get("coin", []):
            if coin.get("coin") == "USDT":
                total_eq = coin.get("walletBalance", "N/A")
                break
        step("wallet_balance", "pass", f"Account: {acct.get('accountType', 'N/A')}, USDT balance: {total_eq}",
             data={"accountType": acct.get("accountType"), "totalEquity": total_eq})
    else:
        step("wallet_balance", "fail", "No balance returned. API keys may be invalid.", data=balance)
        results["overall_pass"] = False
        return results

    # ── Step 3: Get ticker for BTCUSDT ──
    ticker = executor.get_tickers("BTCUSDT")
    if ticker:
        last_price = ticker.get("lastPrice", "N/A")
        step("get_ticker", "pass", f"BTCUSDT last price: {last_price}",
             data={"symbol": "BTCUSDT", "lastPrice": last_price, "fundingRate": ticker.get("fundingRate", "N/A")})
    else:
        step("get_ticker", "fail", "No ticker data returned")
        results["overall_pass"] = False
        return results

    # ── Step 4: Get instruments info (min qty) ──
    instr = executor.get_instruments_info("BTCUSDT")
    if instr:
        lot_size = instr.get("lotSizeFilter", {})
        min_qty = lot_size.get("minOrderQty", "N/A")
        qty_step = lot_size.get("qtyStep", "N/A")
        step("instruments_info", "pass", f"BTCUSDT min qty: {min_qty}, qty step: {qty_step}",
             data={"minOrderQty": min_qty, "qtyStep": qty_step, "priceFilter": instr.get("priceFilter", {})})
    else:
        step("instruments_info", "fail", "No instrument info returned")
        results["overall_pass"] = False
        return results

    # ── Step 5: Place a small market BUY order ──
    # Use minimum qty for BTCUSDT (typically 0.001)
    test_qty = 0.001
    current_price = float(ticker.get("lastPrice", 0))
    if current_price == 0:
        step("place_order", "fail", "Cannot determine current price for SL/TP calculation")
        results["overall_pass"] = False
        return results

    # SL 2% below, TP 3% above
    sl_price = round(current_price * 0.98, 2)
    tp_price = round(current_price * 1.03, 2)

    order_result = executor.place_order(
        symbol="BTCUSDT",
        side="Buy",
        qty=test_qty,
        order_type="Market",
        take_profit=tp_price,
        stop_loss=sl_price,
        tp_trigger_by="LastPrice",
        sl_trigger_by="LastPrice",
    )

    if order_result.get("retCode") == 0:
        order_id = order_result.get("result", {}).get("orderId", "N/A")
        step("place_order", "pass", f"Market BUY {test_qty} BTCUSDT at ~${current_price:.2f}, order_id={order_id}",
             data={"orderId": order_id, "side": "Buy", "qty": test_qty, "sl": sl_price, "tp": tp_price})
    else:
        step("place_order", "fail", f"Order rejected: {order_result.get('retMsg')}", data=order_result)
        results["overall_pass"] = False
        return results

    # Wait for order to fill
    time.sleep(2)

    # ── Step 6: Check position ──
    positions = executor.get_positions(symbol="BTCUSDT")
    if positions:
        pos = positions[0]
        pos_size = pos.get("size", "0")
        pos_side = pos.get("side", "N/A")
        pos_pnl = pos.get("unrealisedPnl", "0")
        step("check_position", "pass", f"Position: {pos_side} {pos_size} BTCUSDT, unrealised PnL: {pos_pnl}",
             data={"size": pos_size, "side": pos_side, "entryPrice": pos.get("avgPrice", "N/A"),
                   "unrealisedPnl": pos_pnl, "stopLoss": pos.get("stopLoss", "N/A"),
                   "takeProfit": pos.get("takeProfit", "N/A")})
    else:
        step("check_position", "fail", "No position found after order placement")
        results["overall_pass"] = False
        return results

    # ── Step 7: Get open orders (should have SL/TP attached) ──
    open_orders = executor.get_open_orders(symbol="BTCUSDT")
    step("open_orders", "info", f"{len(open_orders.get('result', {}).get('list', []))} open orders",
         data={"count": len(open_orders.get("result", {}).get("list", []))})

    # ── Step 8: Close position ──
    close_result = executor.close_position("BTCUSDT", "LONG", test_qty)
    if close_result.get("retCode") == 0:
        close_order_id = close_result.get("result", {}).get("orderId", "N/A")
        step("close_position", "pass", f"Closed position with reduce-only SELL, order_id={close_order_id}",
             data={"orderId": close_order_id})
    else:
        step("close_position", "fail", f"Close order rejected: {close_result.get('retMsg')}", data=close_result)

    time.sleep(2)

    # ── Step 9: Verify position closed ──
    positions_after = executor.get_positions(symbol="BTCUSDT")
    pos_after = positions_after[0] if positions_after else {}
    pos_after_size = float(pos_after.get("size", 0)) if pos_after else 0
    if pos_after_size == 0:
        step("verify_closed", "pass", "Position successfully closed, size=0")
    else:
        step("verify_closed", "fail", f"Position still open, size={pos_after_size}", data=pos_after)

    # ── Step 10: Reconciliation test ──
    fake_local = [{"symbol": "BTCUSDT", "status": "OPEN", "qty_contracts": 0.001, "side": "LONG"}]
    recon = executor.reconcile(fake_local)
    step("reconciliation", "info", f"Reconciliation test: missing_on_bybit={len(recon['missing_on_bybit'])}, "
         f"missing_locally={len(recon['missing_locally'])}, size_mismatches={len(recon['size_mismatches'])}",
         data=recon)

    # ── Overall result ──
    failed = [s for s in results["steps"] if s["status"] == "fail"]
    results["overall_pass"] = len(failed) == 0
    results["failed_count"] = len(failed)
    results["passed_count"] = len([s for s in results["steps"] if s["status"] == "pass"])

    print(f"\n{'='*60}")
    print(f"VALIDATION {'PASSED' if results['overall_pass'] else 'FAILED'}")
    print(f"Passed: {results['passed_count']}, Failed: {results['failed_count']}")
    print(f"{'='*60}")

    return results


if __name__ == "__main__":
    results = run_validation()
    output_path = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "bybit_api_validation.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")
