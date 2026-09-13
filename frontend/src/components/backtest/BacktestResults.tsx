import { useState, useEffect } from 'react';
import {
  TrendingUp, CheckCircle2, XCircle, Loader2, Save, ArrowRight,
  Calendar, FlaskConical, Trash2, X, ChevronRight, Activity
} from 'lucide-react';
import { API_BASE } from '../../config';
import { useBacktestStore } from '../../stores/backtestStore';
import type { BacktestRun, StageResult, Decision, PaperTrade } from '../../types';

const STAGE_LABELS: Record<string, string> = {
  d1: 'D1 Audit', d4: 'D4 Features', d5a: 'D5A Backtest',
  d5b: 'D5B Walk-Forward', d5c: 'D5C DQS', d6: 'D6 Leverage', d7: 'D7 Stress',
};

interface Props {
  currentStages?: StageResult[];
  currentMetrics?: Record<string, number> | null;
  currentD7?: Record<string, { sharpe?: number; passed?: boolean }> | null;
  currentDecision?: Decision | null;
  onRerun?: () => void;
}

export default function BacktestResults({
  currentStages,
  currentMetrics,
  currentD7,
  currentDecision,
}: Props) {
  const { runs, deleteRun } = useBacktestStore();
  const [modalRun, setModalRun] = useState<BacktestRun | null>(null);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);

  useEffect(() => {
    setTradesLoading(true);
    fetch(`${API_BASE}/api/paper/trades`)
      .then(r => r.json())
      .then(data => {
        const all = [
          ...(data.active || []),
          ...(data.closed || []),
          ...(data.pending || []),
        ];
        // Sort by most recent activity (entry_time desc, fallback to id desc)
        all.sort((a: PaperTrade, b: PaperTrade) => {
          const ta = a.exit_time || a.entry_time || String(a.id);
          const tb = b.exit_time || b.entry_time || String(b.id);
          return tb.localeCompare(ta);
        });
        setTrades(all.slice(0, 100));
      })
      .catch(() => setTrades([]))
      .finally(() => setTradesLoading(false));
  }, []);

  const displayRuns = runs.length > 0 ? runs : [];

  const openModal = (run: BacktestRun) => setModalRun(run);
  const closeModal = () => setModalRun(null);

  return (
    <div className="bt-results">
      {/* Current run (if in progress) */}
      {currentStages && currentStages.some((s) => s.status !== 'pending') && (
        <div className="bt-current-run">
          <div className="bt-section-title">Current Run</div>
          <RunMiniCard
            run={{
              id: 'current',
              timestamp: Date.now(),
              config: useBacktestStore.getState().config,
              stages: currentStages,
              metrics: currentMetrics ?? undefined,
              d7Scenarios: currentD7 ?? undefined,
              decision: currentDecision ?? undefined,
            }}
            onClick={() => {}}
            isCurrent
          />
        </div>
      )}

      {/* Live Trade History */}
      <div className="bt-trade-history">
        <div className="bt-section-title">Live Trade History ({trades.length})</div>
        {tradesLoading ? (
          <div className="bt-empty-state"><Loader2 size={24} className="animate-spin" /> Loading trades...</div>
        ) : trades.length === 0 ? (
          <div className="bt-empty-state">
            <Activity size={48} style={{ marginBottom: 16, opacity: 0.4 }} />
            <div className="headline-sm">No trades yet</div>
            <div className="body-md">Run the pipeline or wait for auto-open signals to generate trades.</div>
          </div>
        ) : (
          <div className="bt-trade-table-wrapper">
            <table className="bt-trade-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Status</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>SL / TP</th>
                  <th>Leverage</th>
                  <th>PnL</th>
                  <th>Hold Time</th>
                  <th>Exit Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.map(t => (
                  <tr key={t.id}>
                    <td><span className="bt-trade-symbol">{t.symbol}</span></td>
                    <td><span className={`bt-trade-side ${t.side.toLowerCase()}`}>{t.side}</span></td>
                    <td>
                      <span className={`bt-trade-status ${t.status.toLowerCase()}`}>
                        {t.status}
                      </span>
                    </td>
                    <td>${t.entry_price?.toLocaleString() || t.entry_price_planned?.toLocaleString() || '—'}</td>
                    <td>{t.exit_price ? `$${t.exit_price.toLocaleString()}` : '—'}</td>
                    <td>${t.sl_price?.toLocaleString() || '—'} / ${t.tp_price?.toLocaleString() || '—'}</td>
                    <td>{t.leverage}x</td>
                    <td className={t.pnl > 0 ? 'bt-pnl-pos' : t.pnl < 0 ? 'bt-pnl-neg' : ''}>
                      {t.pnl !== undefined && t.pnl !== null
                        ? `${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}`
                        : t.live_pnl !== undefined && t.live_pnl !== null
                          ? `${t.live_pnl >= 0 ? '+' : ''}$${t.live_pnl.toFixed(2)} (open)`
                          : '—'}
                    </td>
                    <td>{t.hours_held !== null ? `${t.hours_held}h` : t.status === 'OPEN' ? 'Open' : '—'}</td>
                    <td>{t.exit_reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Runs listing */}
      {displayRuns.length > 0 ? (
        <div className="bt-runs-list">
          <div className="bt-section-title">Backtest History ({displayRuns.length})</div>
          <div className="bt-runs-grid">
            {displayRuns.map((run) => (
              <RunMiniCard
                key={run.id}
                run={run}
                onClick={() => openModal(run)}
                isActive={false}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="bt-empty-state">
          <TrendingUp size={48} style={{ marginBottom: 16, opacity: 0.4 }} />
          <div className="headline-sm">No backtests yet</div>
          <div className="body-md">Configure your backtest and click Run Pipeline to see results here.</div>
        </div>
      )}

      {/* Detail Modal */}
      {modalRun && (
        <div className="modal-backdrop" onClick={closeModal}>
          <div className="modal-glass bt-modal-wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">
                <FlaskConical size={18} />
                Backtest Result
              </div>
              <button className="modal-close pressable" onClick={closeModal}>
                <X size={20} />
              </button>
            </div>
            <div className="modal-body" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
              <RunDetailContent run={modalRun} />
            </div>
            <div className="modal-footer">
              <button className="btn-secondary pressable" style={{ flex: 1 }} onClick={closeModal}>
                Close
              </button>
              <button
                className="btn-primary pressable"
                style={{ flex: 1 }}
                onClick={() => {
                  deleteRun(modalRun.id);
                  closeModal();
                }}
              >
                <Trash2 size={14} /> Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Run Mini Card (listing) ────────────────────────────────────── */
function RunMiniCard({
  run,
  onClick,
  isActive,
  isCurrent,
}: {
  run: BacktestRun;
  onClick: () => void;
  isActive?: boolean;
  isCurrent?: boolean;
}) {
  const cfg = run.config;
  const sharpe = run.metrics?.sharpe;
  const winRate = run.metrics?.win_rate;
  const totalReturn = run.metrics?.total_return;
  const allPassed = run.stages.every((s) => s.status === 'pass' || s.status === 'pending');
  const anyFailed = run.stages.some((s) => s.status === 'fail');

  return (
    <button
      className={`bt-run-card ${isActive ? 'active' : ''} ${isCurrent ? 'current' : ''}`}
      onClick={onClick}
    >
      <div className="bt-run-card-header">
        <span className="bt-run-symbol">{cfg.symbol}</span>
        {anyFailed ? (
          <XCircle size={14} className="bt-run-status fail" />
        ) : allPassed ? (
          <CheckCircle2 size={14} className="bt-run-status pass" />
        ) : (
          <Loader2 size={14} className="bt-run-status running animate-spin" />
        )}
      </div>
      <div className="bt-run-card-meta">
        <span><Calendar size={12} /> {cfg.startDate.slice(0, 7)} → {cfg.endDate.slice(0, 7)}</span>
        <span><FlaskConical size={12} /> {cfg.strategyType.replace('_', ' ')}</span>
      </div>
      <div className="bt-run-card-metrics">
        <MiniMetric label="Sharpe" value={fmt(sharpe)} />
        <MiniMetric label="Win Rate" value={winRate !== undefined ? `${fmt(winRate, true)}%` : '—'} />
        <MiniMetric
          label="Return"
          value={totalReturn !== undefined ? `${fmt(totalReturn, true)}%` : '—'}
          tone={totalReturn && totalReturn > 0 ? 'success' : totalReturn && totalReturn < 0 ? 'danger' : undefined}
        />
      </div>
      {isCurrent && <div className="bt-run-current-badge">LIVE</div>}
      {!isCurrent && (
        <div className="bt-run-chevron">
          <ChevronRight size={16} />
        </div>
      )}
    </button>
  );
}

function MiniMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'success' | 'danger';
}) {
  return (
    <div className="bt-mini-metric">
      <span className="bt-mini-label">{label}</span>
      <span className={`bt-mini-value ${tone || ''}`}>{value}</span>
    </div>
  );
}

/* ── Run Detail (modal content) ─────────────────────────────────── */
function RunDetailContent({ run }: { run: BacktestRun }) {
  return (
    <div className="bt-run-detail">
      {/* Stepper */}
      <div className="bt-stepper">
        {run.stages.map((s, i) => (
          <div key={s.stage} className="bt-step">
            <div className={`bt-step-circle ${s.status}`}>
              {s.status === 'running' ? <Loader2 size={14} className="animate-spin" />
                : s.status === 'pass' ? <CheckCircle2 size={14} />
                  : s.status === 'fail' ? <XCircle size={14} />
                    : <span>{i + 1}</span>}
            </div>
            <span className="bt-step-label">{STAGE_LABELS[s.stage] || s.stage}</span>
            {i < run.stages.length - 1 && (
              <div className={`bt-step-line ${s.status === 'pass' ? 'active' : ''}`} />
            )}
          </div>
        ))}
      </div>

      {/* Metrics */}
      {run.metrics && (
        <div className="bt-metrics-grid">
          <MetricCard label="Sharpe" value={fmt(run.metrics.sharpe)} />
          <MetricCard label="Win Rate" value={`${fmt(run.metrics.win_rate, true)}%`} />
          <MetricCard label="Max Drawdown" value={`${fmt(run.metrics.max_drawdown, true)}%`} tone={run.metrics.max_drawdown && run.metrics.max_drawdown > 20 ? 'danger' : 'neutral'} />
          <MetricCard label="Total Return" value={`${fmt(run.metrics.total_return, true)}%`} tone={run.metrics.total_return && run.metrics.total_return > 0 ? 'success' : 'danger'} />
          <MetricCard label="Trades" value={fmt(run.metrics.total_trades, false, 0)} />
          <MetricCard label="Profit Factor" value={fmt(run.metrics.profit_factor)} />
        </div>
      )}

      {/* D7 */}
      {run.d7Scenarios && Object.keys(run.d7Scenarios).length > 0 && (
        <div className="bt-d7-panel">
          <div className="bt-section-title">D7 Reality Probe</div>
          <div className="bt-d7-grid">
            {Object.entries(run.d7Scenarios).map(([name, scenario]) => (
              <div key={name} className={`bt-d7-card ${scenario.passed ? 'pass' : 'fail'}`}>
                <div className="bt-d7-name">{name.replace(/_/g, ' ')}</div>
                <div className="bt-d7-badge">{scenario.passed ? 'PASS' : 'FAIL'}</div>
                <div className="bt-d7-sharpe">Sharpe: {fmt(scenario.sharpe)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Decision */}
      {run.decision && (
        <div className="bt-decision-card">
          <div className="bt-decision-header">
            <span className="bt-decision-dir" data-dir={run.decision.recommendation.direction.toLowerCase()}>
              {run.decision.recommendation.direction}
            </span>
            <span className="bt-decision-belief">Belief {run.decision.belief.score}/100</span>
          </div>
          <div className="bt-decision-levels">
            <Level label="Entry" value={fmtPrice(run.decision.recommendation.entry)} />
            <Level label="Stop" value={fmtPrice(run.decision.recommendation.stop)} tone="danger" />
            <Level label="Target" value={fmtPrice(run.decision.recommendation.target)} tone="success" />
          </div>
          <div className="bt-decision-confidence">
            Confidence: {(run.decision.belief.confidence * 100).toFixed(0)}%
          </div>
          <div className="bt-decision-actions">
            <button className="btn-primary"><Save size={14} /> Store Decision</button>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'danger' | 'neutral' }) {
  return (
    <div className="bt-metric-card">
      <div className="bt-metric-label">{label}</div>
      <div className={`bt-metric-value ${tone || ''}`}>{value}</div>
    </div>
  );
}

function Level({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'danger' }) {
  return (
    <div className="bt-level">
      <span className="bt-level-label">{label}</span>
      <ArrowRight size={12} className="bt-level-arrow" />
      <span className={`bt-level-value ${tone || ''}`}>{value}</span>
    </div>
  );
}

function fmt(n: number | undefined, pct = false, digits = 2): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  if (pct) return (n * (n > 1 ? 1 : 100)).toFixed(digits);
  return n.toFixed(digits);
}

function fmtPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
