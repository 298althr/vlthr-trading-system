// ── All shared TypeScript types ───────────────────────────────────────────────

export interface HCSSignal {
  id: string;
  dbId: number;
  asset: string;
  type: 'LONG' | 'SHORT';
  confidence: number;
  grade: string;
  signal_strength: string;
  price: number;
  tp: number;
  sl: number;
  rr: number;
  dollar_risk: number;
  actual_dollar_risk: number;
  leveraged_notional: number;
  qty_contracts: number;
  rsi: number;
  adx: number;
  funding_rate: number;
  oi_delta_pct: number;
  ob_imbalance_pct: number;
  technical_score: number;
  market_structure_score: number;
  funding_oi_score: number;
  session_symbol_score: number;
  session: string;
  trade_decision: string;
  action: string;
  summary: string;
  time: string;
  scan_time_utc: string;
  signal_bar_utc: string;
  trafficLight: 'GREEN' | 'RED';
  timestamp: number;
  isPowerTrade: boolean;
  isExpired: boolean;
  expiryReason: string;
  ageMinutes: number;
  lifecycle_state: 'PENDING' | 'IN_TRADE' | 'TP_HIT' | 'SL_HIT' | 'TIME_EXIT' | 'MANUAL' | 'CANCELED' | 'ABANDONED' | string;
}

export interface WatchlistGate {
  pass: boolean;
  label: string;
}

export interface WatchlistItem {
  id: string;
  symbol: string;
  status: 'CONFIRMED' | 'APPROACHING' | 'OFF_SESSION' | 'WATCHING';
  quality: string;
  gates_passed: string;
  gates?: Record<'session' | 'adx' | 'direction' | 'rsi' | 'bull', WatchlistGate>;
  current_price: number;
  rsi: number;
  adx: number;
  atr: number;
  session: string;
  regime: string;
  h4_direction: string;
  h4_ema_trend: string;
  sl_level: number;
  tp_level: number;
  notes: string;
  scan_time_utc: string;
  timestamp: number;
}

export interface PowerTrade {
  id: string;
  dbId: number;
  signalId: number;
  symbol: string;
  confidence: number;
  signal_strength: string;
  price: number;
  tp: number;
  sl: number;
  rr: number;
  dollar_risk: number;
  leveraged_notional: number;
  session: string;
  h4_direction: string;
  action: string;
  summary: string;
  urgency: string;
  analyst_note: string;
  status: string;
  promoted_at: string;
  timestamp: number;
}

export interface Winner {
  id: string;
  symbol: string;
  confidence: number;
  signal_strength: string;
  entry_price: number;
  exit_price: number;
  net_pnl_pct: number;
  net_pnl_usd: number;
  hours_held: number;
  exit_reason: string;
  session: string;
  recorded_at: string;
  timestamp: number;
}

export interface PaperAccount {
  balance: number;
  wallet_balance: number;
  available: number;
  equity: number;
  margin_used: number;
  total_pnl: number;
  total_pnl_usd: number;
  total_trades: number;
  win_rate: number;
  realized_pnl: number;
  cumulative_funding_paid: number;
  live_floating_pnl: number;
  live_total_pnl_usd: number;
  live_win_rate: number;
}

export interface PaperTrade {
  id: number;
  signalId: number | null;
  symbol: string;
  side: string;
  entry_price: number;
  entry_price_planned: number;
  exit_price: number | null;
  sl_price: number;
  tp_price: number;
  qty: number;
  leverage: number;
  leveraged_notional: number;
  margin: number;
  confidence: number;
  funding_rate: number;
  fees_total: number;
  pnl: number;
  pnl_pct: number;
  live_pnl: number | null;
  live_pnl_pct: number | null;
  live_price: number | null;
  pct_to_tp: number | null;
  pct_to_sl: number | null;
  status: string;
  notes: string;
  entry_time: string | null;
  exit_time: string | null;
  exit_reason: string | null;
  hours_held: number | null;
}

export interface HistoryItem {
  id: string;
  asset: string;
  type: string;
  grade: string;
  price: number;
  status: string;
  pnl: string;
  time: string;
  timestamp: number;
}

