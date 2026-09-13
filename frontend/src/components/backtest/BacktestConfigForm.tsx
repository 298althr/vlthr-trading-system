import { useState, useEffect } from 'react';
import { FlaskConical, ChevronDown, ChevronUp, Play } from 'lucide-react';
import { API_BASE } from '../../config';
import GlassDropdown from '../shared/GlassDropdown';
import { useBacktestStore } from '../../stores/backtestStore';
import type { BacktestConfig } from '../../types';

const INTERVALS = ['5m', '15m', '30m', '1h', '4h'];
const STRATEGY_TYPES = [
  { key: 'pullback_to_trend', label: 'PullbackToTrend' },
  { key: 'mean_reversion', label: 'Mean Reversion' },
  { key: 'trend_following', label: 'Trend Following' },
  { key: 'breakout', label: 'Breakout' },
  { key: 'momentum', label: 'Momentum' },
] as const;

const STAGE_OPTIONS = [
  { key: 'd1', label: 'D1 Data Audit', default: true },
  { key: 'd4', label: 'D4 Feature Discovery', default: false },
  { key: 'd5a', label: 'D5A Mechanical Backtest', default: true },
  { key: 'd5b', label: 'D5B Walk-Forward', default: true },
  { key: 'd5c', label: 'D5C DQS Filter', default: false },
  { key: 'd6', label: 'D6 Leverage Risk', default: false },
  { key: 'd7', label: 'D7 Reality Probe', default: false },
] as const;

interface Props {
  onRun: (cfg: BacktestConfig) => void;
  running: boolean;
}

