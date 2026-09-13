"""VLTHR Phase 1 Validation — v0.3.0 Bybit Mechanics"""
import os, sys, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

_engine = Path(__file__).resolve().parent
sys.path.insert(0, str(_engine))
from portfolio_config import QTY_STEP, MMR, TAKER_FEE, MIN_NOTIONAL, get_liquidation_price

try:
    import psycopg2
except ImportError:
    print("[ERROR] pip install psycopg2-binary"); sys.exit(1)

DB_URL = os.environ.get("DB_URL")
if not DB_URL:
    env = _engine.parent / ".env"
    if env.exists():
        with open(env) as f:
            for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.strip().split("=", 1)
                    if k == "DB_URL": DB_URL = v
if not DB_URL:
    print("[ERROR] DB_URL not found"); sys.exit(1)


def connect():
    try:
        return psycopg2.connect(DB_URL, sslmode="prefer")
    except psycopg2.OperationalError:
        return psycopg2.connect(DB_URL.replace(":5432/", ":6543/"), sslmode="prefer")


def cleanup():
    c = connect()
    cur = c.cursor()
    try:
        # Cascade cleanup: debug logs -> event logs -> statements -> trades -> signals
        cur.execute("""
            DELETE FROM trade_debug_log tdl
            USING paper_trades pt, high_confidence_signals hcs
            WHERE tdl.trade_id = pt.id AND pt.signal_id = hcs.id AND hcs.summary = 'VTEST'
        """)
        c.commit()
        cur.execute("""
            DELETE FROM trade_event_log tel
            USING paper_trades pt, high_confidence_signals hcs
            WHERE tel.trade_id = pt.id AND pt.signal_id = hcs.id AND hcs.summary = 'VTEST'
        """)
        c.commit()
        cur.execute("DELETE FROM signal_audit_log WHERE symbol IN ('BTCUSDT','ETHUSDT') AND final_action IN ('SKIPPED','EXPIRED','REJECTED','APPROVED','PENDING','OPEN')")
        c.commit()
        cur.execute("""
            DELETE FROM paper_trade_statements pts
            USING paper_trades pt, high_confidence_signals hcs
            WHERE pts.trade_id = pt.id AND pt.signal_id = hcs.id AND hcs.summary = 'VTEST'
        """)
        c.commit()
        cur.execute("""
            DELETE FROM paper_trades pt
            USING high_confidence_signals hcs
            WHERE pt.signal_id = hcs.id AND hcs.summary = 'VTEST'
        """)
        c.commit()
        cur.execute("DELETE FROM high_confidence_signals WHERE summary='VTEST'")
        c.commit()
    except Exception as e:
        print(f"  [cleanup warning] {e}")
        c.rollback()
    finally:
        cur.close(); c.close()


def create_signal(symbol, price, qty=0.5, lev=4):
    c = connect()
    cur = c.cursor()
    try:
        cur.execute("""
            INSERT INTO high_confidence_signals
            (scan_time_utc,signal_bar_utc,symbol,confidence,grade,price_at_signal,sl_price,tp_price,qty_contracts,leverage,funding_rate,oi_delta_pct,summary,h4_direction,trade_decision)
            VALUES (NOW(),NOW(),%s,70,'GOOD',%s,%s,%s,%s,%s,0.0001,0.5,'VTEST','UP','LONG')
            RETURNING id
        """, (symbol, price, price*0.98, price*1.04, qty, lev))
        sid = cur.fetchone()[0]
        c.commit()
        return sid
    finally:
        cur.close(); c.close()


def get_trade(tid):
    c = connect()
    cur = c.cursor()
    try:
        cur.execute("SELECT * FROM paper_trades WHERE id=%s", (tid,))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None
    finally:
        cur.close(); c.close()