export interface OrderbookData {
  bids: [number, number][];
  asks: [number, number][];
  bestBid: number;
  bestAsk: number;
  depthBid: number;
  depthAsk: number;
  imbalance: number;
  spread: number;
}

export interface OIData {
  openInterest: number;
  oiDeltaPct: number;
}

export interface FundingData {
  rate: number;
  nextFundingTime: string | null;
}

export interface MarketIntel {
  symbol: string;
  price: number;
  change24h: number;
  orderbook: OrderbookData | null;
  oi: OIData | null;
  funding: FundingData | null;
  narrative: string;
  history: {
    prices: { time: number; price: number }[];
    funding: { time: number; rate: number }[];
    oi: { time: number; oi: number }[];
  };
}

export type Tab = 'dashboard' | 'signals' | 'vtc' | 'backtest' | 'paper' | 'demo';

export interface VTCState {
  price: number;
  score: number;
  confidence: number;
  marketCap: number;
  nav: number;
  supply: number;
  timestamp: string;
  history: { timestamp: string; price: number }[];
  scores: { timestamp: string; score: number }[];
  treasury: {
    total_supply: number;
    circulating_supply: number;
    minted: number;
    burned: number;
  };
  audit: {
    id: string;
    timestamp: string;
    score: number;
    old_price: number;
    new_price: number;
    reason: string;
  }[];
  vector: {
    timestamp: string;
    sharpe: number;
    sortino: number;
    drawdown: number;
    expectancy: number;
    win_rate: number;
    capital_growth: number;
    stability: number;
    risk_score: number;
  } | null;
  layers: {
    performance: number;
    risk: number;
    growth: number;
  };
}

export type ModalState =
  | { type: 'paper'; signal: HCSSignal }
 | { type: 'close'; trade: PaperTrade }
  | null;

// ── Backtest / AI Types ─────────────────────────────────────────────────────

export interface ThinkStep {
  type: 'reasoning' | 'tool_call' | 'tool_result';
  label: string;
  detail: string;
  timestamp: number;
}

export interface ToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

export interface ToolResult {
  tool_call_id: string;
  role: 'tool';
  content: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  thinking?: ThinkStep[];
  timestamp: number;
}

export interface BacktestConfig {
  symbol: string;
  interval: string;
  startDate: string;
  endDate: string;
  strategyType: string;
  parameters: {
    rsi_threshold: number;
    adx_min: number;
    sl_mult: number;
    tp_mult: number;
    capital: number;
    leverage: number;
    risk_per_trade_pct: number;
    session_gating: boolean;
    direction: 'long' | 'both';
  };
  stages: string[];
}

export interface StageResult {
  stage: string;
  status: 'pending' | 'running' | 'pass' | 'fail';
  metrics?: Record<string, unknown>;
}

export interface BacktestRun {
  id: string;
  timestamp: number;
  config: BacktestConfig;
  strategyId?: string;
  stages: StageResult[];
  metrics?: Record<string, number>;
  d7Scenarios?: Record<string, { sharpe?: number; passed?: boolean }>;
  decision?: Decision | null;
}

export interface Decision {
  id: string;
  timestamp: string;
  context: {
    symbol: string;
    timeframe: string;
    objective: string;
    risk_profile: string;
  };
  hypothesis: {
    strategy_type: string;
    indicators: string[];
    parameters: Record<string, number | string>;
  };
  backtest: {
    win_rate: number;
    profit_factor: number;
    sharpe: number;
    sortino: number;
    cagr: number;
    drawdown: number;
    exposure: number;
    expectancy: number;
  };
  validation: {
    walkforward: string;
    montecarlo: string;
    oos: string;
  };
  belief: {
    score: number;
    confidence: number;
    uncertainty: number;
  };
  recommendation: {
    direction: 'LONG' | 'SHORT' | 'NEUTRAL';
    entry: number;
    stop: number;
    target: number;
  };
  outcome?: 'pending' | 'won' | 'lost';
}

export interface BacktestSessionMeta {
  id: string;
  label: string;
  createdAt: number;
  model?: string;
}