export default function BacktestConfigForm({ onRun, running }: Props) {
  const store = useBacktestStore();
  const [symbols, setSymbols] = useState<string[]>(['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT']);
  const [showParams, setShowParams] = useState(true);

  const [cfg, setCfg] = useState<BacktestConfig>(store.config);

  useEffect(() => {
    fetch(`${API_BASE}/api/engine/data/symbols`)
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.symbols)) setSymbols(data.symbols);
      })
      .catch(() => {/* fallback to default symbols */});
  }, []);

  useEffect(() => {
    store.setConfig(cfg);
  }, [cfg]);

  const toggleStage = (key: string) => {
    setCfg(prev => ({
      ...prev,
      stages: prev.stages.includes(key)
        ? prev.stages.filter(s => s !== key)
        : [...prev.stages, key],
    }));
  };

  const updateParam = (key: keyof BacktestConfig['parameters'], val: number | boolean | string) => {
    setCfg(prev => ({ ...prev, parameters: { ...prev.parameters, [key]: val } }));
  };

  return (
    <div className="bt-config-card">
      <div className="bt-config-header">
        <FlaskConical size={18} className="bt-config-icon" />
        <span className="bt-config-title">Backtest Configuration</span>
      </div>

      <div className="bt-form-grid">
        <GlassDropdown
          label="Symbol"
          value={cfg.symbol}
          options={symbols.map(s => ({ value: s, label: s }))}
          onChange={(val) => setCfg(prev => ({ ...prev, symbol: val }))}
        />

        <GlassDropdown
          label="Interval"
          value={cfg.interval}
          options={INTERVALS.map(i => ({ value: i, label: i }))}
          onChange={(val) => setCfg(prev => ({ ...prev, interval: val }))}
        />

        <div className="bt-form-group">
          <label className="bt-form-label">Start Date</label>
          <input
            type="date"
            className="bt-input"
            value={cfg.startDate}
            onChange={e => setCfg(prev => ({ ...prev, startDate: e.target.value }))}
          />
        </div>

        <div className="bt-form-group">
          <label className="bt-form-label">End Date</label>
          <input
            type="date"
            className="bt-input"
            value={cfg.endDate}
            onChange={e => setCfg(prev => ({ ...prev, endDate: e.target.value }))}
          />
        </div>
      </div>

      <div className="bt-form-group" style={{ marginTop: 16 }}>
        <label className="bt-form-label">Strategy Type</label>
        <div className="bt-chip-group">
          {STRATEGY_TYPES.map(st => (
            <button
              key={st.key}
              className={`bt-chip ${cfg.strategyType === st.key ? 'active' : ''}`}
              onClick={() => setCfg(prev => ({ ...prev, strategyType: st.key }))}
            >
              {st.label}
            </button>
          ))}
        </div>
      </div>

      <div className="bt-collapsible" style={{ marginTop: 16 }}>
        <button className="bt-collapsible-toggle" onClick={() => setShowParams(v => !v)}>
          <span>Parameters</span>
          {showParams ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {showParams && (
          <div className="bt-params-grid">
            <ParamSlider label="RSI Threshold" min={20} max={50} step={1}
              value={cfg.parameters.rsi_threshold}
              onChange={v => updateParam('rsi_threshold', v)} />
            <ParamSlider label="ADX Minimum" min={15} max={35} step={1}
              value={cfg.parameters.adx_min}
              onChange={v => updateParam('adx_min', v)} />
            <ParamSlider label="SL x ATR" min={1.0} max={3.0} step={0.1}
              value={cfg.parameters.sl_mult}
              onChange={v => updateParam('sl_mult', v)} />
            <ParamSlider label="TP x ATR" min={1.5} max={4.0} step={0.1}
              value={cfg.parameters.tp_mult}
              onChange={v => updateParam('tp_mult', v)} />
            <ParamSlider label="Leverage" min={1} max={10} step={1}
              value={cfg.parameters.leverage}
              onChange={v => updateParam('leverage', v)} />
            <ParamSlider label="Risk / Trade (%)" min={0.5} max={5.0} step={0.5}
              value={cfg.parameters.risk_per_trade_pct}
              onChange={v => updateParam('risk_per_trade_pct', v)} />
            <div className="bt-form-group">
              <label className="bt-form-label">Initial Capital ($)</label>
              <input
                type="number"
                className="bt-input"
                value={cfg.parameters.capital}
                onChange={e => updateParam('capital', parseFloat(e.target.value) || 0)}
              />
            </div>
            <div className="bt-form-group">
              <label className="bt-form-label">Direction</label>
              <div className="bt-chip-group">
                {(['long', 'both'] as const).map(d => (
                  <button
                    key={d}
                    className={`bt-chip ${cfg.parameters.direction === d ? 'active' : ''}`}
                    onClick={() => updateParam('direction', d)}
                  >
                    {d === 'long' ? 'Long Only' : 'Long & Short'}
                  </button>
                ))}
              </div>
            </div>
            <div className="bt-form-group" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <label className="bt-form-label">Session Gating</label>
              <input
                type="checkbox"
                checked={cfg.parameters.session_gating}
                onChange={e => updateParam('session_gating', e.target.checked)}
                style={{ width: 18, height: 18, accentColor: 'var(--primary)' }}
              />
              <span className="body-sm" style={{ color: 'var(--text-secondary)' }}>London + NY Late only</span>
            </div>
          </div>
        )}
      </div>

      <div className="bt-form-group" style={{ marginTop: 16 }}>
        <label className="bt-form-label">Pipeline Stages</label>
        <div className="bt-stage-grid">
          {STAGE_OPTIONS.map(stage => (
            <label key={stage.key} className="bt-checkbox-row">
              <input
                type="checkbox"
                checked={cfg.stages.includes(stage.key)}
                onChange={() => toggleStage(stage.key)}
              />
              <span className="bt-checkmark" />
              <span className="bt-checkbox-label">{stage.label}</span>
            </label>
          ))}
        </div>
      </div>

      <button
        className="btn-primary bt-run-btn"
        onClick={() => onRun(cfg)}
        disabled={running || cfg.stages.length === 0}
      >
        <Play size={16} />
        {running ? 'Running Pipeline...' : 'Run Pipeline'}
      </button>
    </div>
  );
}

function ParamSlider({ label, min, max, step, value, onChange }: {
  label: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void;
}) {
  return (
    <div className="bt-form-group">
      <div className="bt-slider-header">
        <label className="bt-form-label">{label}</label>
        <span className="bt-slider-value">{value}</span>
      </div>
      <input
        type="range"
        className="bt-slider"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
      />
    </div>
  );
}
