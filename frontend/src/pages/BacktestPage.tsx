import { useState } from 'react';
import { FlaskConical, MessageSquare, BarChart3 } from 'lucide-react';
import { API_BASE } from '../config';
import BacktestConfigForm from '../components/backtest/BacktestConfigForm';
import BacktestResults from '../components/backtest/BacktestResults';
import AiChatPanel from '../components/backtest/AiChatPanel';
import { useBacktestStore } from '../stores/backtestStore';
import type { BacktestConfig, StageResult, BacktestRun } from '../types';

type SubTab = 'config' | 'chat' | 'results';

export default function BacktestPage() {
  const [subTab, setSubTab] = useState<SubTab>('config');
  const [running, setRunning] = useState(false);
  const [stageResults, setStageResults] = useState<StageResult[]>([
    { stage: 'd1', status: 'pending' },
    { stage: 'd4', status: 'pending' },
    { stage: 'd5a', status: 'pending' },
    { stage: 'd5b', status: 'pending' },
    { stage: 'd5c', status: 'pending' },
    { stage: 'd6', status: 'pending' },
    { stage: 'd7', status: 'pending' },
  ]);
  const [metrics, setMetrics] = useState<Record<string, number> | null>(null);
  const [d7Scenarios, setD7Scenarios] = useState<Record<string, { sharpe?: number; passed?: boolean }> | null>(null);
  const [decision, setDecision] = useState<any | null>(null);

  const store = useBacktestStore();
  const config = store.config;

  const runPipeline = async (cfg: BacktestConfig) => {
    store.setConfig(cfg);
    setRunning(true);
    setSubTab('results');

    const runId = crypto.randomUUID();
    const allStages = ['d1', 'd4', 'd5a', 'd5b', 'd5c', 'd6', 'd7'];
    const initialStages = allStages.map((s) => ({
      stage: s,
      status: (cfg.stages.includes(s) ? 'pending' : 'pending') as StageResult['status'],
    }));
    setStageResults(initialStages);
    setMetrics(null);
    setD7Scenarios(null);
    setDecision(null);

    // Create strategy first
    let strategyId: string | undefined;
    try {
      const stratRes = await fetch(`${API_BASE}/api/engine/strategy/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: `${cfg.symbol}_${cfg.strategyType}_${cfg.interval}`,
          type: cfg.strategyType,
          parameters: {
            rsi_threshold: cfg.parameters.rsi_threshold,
            adx_min: cfg.parameters.adx_min,
            sl_mult: cfg.parameters.sl_mult,
            tp_mult: cfg.parameters.tp_mult,
            capital: cfg.parameters.capital,
            leverage: cfg.parameters.leverage,
            risk_per_trade_pct: cfg.parameters.risk_per_trade_pct,
            session_gating: cfg.parameters.session_gating,
            direction: cfg.parameters.direction,
          },
          rules: [
            `rsi < ${cfg.parameters.rsi_threshold}`,
            `adx > ${cfg.parameters.adx_min}`,
            `direction: ${cfg.parameters.direction}`,
            cfg.parameters.session_gating ? 'session: london+ny_late' : 'session: any',
          ],
        }),
      });
      const stratData = await stratRes.json();
      if (stratData.id) strategyId = stratData.id;
    } catch {
      /* strategy creation failed, continue without id */
    }

    // Run each selected stage sequentially
    let finalStages = [...initialStages];
    for (const stage of cfg.stages) {
      finalStages = finalStages.map((s) =>
        s.stage === stage ? { ...s, status: 'running' as const } : s
      );
      setStageResults([...finalStages]);

      try {
        const res = await fetch(`${API_BASE}/api/engine/pipeline/${stage}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            symbol: cfg.symbol,
            interval: cfg.interval,
            start: cfg.startDate,
            end: cfg.endDate,
            strategy_id: strategyId,
            params: {
              rsi_threshold: cfg.parameters.rsi_threshold,
              adx_min: cfg.parameters.adx_min,
              sl_mult: cfg.parameters.sl_mult,
              tp_mult: cfg.parameters.tp_mult,
              leverage: cfg.parameters.leverage,
              risk_per_trade_pct: cfg.parameters.risk_per_trade_pct,
              session_gating: cfg.parameters.session_gating,
              direction: cfg.parameters.direction,
            },
            initial_capital: cfg.parameters.capital,
          }),
        });

        const data = await res.json();
        const passed = data.passed !== false && !data.error;

        finalStages = finalStages.map((s) =>
          s.stage === stage
            ? { ...s, status: passed ? ('pass' as const) : ('fail' as const), metrics: data }
            : s
        );
        setStageResults([...finalStages]);

        if (data.metrics) setMetrics(data.metrics);
        if (data.scenarios) setD7Scenarios(data.scenarios);
        if (data.decision) setDecision(data.decision);

        if (!passed && (stage === 'd1' || stage === 'd5a')) break;
      } catch {
        finalStages = finalStages.map((s) =>
          s.stage === stage ? { ...s, status: 'fail' as const } : s
        );
        setStageResults([...finalStages]);
        break;
      }
    }

    // Save run to store
    const run: BacktestRun = {
      id: runId,
      timestamp: Date.now(),
      config: cfg,
      strategyId,
      stages: finalStages,
      metrics: metrics ?? undefined,
      d7Scenarios: d7Scenarios ?? undefined,
      decision: decision ?? undefined,
    };
    store.addRun(run);
    setRunning(false);
  };

  const SUB_TABS: { key: SubTab; Icon: React.ElementType; label: string }[] = [
    { key: 'config', Icon: FlaskConical, label: 'Config' },
    { key: 'chat', Icon: MessageSquare, label: 'AI Chat' },
    { key: 'results', Icon: BarChart3, label: 'Results' },
  ];

  return (
    <div className="bt-page">
      <div className="section-title">Backtest Engine</div>
      <div className="body-sm" style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
        Configure, chat with the AI advisor, and run pipeline stages.
      </div>

      {/* Sub-tabs */}
      <div className="bt-subtabs">
        {SUB_TABS.map(({ key, Icon, label }) => (
          <button
            key={key}
            className={`bt-subtab ${subTab === key ? 'active' : ''}`}
            onClick={() => setSubTab(key)}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {/* Config Tab */}
      {subTab === 'config' && (
        <BacktestConfigForm onRun={runPipeline} running={running} />
      )}

      {/* Chat Tab */}
      {subTab === 'chat' && (
        <AiChatPanel configContext={config} />
      )}

      {/* Results Tab */}
      {subTab === 'results' && (
        <BacktestResults
          currentStages={stageResults}
          currentMetrics={metrics}
          currentD7={d7Scenarios}
          currentDecision={decision}
          onRerun={() => setSubTab('config')}
        />
      )}
    </div>
  );
}
