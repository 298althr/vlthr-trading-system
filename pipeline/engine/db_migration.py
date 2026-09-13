"""
VLTHR Portfolio DQS — DB Migration
Creates the 5 new tables + error_log required by the portfolio-centric pipeline.
Run once before any pipeline phase.

Usage:
    python engine/db_migration.py
"""
import os
import sys
from pathlib import Path

# Auto-load .env
_engine = Path(__file__).resolve().parent
try:
    _env = _engine.parent.parent.parent.parent / ".env"
except IndexError:
    _env = _engine.parent / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

import psycopg2

DB_URL = os.environ.get("DB_URL")

NEW_TABLES_SQL = """
-- 1. Signal live state — 1 row per symbol, updated each 15m run
CREATE TABLE IF NOT EXISTS signal_state (
    id SERIAL PRIMARY KEY,
    signal_id INT REFERENCES high_confidence_signals(id),
    symbol VARCHAR(16) NOT NULL,
    CONSTRAINT uq_signal_state_symbol_bar UNIQUE (symbol, signal_bar_utc),
    signal_bar_utc TIMESTAMP NOT NULL,
    first_seen_utc TIMESTAMP NOT NULL,
    last_updated_utc TIMESTAMP NOT NULL,

    current_dqs INT NOT NULL,
    current_ranker_score INT,
    current_entry NUMERIC(18,8),
    current_sl NUMERIC(18,8),
    current_tp NUMERIC(18,8),
    risk_pct NUMERIC(5,2),

    prev_dqs INT,
    prev_ranker_score INT,
    dqs_delta INT,

    consecutive_improvements SMALLINT DEFAULT 0,
    consecutive_declines SMALLINT DEFAULT 0,
    runs_tracked SMALLINT DEFAULT 0,
    trajectory_bonus INT DEFAULT 0,

    adjusted_score INT,

    status VARCHAR(20) DEFAULT 'ACTIVE',
    expiry_reason VARCHAR(128),

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 2. Signal run log — append-only history
CREATE TABLE IF NOT EXISTS signal_state_log (
    id SERIAL PRIMARY KEY,
    signal_id INT REFERENCES high_confidence_signals(id),
    symbol VARCHAR(16) NOT NULL,
    run_time TIMESTAMP NOT NULL,
    run_number INT NOT NULL,

    dqs INT,
    ranker_score INT,
    entry NUMERIC(18,8),
    sl NUMERIC(18,8),
    tp NUMERIC(18,8),
    risk_pct NUMERIC(5,2),
    status VARCHAR(20),
    trajectory VARCHAR(32),
    delta INT,

    created_at TIMESTAMP DEFAULT NOW()
);

-- 3. Risk ledger — tracks open risk per symbol and total
CREATE TABLE IF NOT EXISTS risk_ledger (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(16) NOT NULL,
    trade_id INT REFERENCES paper_trades(id),
    entry_price NUMERIC(18,8),
    sl_price NUMERIC(18,8),
    qty_contracts NUMERIC(18,4),
    dollar_risk NUMERIC(12,2),
    risk_pct_of_account NUMERIC(5,2),
    is_open BOOLEAN DEFAULT TRUE,
    opened_at TIMESTAMP DEFAULT NOW(),
    closed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. Trade count log — session and daily counters
CREATE TABLE IF NOT EXISTS trade_count_log (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    session VARCHAR(16) NOT NULL,
    symbol VARCHAR(16),
    trade_id INT REFERENCES paper_trades(id),
    action VARCHAR(16),  -- 'OPENED', 'CLOSED'
    count_type VARCHAR(16), -- 'SESSION', 'DAILY'
    running_total INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(date, session, count_type)
);

-- 5. Portfolio snapshot — taken every 15m run
CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    id SERIAL PRIMARY KEY,
    run_time TIMESTAMP NOT NULL,
    account_balance NUMERIC(12,2),
    account_equity NUMERIC(12,2),
    margin_used NUMERIC(12,2),
    total_open_risk NUMERIC(12,2),
    total_open_risk_pct NUMERIC(5,2),
    active_trades_count INT,
    daily_trades_count INT,
    london_trades_count INT,
    ny_late_trades_count INT,
    highest_dqs_active INT,
    lowest_dqs_active INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 6. Error log — for circuit breaker and safety layer
CREATE TABLE IF NOT EXISTS error_log (
    id SERIAL PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    error_type VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    stack_trace TEXT,
    symbol VARCHAR(16),
    run_time TIMESTAMP,
    is_critical BOOLEAN DEFAULT FALSE,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 7. Pipeline heartbeat — one row per pipeline iteration (Phase 1)
CREATE TABLE IF NOT EXISTS pipeline_heartbeat (
    id            BIGSERIAL PRIMARY KEY,
    run_time      TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL,              -- OK | ABORTED | ERROR
    abort_reason  TEXT,                       -- e.g. pre_flight_failed, stale_data
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    signals_scanned   INTEGER NOT NULL DEFAULT 0,
    signals_approved  INTEGER NOT NULL DEFAULT 0,
    node_summary  JSONB,                      -- compact per-node pass/fail rollup
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_heartbeat_run_time ON pipeline_heartbeat (run_time DESC);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_signal_state_symbol ON signal_state(symbol);
CREATE INDEX IF NOT EXISTS idx_signal_state_status ON signal_state(status);
CREATE INDEX IF NOT EXISTS idx_signal_state_symbol_bar ON signal_state(symbol, signal_bar_utc);
CREATE INDEX IF NOT EXISTS idx_signal_log_symbol_time ON signal_state_log(symbol, run_time);
CREATE INDEX IF NOT EXISTS idx_risk_ledger_symbol ON risk_ledger(symbol);
CREATE INDEX IF NOT EXISTS idx_risk_ledger_open ON risk_ledger(is_open);
CREATE INDEX IF NOT EXISTS idx_trade_count_date_session ON trade_count_log(date, session);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshot_time ON portfolio_snapshot(run_time);
CREATE INDEX IF NOT EXISTS idx_error_log_source ON error_log(source);
CREATE INDEX IF NOT EXISTS idx_error_log_created ON error_log(created_at);
CREATE INDEX IF NOT EXISTS idx_heartbeat_status ON pipeline_heartbeat(status);

-- 8. Predictions — one row per symbol per run, resolved on next bar (Phase 3)
CREATE TABLE IF NOT EXISTS predictions (
    id            BIGSERIAL PRIMARY KEY,
    run_time      TIMESTAMPTZ NOT NULL,
    symbol        VARCHAR(16) NOT NULL,
    features      JSONB,                       -- snapshot of features used
    prob_favorable NUMERIC(5,4),                -- P(favorable next bar)
    predicted_outcome INT,                     -- 1 = favorable, 0 = unfavorable
    actual_outcome  INT,                       -- filled on next bar close
    hit           BOOLEAN,                     -- predicted == actual
    dqs_at_predict INT NOT NULL DEFAULT 0,
    model_version TEXT DEFAULT 'v1',
    shadow_mode   BOOLEAN DEFAULT TRUE,          -- TRUE until trust criteria met
    close_price_at_prediction DOUBLE PRECISION,  -- price at prediction time for resolution
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_predictions_run_time ON predictions (run_time DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol ON predictions (symbol);
CREATE INDEX IF NOT EXISTS idx_predictions_resolved ON predictions (actual_outcome) WHERE actual_outcome IS NOT NULL;

-- 9. Model state — serialized River model checkpoint (Phase 3)
CREATE TABLE IF NOT EXISTS model_state (
    id            SERIAL PRIMARY KEY,
    model_type    TEXT NOT NULL,                 -- 'river_hoeffding_tree' | 'river_logistic'
    symbol        VARCHAR(16),                   -- NULL = global model
    serialized    BYTEA NOT NULL,                 -- pickle/bytes of model object
    n_samples     INT NOT NULL DEFAULT 0,       -- training samples seen
    accuracy      NUMERIC(5,4),                   -- rolling accuracy
    brier_score   NUMERIC(5,4),                   -- calibration metric
    shadow_pnl    NUMERIC(12,2) DEFAULT 0,      -- cumulative shadow P&L
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_model_state_type_symbol ON model_state (model_type, symbol);

-- 10. Pipeline trace — one row per node per run (Phase 4)
CREATE TABLE IF NOT EXISTS pipeline_trace (
    id            BIGSERIAL PRIMARY KEY,
    run_time      TIMESTAMPTZ NOT NULL,
    node          TEXT NOT NULL,                 -- scan | score | predict | state | rank | gate | upsert | snapshot | invariants
    status        TEXT NOT NULL,                 -- OK | FAIL | SKIP | WARN
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    in_count      INTEGER DEFAULT 0,            -- items entering node
    out_count     INTEGER DEFAULT 0,             -- items leaving node
    detail        JSONB,                        -- arbitrary diagnostic payload
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trace_run_time ON pipeline_trace (run_time DESC);
CREATE INDEX IF NOT EXISTS idx_trace_node ON pipeline_trace (node);
CREATE INDEX IF NOT EXISTS idx_trace_status ON pipeline_trace (status);
"""


