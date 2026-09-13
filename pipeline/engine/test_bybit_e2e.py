"""
End-to-End Bybit Demo Trade Simulation
=======================================
Mirrors the exact orchestrator code path: wallet fetch, qty calc, cap_position_size,
qtyStep rounding, min notional check, place_order with SL/TP, position verification,
close via reduce-only, verify closed.

Runs inside the pipeline container:
    docker exec vlthr-pipeline python3 /app/engine/test_bybit_e2e.py
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))

from bybit_executor import create_executor_from_env
from portfolio_config import QTY_STEP, MIN_NOTIONAL, MAX_LEVERAGE, SYMBOLS

def cap_position_size(position_size_1x, account_balance):
    max_notional = account_balance * MAX_LEVERAGE
    return min(position_size_1x, max_notional)

def run():
    executor = create_executor_from_env()
    if not executor:
        print("FAIL: No executor created. Check EXECUTION_MODE and API keys.")
        return

    print(f"Executor active (demo={executor._demo}, base={executor._base_url})")
    print("=" * 80)

    # Fetch wallet balance (same path as orchestrator)
    wallet = executor.get_wallet_balance()
    balance = 0
    if wallet and wallet.get("list"):
        for coin in wallet["list"][0].get("coin", []):
            if coin.get("coin") == "USDT":
                balance = float(coin.get("walletBalance", 0))
                break
    if balance == 0:
        print("FAIL: Could not fetch USDT balance from Bybit")
        return
    print(f"Wallet balance: ${balance:,.2f}")
    print()

    # Test scenarios: one per symbol, alternating long/short
    scenarios = [
        {"symbol": "BTCUSDT",  "side": "LONG",  "risk_pct": 1.0, "sl_pct": 1.5, "tp_pct": 3.0},
        {"symbol": "ETHUSDT",  "side": "SHORT", "risk_pct": 1.0, "sl_pct": 1.5, "tp_pct": 3.0},
        {"symbol": "SOLUSDT",  "side": "LONG",  "risk_pct": 1.0, "sl_pct": 2.0, "tp_pct": 4.0},
        {"symbol": "XRPUSDT",  "side": "SHORT", "risk_pct": 1.0, "sl_pct": 2.0, "tp_pct": 4.0},
        {"symbol": "BNBUSDT",  "side": "LONG",  "risk_pct": 1.0, "sl_pct": 1.5, "tp_pct": 3.0},
        {"symbol": "DOGEUSDT", "side": "SHORT", "risk_pct": 1.0, "sl_pct": 2.0, "tp_pct": 4.0},
    ]

    results = []
    for s in scenarios:
        sym = s["symbol"]
        print(f"\n{'─' * 70}")
        print(f"TEST: {sym} {s['side']} risk={s['risk_pct']}%")

        # 1. Get current ticker price
        ticker = executor.get_tickers(sym)
        if not ticker:
            print(f"  FAIL: Could not fetch ticker for {sym}")
            results.append({"symbol": sym, "step": "ticker", "status": "FAIL", "error": "no ticker"})
            continue
        close_price = float(ticker.get("lastPrice", 0))
        if close_price == 0:
            print(f"  FAIL: Ticker price is 0 for {sym}")
            results.append({"symbol": sym, "step": "ticker", "status": "FAIL", "error": "price=0"})
            continue
        print(f"  Price: ${close_price:,.4f}")

        # 2. Calculate SL/TP (same logic as pipeline: percentage-based)
        sl_dist = close_price * (s["sl_pct"] / 100)
        if s["side"] == "LONG":
            sl_price = close_price - sl_dist
            tp_price = close_price + (close_price * (s["tp_pct"] / 100))
            bybit_side = "Buy"
        else:
            sl_price = close_price + sl_dist
            tp_price = close_price - (close_price * (s["tp_pct"] / 100))
            bybit_side = "Sell"

        # 3. Calculate qty (EXACT same path as orchestrator)
        bybit_dollar_risk = balance * (s["risk_pct"] / 100)
        bybit_qty = bybit_dollar_risk / sl_dist if sl_dist > 0 else 0

        # cap_position_size
        position_1x = close_price * bybit_qty
        position_1x = cap_position_size(position_1x, balance)
        bybit_qty = position_1x / close_price if close_price > 0 else 0

        # Round to qtyStep
        step = QTY_STEP.get(sym, 0.001)
        bybit_qty = (int(bybit_qty / step) * step) if step > 0 else bybit_qty
        bybit_dollar_risk = sl_dist * bybit_qty

        notional = close_price * bybit_qty
        print(f"  Qty calc: raw_dollar_risk=${bybit_dollar_risk:.2f}, qty={bybit_qty:.4f}, notional=${notional:.2f}")
        print(f"  qtyStep={step}, rounded_qty={round(bybit_qty, 4)}")
        print(f"  SL={sl_price:.4f}, TP={tp_price:.4f}")

        # 4. Min notional check
        if notional < MIN_NOTIONAL:
            print(f"  SKIP: notional ${notional:.2f} < min ${MIN_NOTIONAL}")
            results.append({"symbol": sym, "step": "notional", "status": "SKIP", "notional": notional})
            continue

        # 5. Place order (same call as orchestrator)
        print(f"  PLACING ORDER: {sym} {bybit_side} qty={round(bybit_qty, 4)} Market SL={round(sl_price,4)} TP={round(tp_price,4)}")
        order_result = executor.place_order(
            symbol=sym,
            side=bybit_side,
            qty=round(bybit_qty, 4),
            order_type="Market",
            take_profit=round(tp_price, 4),
            stop_loss=round(sl_price, 4),
        )
        ret_code = order_result.get("retCode")
        ret_msg = order_result.get("retMsg", "")
        if ret_code != 0:
            print(f"  FAIL: retCode={ret_code}, retMsg={ret_msg}")
            results.append({"symbol": sym, "step": "place_order", "status": "FAIL",
                            "retCode": ret_code, "retMsg": ret_msg})
            continue

        order_id = order_result.get("result", {}).get("orderId", "")
        print(f"  ORDER ACCEPTED: orderId={order_id}")

        # 6. Verify position exists
        time.sleep(2)
        positions = executor.get_positions(symbol=sym)
        pos_found = False
        pos_size = 0
        pos_side = ""
        pos_entry = 0
        for p in positions:
            if p.get("symbol") == sym and float(p.get("size", 0)) > 0:
                pos_found = True
                pos_size = float(p["size"])
                pos_side = p.get("side", "")
                pos_entry = float(p.get("avgPrice", 0))
                break
        if not pos_found:
            print(f"  FAIL: Position not found after order fill")
            results.append({"symbol": sym, "step": "verify_position", "status": "FAIL",
                            "error": "position not found"})
            continue
        print(f"  POSITION VERIFIED: side={pos_side}, size={pos_size}, entry={pos_entry:.4f}")

        # 7. Verify SL/TP orders are attached
        open_orders = executor.get_open_orders(symbol=sym)
        sl_orders = [o for o in open_orders if o.get("stopOrderType") == "StopLoss"]
        tp_orders = [o for o in open_orders if o.get("stopOrderType") == "TakeProfit"]
        print(f"  SL/TP check: open_orders={len(open_orders)}, sl_found={len(sl_orders)>0}, tp_found={len(tp_orders)>0}")
        for o in open_orders:
            print(f"    order: stopOrderType={o.get('stopOrderType','')}, "
                  f"triggerPrice={o.get('triggerPrice','')}, status={o.get('orderStatus','')}, "
                  f"reduceOnly={o.get('reduceOnly','')}, side={o.get('side','')}")

        # 8. Close position with reduce-only market order
        close_side = "Sell" if bybit_side == "Buy" else "Buy"
        print(f"  CLOSING: {sym} {close_side} qty={round(pos_size, 4)} reduce-only")
        close_result = executor.place_order(
            symbol=sym,
            side=close_side,
            qty=round(pos_size, 4),
            order_type="Market",
            reduce_only=True,
        )
        close_ret = close_result.get("retCode")
        close_msg = close_result.get("retMsg", "")
        if close_ret != 0:
            print(f"  FAIL: Close failed retCode={close_ret}, retMsg={close_msg}")
            results.append({"symbol": sym, "step": "close", "status": "FAIL",
                            "retCode": close_ret, "retMsg": close_msg})
            continue
        close_order_id = close_result.get("result", {}).get("orderId", "")
        print(f"  CLOSE ACCEPTED: orderId={close_order_id}")

        # 9. Verify position is gone
        time.sleep(2)
        positions_after = executor.get_positions(symbol=sym)
        still_open = False
        for p in positions_after:
            if p.get("symbol") == sym and float(p.get("size", 0)) > 0:
                still_open = True
                break
        if still_open:
            print(f"  FAIL: Position still open after close")
            results.append({"symbol": sym, "step": "verify_closed", "status": "FAIL",
                            "error": "position still open"})
        else:
            print(f"  POSITION CLOSED: verified size=0")
            results.append({"symbol": sym, "step": "complete", "status": "PASS",
                            "orderId": order_id, "closeOrderId": close_order_id,
                            "qty": round(bybit_qty, 4), "notional": round(notional, 2),
                            "entry": pos_entry, "sl": round(sl_price, 4), "tp": round(tp_price, 4)})

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    print(f"Total: {len(results)}  PASS: {passed}  FAIL: {failed}  SKIP: {skipped}")
    print()
    for r in results:
        status_icon = "OK" if r["status"] == "PASS" else "SKIP" if r["status"] == "SKIP" else "FAIL"
        detail = ""
        if r["status"] == "FAIL":
            detail = f" step={r['step']} err={r.get('error','')} retCode={r.get('retCode','')} retMsg={r.get('retMsg','')}"
        elif r["status"] == "PASS":
            detail = f" qty={r.get('qty')} notional=${r.get('notional')} entry={r.get('entry')}"
        print(f"  [{status_icon}] {r['symbol']}{detail}")

    # Save results
    out_path = "/app/logs/bybit_e2e_results.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results,
                   "summary": {"pass": passed, "fail": failed, "skip": skipped}}, f, indent=2)
    print(f"\nResults saved to {out_path}")

    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run()