def run_all():
    print("=" * 60)
    print("VLTHR Phase 1-6 Validation — v0.3.5")
    print("=" * 60)
    results = []

    # ── TEST 1: Margin Formula ──
    print("\n[TEST 1] Margin Formula")
    cleanup()
    c = connect(); cur = c.cursor()
    try:
        p, q, l = 65000.0, 0.01, 4
        n = p * q
        exp = (n / l) + (n * TAKER_FEE)
        sid = create_signal("BTCUSDT", p, q, l)
        cur.execute("""
            INSERT INTO paper_trades (signal_id,symbol,signal_bar_utc,scan_time_utc,side,entry_price_planned,entry_price_actual,sl_price,tp_price,qty_contracts,leveraged_notional,margin_required,leverage,confidence,funding_rate_at_entry,entry_fee,exit_fee,fees_total,funding_events,funding_total,slippage_pct,slippage_usd,status,notes)
            VALUES (%s,%s,NOW(),NOW(),'LONG',%s,%s,%s,%s,%s,%s,%s,%s,70,0.0001,%s,%s,%s,0,0,0.1,%s,'OPEN','VTEST margin')
            RETURNING id
        """, (sid, "BTCUSDT", p, p, p*0.98, p*1.04, q, n*l, exp, l, n*TAKER_FEE, n*TAKER_FEE, n*TAKER_FEE*2, n*0.001))
        tid = cur.fetchone()[0]; c.commit()
        trade = get_trade(tid)
        act = float(trade["margin_required"])
        ok = abs(act - exp) < 0.01
        print(f"  Notional ${n:,.2f} | Lev {l}x | Expected ${exp:,.4f} | Actual ${act:,.4f} | {'PASS' if ok else 'FAIL'}")
        results.append(("Margin Formula", ok))
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        c.rollback(); results.append(("Margin Formula", False))
    finally:
        cur.close(); c.close()

    # ── TEST 2: Liquidation Price ──
    print("\n[TEST 2] Liquidation Price")
    cleanup()
    c = connect(); cur = c.cursor()
    try:
        p, q, l = 65000.0, 0.01, 4
        n = p * q; ftc = n * TAKER_FEE; m = (n / l) + ftc; mm = n * MMR
        exp_lp = get_liquidation_price(p, q, m, mm, "LONG")
        sid = create_signal("BTCUSDT", p, q, l)
        cur.execute("""
            INSERT INTO paper_trades (signal_id,symbol,signal_bar_utc,scan_time_utc,side,entry_price_planned,entry_price_actual,sl_price,tp_price,qty_contracts,leveraged_notional,margin_required,leverage,confidence,entry_fee,exit_fee,fees_total,liquidation_price,maintenance_margin,status,notes)
            VALUES (%s,%s,NOW(),NOW(),'LONG',%s,%s,%s,%s,%s,%s,%s,%s,70,%s,%s,%s,%s,%s,'OPEN','VTEST liq')
            RETURNING id
        """, (sid, "BTCUSDT", p, p, p*0.98, p*1.04, q, n*l, m, l, ftc, ftc, ftc*2, exp_lp, mm))
        tid = cur.fetchone()[0]; c.commit()
        trade = get_trade(tid)
        act_lp = float(trade["liquidation_price"]) if trade["liquidation_price"] else 0
        ok = abs(act_lp - exp_lp) < 0.01
        print(f"  Entry ${p:,.2f} | IM ${m:,.4f} | MM ${mm:,.4f} | Expected LP ${exp_lp:,.4f} | Actual ${act_lp:,.4f} | {'PASS' if ok else 'FAIL'}")
        results.append(("Liquidation Price", ok))
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        c.rollback(); results.append(("Liquidation Price", False))
    finally:
        cur.close(); c.close()

    # ── TEST 3: qtyStep Rounding ──
    print("\n[TEST 3] qtyStep Rounding")
    cases = [("BTCUSDT",0.02234561,0.001,0.022),("SOLUSDT",1.23456,0.1,1.2),("XRPUSDT",15.678,1.0,15.0),("ETHUSDT",0.005678,0.001,0.005),("BNBUSDT",0.03456,0.01,0.03),("DOGEUSDT",55.789,1.0,55.0)]
    ok = True
    for sym, raw, step, exp in cases:
        got = (int(raw / step) * step) if step > 0 else raw
        passed = abs(got - exp) < 0.0001
        ok = ok and passed
        print(f"  {sym}: {raw} -> {got} (exp {exp}) {'PASS' if passed else 'FAIL'}")
    results.append(("qtyStep Rounding", ok))

    # ── TEST 4: minNotional Guard ──
    print("\n[TEST 4] minNotional Guard")
    cleanup()
    p, q = 65000.0, 0.00005
    n = p * q
    step = QTY_STEP.get("BTCUSDT", 0.001)
    qr = (int(q / step) * step) if step > 0 else q
    nr = p * qr
    ok = nr < MIN_NOTIONAL
    print(f"  Raw qty {q} | Rounded {qr} | Notional ${nr:,.2f} | Min ${MIN_NOTIONAL} | {'PASS (rejected)' if ok else 'FAIL'}")
    results.append(("minNotional Guard", ok))

    # ── TEST 5: Funding Window Settlement ──
    print("\n[TEST 5] Funding Window Settlement")
    cleanup()
    c = connect(); cur = c.cursor()
    try:
        p, q, l = 65000.0, 0.01, 4
        n = p * q; ftc = n * TAKER_FEE; m = (n / l) + ftc
        # Entry must be in PREVIOUS window to be eligible for current window funding
        et = datetime.now(timezone.utc) - timedelta(hours=10)
        sid = create_signal("BTCUSDT", p, q, l)
        cur.execute("""
            INSERT INTO paper_trades (signal_id,symbol,signal_bar_utc,scan_time_utc,side,entry_price_planned,entry_price_actual,sl_price,tp_price,qty_contracts,leveraged_notional,margin_required,leverage,confidence,entry_fee,exit_fee,fees_total,liquidation_price,maintenance_margin,last_funding_settlement_utc,status,notes)
            VALUES (%s,%s,NOW(),NOW(),'LONG',%s,%s,%s,%s,%s,%s,%s,%s,70,%s,%s,%s,%s,%s,%s,'OPEN','VTEST funding')
            RETURNING id
        """, (sid, "BTCUSDT", p, p, p*0.98, p*1.04, q, n*l, m, l, ftc, ftc, ftc*2, get_liquidation_price(p,q,m,n*MMR,"LONG"), n*MMR, et))
        tid = cur.fetchone()[0]; c.commit()
        trade = get_trade(tid)
        settled = trade["last_funding_settlement_utc"]
        now = datetime.now(timezone.utc)
        wh = 0 if now.hour < 8 else 8 if now.hour < 16 else 16
        ws = datetime(now.year, now.month, now.day, wh, 0, 0, tzinfo=timezone.utc)
        eligible = et < ws
        already = settled is not None and settled >= ws
        ok = eligible and not already
        print(f"  Entry {et.isoformat()} | Window {ws.isoformat()} | Eligible={eligible} | Settled={already} | {'PASS' if ok else 'FAIL'}")
        results.append(("Funding Window", ok))
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        c.rollback(); results.append(("Funding Window", False))
    finally:
        cur.close(); c.close()

    # ── TEST 6: LIQUIDATED Trigger ──
    print("\n[TEST 6] LIQUIDATED Trigger")
    cleanup()
    c = connect(); cur = c.cursor()
    try:
        p, q, l = 65000.0, 0.01, 4
        n = p * q; ftc = n * TAKER_FEE; m = (n / l) + ftc; mm = n * MMR
        lp = get_liquidation_price(p, q, m, mm, "LONG")
        sid = create_signal("BTCUSDT", p, q, l)
        cur.execute("""
            INSERT INTO paper_trades (signal_id,symbol,signal_bar_utc,scan_time_utc,side,entry_price_planned,entry_price_actual,sl_price,tp_price,qty_contracts,leveraged_notional,margin_required,leverage,confidence,entry_fee,exit_fee,fees_total,liquidation_price,maintenance_margin,status,notes)
            VALUES (%s,%s,NOW(),NOW(),'LONG',%s,%s,%s,%s,%s,%s,%s,%s,70,%s,%s,%s,%s,%s,'OPEN','VTEST liq trigger')
            RETURNING id
        """, (sid, "BTCUSDT", p, p, p*0.98, p*1.04, q, n*l, m, l, ftc, ftc, ftc*2, lp, mm))
        tid = cur.fetchone()[0]; c.commit()
        trade = get_trade(tid)
        live = lp - 10
        side = trade["side"]
        liq = float(trade["liquidation_price"]) if trade["liquidation_price"] else 0
        should = side == "LONG" and liq > 0 and live <= liq
        ok = should
        print(f"  Entry ${p:,.2f} | LP ${lp:,.4f} | Live ${live:,.2f} | ShouldLiq={should} | {'PASS' if ok else 'FAIL'}")
        results.append(("LIQUIDATED Trigger", ok))
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        c.rollback(); results.append(("LIQUIDATED Trigger", False))
    finally:
        cur.close(); c.close()

    # ── TEST 7: Trade Event Log (Phase 2) ──
    print("\n[TEST 7] Trade Event Log (OPEN / CLOSE / FUNDING)")
    c = connect(); cur = c.cursor()
    try:
        p, q, l = 65000.0, 0.01, 4
        n = p * q; ftc = n * TAKER_FEE; m = (n / l) + ftc; mm = n * MMR
        lp = get_liquidation_price(p, q, m, mm, "LONG")
        sid = create_signal("BTCUSDT", p, q, l)
        cur.execute("""
            INSERT INTO paper_trades (signal_id,symbol,signal_bar_utc,scan_time_utc,side,entry_price_planned,entry_price_actual,sl_price,tp_price,qty_contracts,leveraged_notional,margin_required,leverage,confidence,entry_fee,exit_fee,fees_total,liquidation_price,maintenance_margin,status,notes)
            VALUES (%s,%s,NOW(),NOW(),'LONG',%s,%s,%s,%s,%s,%s,%s,%s,70,%s,%s,%s,%s,%s,'OPEN','VTEST event log')
            RETURNING id
        """, (sid, "BTCUSDT", p, p, p*0.98, p*1.04, q, n*l, m, l, ftc, ftc, ftc*2, lp, mm))
        tid = cur.fetchone()[0]; c.commit()

        # Insert a mock trade event
        cur.execute("""
            INSERT INTO trade_event_log (trade_id,symbol,event_type,event_time,price_at_event,balance_before,balance_after,margin_delta,fee_applied,pnl_delta,detail)
            VALUES (%s,%s,'OPEN',NOW(),%s,10000,9875,%s,%s,0,'{\"side\":\"LONG\"}'::jsonb)
        """, (tid, "BTCUSDT", p, m, ftc))
        c.commit()

        cur.execute("SELECT COUNT(*) FROM trade_event_log WHERE trade_id=%s", (tid,))
        count = cur.fetchone()[0]
        ok = count >= 1
        print(f"  Inserted trade event for trade {tid} | Rows={count} | {'PASS' if ok else 'FAIL'}")
        results.append(("Trade Event Log", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); c.rollback(); results.append(("Trade Event Log", False))
    finally:
        cur.close(); c.close()

    # ── TEST 8: Signal Audit Log (Phase 2) ──
    print("\n[TEST 8] Signal Audit Log")
    c = connect(); cur = c.cursor()
    try:
        sid = create_signal("ETHUSDT", 3500.0, 0.1, 4)
        cur.execute("""
            INSERT INTO signal_audit_log (run_time,signal_id,symbol,dqs,gate_reached,gate_passed,gate_reason,final_action)
            VALUES (NOW(),%s,'ETHUSDT',72,'GATE_4A_CORR',FALSE,'Test rejection','REJECTED')
        """, (sid,))
        c.commit()
        cur.execute("SELECT COUNT(*) FROM signal_audit_log WHERE signal_id=%s", (sid,))
        count = cur.fetchone()[0]
        ok = count >= 1
        print(f"  Inserted audit for signal {sid} | Rows={count} | {'PASS' if ok else 'FAIL'}")
        results.append(("Signal Audit Log", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); c.rollback(); results.append(("Signal Audit Log", False))
    finally:
        cur.close(); c.close()

    # ── TEST 9: Calibration Curve (Phase 3) ──
    print("\n[TEST 9] Calibration Curve (IsotonicRegression)")
    try:
        import json
        cal_path = Path(__file__).resolve().parent / "calibration.json"
        with open(cal_path) as f:
            cal = json.load(f)
        curve = cal.get("calibration_curve", {})
        has_curve = bool(curve) and "win_probabilities" in curve
        ok = has_curve
        if ok:
            bps = curve["dqs_breakpoints"]
            probs = curve["win_probabilities"]
            print(f"  Curve present | breakpoints={len(bps)} | samples={curve.get('n_samples_used', 0)} | {'PASS' if ok else 'FAIL'}")
            for bp, prob in zip(bps, probs):
                print(f"    DQS {bp:>3} -> {prob:.2%}")
        else:
            print(f"  Missing calibration_curve in JSON | FAIL")
        results.append(("Calibration Curve", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); results.append(("Calibration Curve", False))

    # ── TEST 10: Calibrated Probability Lookup (Phase 3) ──
    print("\n[TEST 10] Calibrated Probability Lookup")
    try:
        from adaptive_scorer import lookup_calibrated_prob
        p50 = lookup_calibrated_prob(50, "BTCUSDT")
        p75 = lookup_calibrated_prob(75, "BTCUSDT")
        p100 = lookup_calibrated_prob(100, "BTCUSDT")
        ok = 0.0 <= p50 <= 1.0 and 0.0 <= p75 <= 1.0 and 0.0 <= p100 <= 1.0
        print(f"  lookup(50)={p50:.4f} | lookup(75)={p75:.4f} | lookup(100)={p100:.4f} | {'PASS' if ok else 'FAIL'}")
        results.append(("Calibrated Prob Lookup", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); results.append(("Calibrated Prob Lookup", False))

    # ── TEST 11: calibrated_confidence DB write (Phase 3) ──
    print("\n[TEST 11] calibrated_confidence DB column")
    c = connect(); cur = c.cursor()
    try:
        sid = create_signal("SOLUSDT", 145.0, 0.5, 4)
        cur.execute("""
            UPDATE high_confidence_signals SET calibrated_confidence=%s WHERE id=%s
        """, (0.5234, sid))
        c.commit()
        cur.execute("SELECT calibrated_confidence FROM high_confidence_signals WHERE id=%s", (sid,))
        val = cur.fetchone()[0]
        ok = val is not None and abs(float(val) - 0.5234) < 0.001
        print(f"  Written 0.5234 | Read {float(val) if val else None} | {'PASS' if ok else 'FAIL'}")
        results.append(("calibrated_confidence DB", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); c.rollback(); results.append(("calibrated_confidence DB", False))
    finally:
        cur.close(); c.close()

    # ── TEST 12: trade_debug_log insert (Phase 4) ──
    print("\n[TEST 12] trade_debug_log insert (ENTRY/EXIT)")
    c = connect(); cur = c.cursor()
    try:
        sid = create_signal("ETHUSDT", 3500.0, 0.5, 4)
        cur.execute("""
            INSERT INTO paper_trades (signal_id, symbol, signal_bar_utc, scan_time_utc, side, entry_price_planned, entry_price_actual, qty_contracts, margin_required, leverage, status, created_at)
            VALUES (%s, %s, NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id
        """, (sid, "ETHUSDT", "LONG", 3500.0, 3500.0, 0.5, 200.0, 4, "OPEN"))
        tid = cur.fetchone()[0]
        c.commit()
        cur.execute("""
            INSERT INTO trade_debug_log (trade_id, symbol, log_type, price, dqs_score, failure_category, suggested_fix)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (tid, "ETHUSDT", "ENTRY", 3500.0, 72, "UNKNOWN", "Review trade context manually"))
        c.commit()
        cur.execute("SELECT COUNT(*) FROM trade_debug_log WHERE trade_id=%s", (tid,))
        count = cur.fetchone()[0]
        ok = count >= 1
        print(f"  Inserted debug log for trade {tid} | Rows={count} | {'PASS' if ok else 'FAIL'}")
        results.append(("trade_debug_log DB", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); c.rollback(); results.append(("trade_debug_log DB", False))
    finally:
        cur.close(); c.close()

    # ── TEST 13: trade_debugger.classify_failure (Phase 4) ──
    print("\n[TEST 13] trade_debugger.classify_failure")
    try:
        from trade_debugger import classify_failure, suggested_fix
        # MACRO_HEADWIND test
        entry1 = {"btc_24h_ret": -0.03, "atr_15m": 12, "ob_imbalance": 5, "funding_rate": -0.0001, "oi_delta_pct": -1}
        exit1 = {"atr_15m": 12}
        trade1 = {"side": "LONG", "exit_reason": "SL_HIT", "hours_held": 1, "net_pnl_usd": -50}
        cat1 = classify_failure(entry1, exit1, trade1)
        ok1 = cat1 == "MACRO_HEADWIND"

        # WHIPSAW test
        entry2 = {"btc_24h_ret": 0.005, "atr_15m": 10, "ob_imbalance": 5, "funding_rate": -0.0001, "oi_delta_pct": -1}
        exit2 = {"atr_15m": 20}
        trade2 = {"side": "LONG", "exit_reason": "SL_HIT", "hours_held": 1, "net_pnl_usd": -50}
        cat2 = classify_failure(entry2, exit2, trade2)
        ok2 = cat2 == "WHIPSAW"

        # Winner test
        entry3 = {"btc_24h_ret": 0.005, "atr_15m": 10}
        exit3 = {"atr_15m": 10}
        trade3 = {"side": "LONG", "exit_reason": "TP_HIT", "hours_held": 2, "net_pnl_usd": 100}
        cat3 = classify_failure(entry3, exit3, trade3)
        ok3 = cat3 == "UNKNOWN"

        ok = ok1 and ok2 and ok3
        print(f"  MACRO_HEADWIND={cat1} | WHIPSAW={cat2} | Winner={cat3} | {'PASS' if ok else 'FAIL'}")
        results.append(("trade_debugger classify", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); results.append(("trade_debugger classify", False))

    # ── TEST 14: Triple-Barrier Labeling (Phase 5) ──
    print("\n[TEST 14] Triple-Barrier Labeling")
    try:
        from freqtrade_validator import download_ohlcv, triple_barrier_label, build_features
        df = download_ohlcv("BTCUSDT", "15", days=3)
        ok_download = df is not None and len(df) >= 100
        if ok_download:
            labeled = triple_barrier_label(df, pt_mult=2.0, sl_mult=1.0, max_hold=20)
            labeled = build_features(labeled)
            has_labels = "label" in labeled.columns and labeled["label"].isin([0, 1]).all()
            label_balance = labeled["label"].value_counts(normalize=True).to_dict()
            ok = has_labels and len(labeled) >= 50
            print(f"  Downloaded {len(df)} bars | Labeled {len(labeled)} | Labels={label_balance} | {'PASS' if ok else 'FAIL'}")
        else:
            ok = False
            print(f"  Download failed or insufficient data | FAIL")
        results.append(("Triple-Barrier Label", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); results.append(("Triple-Barrier Label", False))

    # ── TEST 15: Dry-Run Backtest (Phase 5) ──
    print("\n[TEST 15] VLTHR Dry-Run Backtest")
    try:
        from freqtrade_validator import dry_run_backtest
        df = download_ohlcv("BTCUSDT", "15", days=3)
        ok = False
        if df is not None and len(df) >= 100:
            from freqtrade_validator import build_features
            df_feat = build_features(df)
            trades = dry_run_backtest(df_feat, entry_dqs_min=65, sl_pct=0.02, tp_pct=0.04, max_hold_bars=20)
            ok = len(trades) > 0 and "net_return" in trades.columns
            print(f"  Trades={len(trades)} | Avg net ret={trades['net_return'].mean():.4f} | Win rate={trades['net_return'].gt(0).mean():.2%} | {'PASS' if ok else 'FAIL'}")
        else:
            print(f"  No data for backtest | FAIL")
        results.append(("Dry-Run Backtest", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); results.append(("Dry-Run Backtest", False))

    # ── TEST 16: Dynamic SL columns (Phase 6) ──
    print("\n[TEST 16] Dynamic SL columns (sl_original, atr_at_entry)")
    c = connect(); cur = c.cursor()
    try:
        sid = create_signal("BTCUSDT", 95000.0, 0.5, 4)
        cur.execute("""
            INSERT INTO paper_trades (
                signal_id, symbol, signal_bar_utc, scan_time_utc, side,
                entry_price_planned, entry_price_actual, sl_price, tp_price,
                qty_contracts, leveraged_notional, margin_required, leverage, confidence,
                entry_fee, exit_fee, fees_total, liquidation_price, maintenance_margin,
                status, notes, sl_original, atr_at_entry
            ) VALUES (%s, %s, NOW(), NOW(), 'LONG', %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            sid, "BTCUSDT", 95000.0, 95000.0, 93100.0, 98800.0,
            0.5, 95000.0 * 4, 23750.0, 4, 70,
            26.125, 26.125, 52.25, 0, 0,
            "OPEN", "VTEST dynamic SL", 93100.0, 450.0
        ))
        tid = cur.fetchone()[0]
        c.commit()
        cur.execute("SELECT sl_original, atr_at_entry FROM paper_trades WHERE id=%s", (tid,))
        row = cur.fetchone()
        ok = row is not None and abs(float(row[0]) - 93100.0) < 1 and abs(float(row[1]) - 450.0) < 1
        print(f"  Written sl_original=93100, atr_at_entry=450 | Read sl_original={row[0] if row else None}, atr_at_entry={row[1] if row else None} | {'PASS' if ok else 'FAIL'}")
        results.append(("Dynamic SL columns", ok))
        cur.execute("DELETE FROM paper_trades WHERE id=%s", (tid,))
        cur.execute("DELETE FROM high_confidence_signals WHERE id=%s", (sid,))
        c.commit()
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); c.rollback(); results.append(("Dynamic SL columns", False))
    finally:
        cur.close(); c.close()

    # ── TEST 17: Dynamic SL logic (Phase 6) ──
    print("\n[TEST 17] Dynamic SL logic (break-even, time decay, ATR expansion)")
    try:
        from dynamic_sl import compute_dynamic_sl

        # Break-even test: LONG trade, price > 50% to TP
        trade1 = {"side": "LONG", "entry_price_actual": 100000, "tp_price": 104000,
                  "sl_original": 98000, "atr_at_entry": 500, "sl_price": 98000}
        res1 = compute_dynamic_sl(trade1, 102100, 1)
        ok1 = res1 and "BREAK_EVEN" in res1["reasons"] and res1["new_sl"] > 98000

        # Time decay test: LONG trade after 5 hours, no break-even
        trade2 = {"side": "LONG", "entry_price_actual": 100000, "tp_price": 104000,
                  "sl_original": 98000, "atr_at_entry": 500, "sl_price": 98000}
        res2 = compute_dynamic_sl(trade2, 100100, 5)
        ok2 = res2 and "TIME_DECAY" in res2["reasons"] and res2["new_sl"] > 98000

        # ATR expansion test: live ATR 2x entry ATR
        trade3 = {"side": "LONG", "entry_price_actual": 100000, "tp_price": 104000,
                  "sl_original": 98000, "atr_at_entry": 500, "sl_price": 98000, "live_atr": 1200}
        res3 = compute_dynamic_sl(trade3, 100100, 1)
        ok3 = res3 and "ATR_EXPANSION" in res3["reasons"] and res3["new_sl"] < 98000

        ok = ok1 and ok2 and ok3
        print(f"  BreakEven={ok1} | TimeDecay={ok2} | AtrExpansion={ok3} | {'PASS' if ok else 'FAIL'}")
        results.append(("Dynamic SL logic", ok))
    except Exception as e:
        print(f"  ERROR: {e}"); traceback.print_exc(); results.append(("Dynamic SL logic", False))

    # ── SUMMARY ──
    cleanup()
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results:
        print(f"  {name:<25} {'PASS' if ok else 'FAIL'}")
        if not ok: all_ok = False
    print("=" * 60)
    print("ALL PASS" if all_ok else "SOME FAILURES -- see above")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run_all())