def migrate():
    if not DB_URL:
        print("[Migration] ERROR: DB_URL not found in .env")
        sys.exit(1)

    try:
        conn = psycopg2.connect(DB_URL, sslmode="prefer")
    except psycopg2.OperationalError:
        alt = DB_URL.replace(":5432/", ":6543/")
        conn = psycopg2.connect(alt, sslmode="prefer")

    cur = conn.cursor()
    cur.execute(NEW_TABLES_SQL)
    conn.commit()

    # Migration: update signal_state unique constraint for existing tables
    try:
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'signal_state' AND constraint_name = 'signal_state_symbol_key'
                ) THEN
                    ALTER TABLE signal_state DROP CONSTRAINT signal_state_symbol_key;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'signal_state' AND constraint_name = 'uq_signal_state_symbol_bar'
                ) THEN
                    ALTER TABLE signal_state ADD CONSTRAINT uq_signal_state_symbol_bar UNIQUE (symbol, signal_bar_utc);
                END IF;
            END $$;
        """)
        conn.commit()
        print("[Migration] signal_state unique constraint updated to (symbol, signal_bar_utc)")
    except Exception as e:
        print(f"[Migration] signal_state constraint update skipped: {e}")
        conn.rollback()

    # Migration: add side, strategy, session columns to signal_state for calibration gate
    signal_state_cols = [
        ("ALTER TABLE signal_state ADD COLUMN IF NOT EXISTS side VARCHAR(8)", "signal_state.side"),
        ("ALTER TABLE signal_state ADD COLUMN IF NOT EXISTS strategy VARCHAR(32)", "signal_state.strategy"),
        ("ALTER TABLE signal_state ADD COLUMN IF NOT EXISTS session VARCHAR(16)", "signal_state.session"),
    ]
    for sql, name in signal_state_cols:
        try:
            cur.execute(sql)
            conn.commit()
            print(f"[Migration] {name} column verified")
        except Exception as e:
            print(f"[Migration] {name} skipped: {e}")
            conn.rollback()

    # Migration: add close_price_at_prediction to predictions if not exists
    try:
        cur.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS close_price_at_prediction DOUBLE PRECISION")
        conn.commit()
        print("[Migration] predictions.close_price_at_prediction column verified")
    except Exception as e:
        print(f"[Migration] predictions column migration skipped: {e}")
        conn.rollback()

    # Migration: add brain output columns to high_confidence_signals
    brain_cols = [
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS brain_confidence_fused NUMERIC(6,4)", "hcs.brain_confidence_fused"),
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS brain_shadow_mode BOOLEAN DEFAULT TRUE", "hcs.brain_shadow_mode"),
    ]
    for sql, name in brain_cols:
        try:
            cur.execute(sql)
            conn.commit()
            print(f"[Migration] {name} column verified")
        except Exception as e:
            print(f"[Migration] {name} skipped: {e}")
            conn.rollback()

    # v0.3.0 — Bybit mechanics alignment columns
    v3_cols = [
        ("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS liquidation_price NUMERIC(18,8)", "paper_trades.liquidation_price"),
        ("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS maintenance_margin NUMERIC(12,4)", "paper_trades.maintenance_margin"),
        ("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS last_funding_settlement_utc TIMESTAMPTZ", "paper_trades.last_funding_settlement_utc"),
        ("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mark_price_at_entry NUMERIC(18,8)", "paper_trades.mark_price_at_entry"),
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS calibrated_confidence NUMERIC(6,4)", "hcs.calibrated_confidence"),
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS information_ratio NUMERIC(8,4)", "hcs.information_ratio"),
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS margin_required NUMERIC(18,8)", "hcs.margin_required"),
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS liquidation_price NUMERIC(18,8)", "hcs.liquidation_price"),
        ("ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS maintenance_margin NUMERIC(12,4)", "hcs.maintenance_margin"),
        ("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sl_original NUMERIC(18,8)", "paper_trades.sl_original"),
        ("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS atr_at_entry NUMERIC(18,8)", "paper_trades.atr_at_entry"),
    ]
    for sql, name in v3_cols:
        try:
            cur.execute(sql)
            conn.commit()
            print(f"[Migration] {name} column verified")
        except Exception as e:
            print(f"[Migration] {name} skipped: {e}")
            conn.rollback()

    # v0.3.0 — Structured logging tables
    log_tables_sql = """
    CREATE TABLE IF NOT EXISTS signal_audit_log (
        id              BIGSERIAL PRIMARY KEY,
        run_time        TIMESTAMPTZ,
        signal_id       INT,
        symbol          VARCHAR(16),
        dqs             INT,
        gate_reached    VARCHAR(32),
        gate_passed     BOOLEAN,
        gate_reason     TEXT,
        final_action    VARCHAR(16),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_audit_signal ON signal_audit_log(signal_id);
    CREATE INDEX IF NOT EXISTS idx_audit_time   ON signal_audit_log(run_time DESC);

    CREATE TABLE IF NOT EXISTS trade_event_log (
        id              BIGSERIAL PRIMARY KEY,
        trade_id        INT REFERENCES paper_trades(id),
        symbol          VARCHAR(16),
        event_type      VARCHAR(32),
        event_time      TIMESTAMPTZ DEFAULT NOW(),
        price_at_event  NUMERIC(18,8),
        balance_before  NUMERIC(12,4),
        balance_after   NUMERIC(12,4),
        margin_delta    NUMERIC(12,4),
        fee_applied     NUMERIC(12,6),
        pnl_delta       NUMERIC(12,4),
        running_pnl     NUMERIC(12,4),
        detail          JSONB
    );
    CREATE INDEX IF NOT EXISTS idx_event_trade ON trade_event_log(trade_id);
    CREATE INDEX IF NOT EXISTS idx_event_time  ON trade_event_log(event_time DESC);
    """
    try:
        cur.execute(log_tables_sql)
        conn.commit()
        print("[Migration] signal_audit_log + trade_event_log tables verified")
    except Exception as e:
        print(f"[Migration] Logging tables skipped: {e}")
        conn.rollback()

    # v0.3.3 — Trade debug context table
    debug_table_sql = """
    CREATE TABLE IF NOT EXISTS trade_debug_log (
        id              BIGSERIAL PRIMARY KEY,
        trade_id        INT REFERENCES paper_trades(id),
        symbol          VARCHAR(16),
        log_type        VARCHAR(16),
        logged_at       TIMESTAMPTZ DEFAULT NOW(),
        price           NUMERIC(18,8),
        btc_price       NUMERIC(18,8),
        btc_24h_ret     NUMERIC(8,4),
        symbol_24h_ret  NUMERIC(8,4),
        correlation_vs_btc NUMERIC(6,4),
        rsi_15m         NUMERIC(6,2),
        adx_4h          NUMERIC(6,2),
        atr_15m         NUMERIC(8,6),
        ema_20          NUMERIC(18,8),
        ema_50          NUMERIC(18,8),
        volume_ratio    NUMERIC(6,2),
        funding_rate    NUMERIC(10,6),
        oi_delta_pct    NUMERIC(8,4),
        ob_spread_pct   NUMERIC(8,4),
        ob_imbalance    NUMERIC(8,4),
        liquidation_side VARCHAR(8),
        session         VARCHAR(16),
        market_regime   VARCHAR(16),
        dqs_score       INT,
        dqs_breakdown   JSONB,
        calibrated_prob NUMERIC(6,4),
        brain_prob      NUMERIC(6,4),
        ai_interpretation TEXT,
        failure_category  VARCHAR(32),
        suggested_fix     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_debug_trade ON trade_debug_log(trade_id);
    CREATE INDEX IF NOT EXISTS idx_debug_logged ON trade_debug_log(logged_at DESC);
    CREATE INDEX IF NOT EXISTS idx_debug_category ON trade_debug_log(failure_category);
    """
    try:
        cur.execute(debug_table_sql)
        conn.commit()
        print("[Migration] trade_debug_log table verified")
    except Exception as e:
        print(f"[Migration] trade_debug_log skipped: {e}")
        conn.rollback()

    # ── v0.6 — Calibration schema (signal_node_log, confidence_matrix, calibration_log, symbol_status) ──

    # signal_node_log — per-node decision logging with scan_id
    signal_node_log_sql = """
    CREATE TABLE IF NOT EXISTS signal_node_log (
        id              SERIAL PRIMARY KEY,
        scan_id         UUID,
        signal_id       INTEGER,
        run_time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        pipeline_run_id TEXT,
        symbol          TEXT NOT NULL,
        dqs             NUMERIC(6,2),
        side            TEXT,
        strategy        TEXT,
        session         TEXT,
        node            TEXT NOT NULL,
        node_index      INTEGER,
        action          TEXT NOT NULL,
        reason          TEXT,
        calibrated_prob NUMERIC(5,4),
        gate_threshold  NUMERIC(5,4),
        detail          JSONB,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_snl_scan_id    ON signal_node_log (scan_id);
    CREATE INDEX IF NOT EXISTS idx_snl_symbol_run ON signal_node_log (symbol, run_time DESC);
    CREATE INDEX IF NOT EXISTS idx_snl_action     ON signal_node_log (action, node, run_time DESC);
    """
    try:
        cur.execute(signal_node_log_sql)
        conn.commit()
        print("[Migration] signal_node_log table verified")
    except Exception as e:
        print(f"[Migration] signal_node_log skipped: {e}")
        conn.rollback()

    # confidence_matrix — multi-dimensional calibration lookup
    confidence_matrix_sql = """
    CREATE TABLE IF NOT EXISTS confidence_matrix (
        id              SERIAL PRIMARY KEY,
        version         INTEGER NOT NULL DEFAULT 1,
        dqs_bucket      INTEGER NOT NULL,
        symbol          TEXT,
        side            TEXT,
        strategy        TEXT,
        session         TEXT,
        sample_size     NUMERIC(8,2) NOT NULL DEFAULT 0,
        win_rate        NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
        avg_pnl_usd     NUMERIC(12,4),
        wilson_lower    NUMERIC(5,4),
        wilson_upper    NUMERIC(5,4),
        source          TEXT NOT NULL DEFAULT 'mixed',
        active_dims     TEXT[],
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_conf_matrix_unique
        ON confidence_matrix (version, dqs_bucket,
            COALESCE(symbol,'_'), COALESCE(side,'_'),
            COALESCE(strategy,'_'), COALESCE(session,'_'));
    CREATE INDEX IF NOT EXISTS idx_conf_matrix_lookup
        ON confidence_matrix (version, dqs_bucket, symbol, side, strategy, session);
    """
    try:
        cur.execute(confidence_matrix_sql)
        conn.commit()
        print("[Migration] confidence_matrix table verified")
    except Exception as e:
        print(f"[Migration] confidence_matrix skipped: {e}")
        conn.rollback()

    # calibration_log — calibration run history
    calibration_log_sql = """
    CREATE TABLE IF NOT EXISTS calibration_log (
        id              SERIAL PRIMARY KEY,
        run_at          TIMESTAMPTZ DEFAULT NOW(),
        version         INTEGER NOT NULL,
        source          TEXT NOT NULL,
        trades_used     NUMERIC(8,2),
        symbols         TEXT[],
        date_range      JSONB,
        matrix_dims     TEXT[],
        matrix_summary  JSONB,
        changes_made    JSONB,
        brier_score     NUMERIC(6,4),
        log_loss        NUMERIC(6,4),
        calibration_error NUMERIC(6,4),
        notes           TEXT
    );
    """
    try:
        cur.execute(calibration_log_sql)
        conn.commit()
        print("[Migration] calibration_log table verified")
    except Exception as e:
        print(f"[Migration] calibration_log skipped: {e}")
        conn.rollback()

    # symbol_status — symbol enable/disable tracking
    symbol_status_sql = """
    CREATE TABLE IF NOT EXISTS symbol_status (
        symbol          TEXT PRIMARY KEY,
        enabled         BOOLEAN NOT NULL DEFAULT TRUE,
        disabled_at     TIMESTAMPTZ,
        disabled_reason TEXT,
        disabled_until  TIMESTAMPTZ,
        last_updated    TIMESTAMPTZ DEFAULT NOW()
    );
    INSERT INTO symbol_status (symbol) VALUES
        ('BTCUSDT'), ('ETHUSDT'), ('SOLUSDT'),
        ('XRPUSDT'), ('BNBUSDT'), ('DOGEUSDT')
    ON CONFLICT DO NOTHING;
    """
    try:
        cur.execute(symbol_status_sql)
        conn.commit()
        print("[Migration] symbol_status table verified")
    except Exception as e:
        print(f"[Migration] symbol_status skipped: {e}")
        conn.rollback()

    # ── HCS column additions ──
    hcs_migrations = [
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS tech_raw            NUMERIC(6,2)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS struct_raw          NUMERIC(6,2)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS ctx_raw             NUMERIC(6,2)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS trend_mult          NUMERIC(5,3)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS session_mult        NUMERIC(5,3)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS symbol_mult         NUMERIC(5,3)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS combined_mult       NUMERIC(5,3)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS mult_cap_hit        BOOLEAN DEFAULT FALSE",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS rsi_overbought_long BOOLEAN DEFAULT FALSE",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS regime              TEXT",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS calibrated_prob     NUMERIC(5,4)",
        "ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS scan_id             UUID",
    ]
    for sql in hcs_migrations:
        try:
            cur.execute(sql)
            conn.commit()
        except Exception as e:
            print(f"[Migration] HCS column skipped: {e}")
            conn.rollback()
    print("[Migration] HCS column additions applied")

    # ── paper_trades column additions ──
    pt_migrations = [
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_rsi       NUMERIC(6,2)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_adx       NUMERIC(6,2)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_session   TEXT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_strategy  TEXT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_regime    TEXT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS calibrated_prob NUMERIC(5,4)",
    ]
    for sql in pt_migrations:
        try:
            cur.execute(sql)
            conn.commit()
        except Exception as e:
            print(f"[Migration] paper_trades column skipped: {e}")
            conn.rollback()
    print("[Migration] paper_trades column additions applied")

    # Checksum columns for immutable audit trail
    checksum_migrations = [
        "ALTER TABLE error_log ADD COLUMN IF NOT EXISTS checksum VARCHAR(16)",
        "ALTER TABLE signal_audit_log ADD COLUMN IF NOT EXISTS checksum VARCHAR(16)",
    ]
    for sql in checksum_migrations:
        try:
            cur.execute(sql)
            conn.commit()
        except Exception as e:
            print(f"[Migration] checksum column skipped: {e}")
            conn.rollback()
    print("[Migration] checksum columns applied")

    # Verify
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_name IN ('signal_state','signal_state_log','risk_ledger','trade_count_log','portfolio_snapshot','error_log','pipeline_heartbeat','predictions','model_state','pipeline_trace')
        ORDER BY table_name
    """)
    created = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()

    print(f"[Migration] Created {len(created)} new tables:")
    for t in created:
        print(f"  - {t}")
    print("[Migration] Done.")


if __name__ == "__main__":
    migrate()
