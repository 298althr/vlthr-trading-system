import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Bot, User, Sparkles, Loader2, Square, BrainCircuit, ChevronDown, ChevronUp, Terminal, Play, Trash2, Plus, Copy, Check } from 'lucide-react';
import { API_BASE } from '../../config';
import { useBacktestStore } from '../../stores/backtestStore';
import type { ChatMessage, ThinkStep, ToolCall, ToolResult, BacktestConfig } from '../../types';

const MAX_CONTEXT_MESSAGES = 25;

const MODELS = [
  { id: 'meta/llama-3.1-8b-instruct', name: 'Llama 3.1 8B', params: '8B', context: '128K', desc: 'Fast + tool calling — recommended' },
  { id: 'meta/llama-3.1-70b-instruct', name: 'Llama 3.1 70B', params: '70B', context: '128K', desc: 'Tool calling — slow cold-start' },
] as const;

const QUICK_ACTIONS = [
  'Probe this symbol',
  'Suggest best strategy',
  'Run D1 audit',
  'Analyze volatility',
  'Search memory',
  'Wipe memory',
];

/* ── Tool definitions (passed to LLM so AI knows what it can call) ─ */
const TOOLS = [
  { type: 'function', function: { name: 'get_data_range', description: 'Discover the earliest and latest available candle dates for a symbol and interval. Call this BEFORE any date-bound tool (get_candles, probe_symbol, backtest, etc.) so you know what date range to request.', parameters: { type: 'object', properties: { symbol: { type: 'string', description: 'e.g. BTCUSDT, ETHUSDT' }, interval: { type: 'string', description: '5m | 15m | 30m | 1h | 4h | 1d' } }, required: ['symbol', 'interval'] } } },
  { type: 'function', function: { name: 'get_candles', description: 'Fetch OHLCV candle data from Parquet store.', parameters: { type: 'object', properties: { symbol: { type: 'string', description: 'e.g. BTCUSDT, ETHUSDT' }, interval: { type: 'string', description: '5m | 15m | 30m | 1h | 4h' }, start: { type: 'string', description: 'ISO date e.g. 2025-01-01' }, end: { type: 'string', description: 'ISO date e.g. 2025-12-31' }, limit: { type: 'number', description: 'Max rows returned (default 500)' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'get_funding', description: 'Fetch funding rate history.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' } }, required: ['symbol', 'start', 'end'] } } },
  { type: 'function', function: { name: 'get_open_interest', description: 'Fetch open interest history.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' } }, required: ['symbol', 'start', 'end'] } } },
  { type: 'function', function: { name: 'calculate_features', description: 'Compute RSI, EMA, ATR, ADX, Bollinger Bands, VWAP for a symbol/interval range.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'create_strategy', description: 'Create a strategy. parameters field must be a JSON object. rules field must be a JSON array of condition strings. type: mean_reversion, trend_following, breakout, momentum, ict, smc.', parameters: { type: 'object', properties: { name: { type: 'string' }, type: { type: 'string' }, parameters: { type: 'object', description: 'Strategy params as plain object e.g. {"fast":10,"slow":20}' }, rules: { type: 'array', items: { type: 'string' }, description: 'Entry/exit condition strings' } }, required: ['name', 'type', 'parameters', 'rules'] } } },
  { type: 'function', function: { name: 'get_strategy', description: 'Retrieve a strategy by ID.', parameters: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] } } },
  { type: 'function', function: { name: 'start_backtest', description: 'Start a backtest simulation. Requires strategy_id, symbol, start/end as ISO dates, and interval.', parameters: { type: 'object', properties: { strategy_id: { type: 'string' }, symbol: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, interval: { type: 'string' }, initial_capital: { type: 'number' } }, required: ['strategy_id', 'symbol', 'start', 'end', 'interval'] } } },
  { type: 'function', function: { name: 'get_backtest_status', description: 'Check if a backtest is running or complete.', parameters: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] } } },
  { type: 'function', function: { name: 'get_backtest_result', description: 'Retrieve backtest metrics and trades.', parameters: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] } } },
  { type: 'function', function: { name: 'run_d1', description: 'Run D1 data audit: coverage, gaps, quality checks.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_d4', description: 'Run D4 feature discovery: indicator importance, regime detection.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_d5a', description: 'Run D5A mechanical backtest.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' }, initial_capital: { type: 'number' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_d5b', description: 'Run D5B walk-forward out-of-sample validation.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' }, initial_capital: { type: 'number' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_d5c', description: 'Run D5C DQS data quality score gate.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' }, initial_capital: { type: 'number' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_d6', description: 'Run D6 leverage risk analysis.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' }, leverage: { type: 'number' }, initial_capital: { type: 'number' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_d7', description: 'Run D7 reality probe: slippage, fees, latency, funding impact.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' }, initial_capital: { type: 'number' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'run_full_pipeline', description: 'Run the full D1-D7 pipeline.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, strategy_id: { type: 'string' }, initial_capital: { type: 'number' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'probe_symbol', description: 'Deep-probe a single symbol/timeframe: detect regimes, mean-reversion zones, momentum bursts, and rank feature predictive power. Use this BEFORE suggesting a strategy to backtest.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'probe_compare', description: 'Compare multiple symbols on the same timeframe to find the best candidate for a strategy archetype.', parameters: { type: 'object', properties: { symbols: { type: 'string', description: 'Comma-separated e.g. BTCUSDT,ETHUSDT,SOLUSDT' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' } }, required: ['symbols', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'probe_suggest', description: 'After probing, get concrete strategy parameters (SL, TP, thresholds) tailored to the detected regime.', parameters: { type: 'object', properties: { symbol: { type: 'string' }, interval: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' }, archetype: { type: 'string', description: 'mean_reversion | trend_following | breakout | momentum' } }, required: ['symbol', 'interval', 'start', 'end'] } } },
  { type: 'function', function: { name: 'create_decision', description: 'Store a trade decision with full context and metrics.', parameters: { type: 'object', properties: { context: { type: 'object' }, hypothesis: { type: 'object' }, backtest: { type: 'object' }, validation: { type: 'object' }, belief: { type: 'object' }, recommendation: { type: 'object' } }, required: ['context', 'hypothesis', 'backtest', 'validation', 'belief', 'recommendation'] } } },
  { type: 'function', function: { name: 'store_memory', description: 'Persist a decision to long-term memory.', parameters: { type: 'object', properties: { prompt: { type: 'string' }, context: { type: 'object' }, strategy: { type: 'object' }, results: { type: 'object' }, belief: { type: 'object' } }, required: ['prompt', 'context', 'strategy', 'results', 'belief'] } } },
  { type: 'function', function: { name: 'get_calibration', description: 'Query DQS calibration: maps DQS score buckets to realized win-rate from closed paper trades.', parameters: { type: 'object', properties: {}, required: [] } } },
  { type: 'function', function: { name: 'get_min_dqs', description: 'Get the minimum DQS score required to achieve a target win-rate based on historical calibration.', parameters: { type: 'object', properties: { target_win_rate: { type: 'number', description: 'Target win-rate % (default 55)' } }, required: [] } } },
  { type: 'function', function: { name: 'search_memory', description: 'Search past decisions.', parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] } } },
] as const;

interface Props {
  configContext: BacktestConfig;
}

/* ── Context compression (Page Index) ────────────────────────────── */
function buildRollingSummary(messages: ChatMessage[]): string {
  const userMsgs = messages.filter((m) => m.role === 'user');
  const assistantMsgs = messages.filter((m) => m.role === 'assistant' && !m.toolCalls);
  const toolCalls = messages.filter((m) => m.role === 'assistant' && m.toolCalls);
  const toolResults = messages.filter((m) => m.role === 'tool');

  const summaryParts: string[] = [];
  if (userMsgs.length > 0) {
    summaryParts.push(`User asked ${userMsgs.length} question(s): ${userMsgs.slice(-3).map((m) => `"${m.content.slice(0, 60)}..."`).join('; ')}`);
  }
  if (toolCalls.length > 0) {
    const calls = toolCalls.flatMap((m) => m.toolCalls?.map((tc) => tc.function.name) ?? []);
    summaryParts.push(`Tools called: ${[...new Set(calls)].join(', ')}`);
  }
  if (toolResults.length > 0) {
    summaryParts.push(`Tool results: ${toolResults.length} result(s) received.`);
  }
  if (assistantMsgs.length > 0) {
    summaryParts.push(`AI responses: ${assistantMsgs.length} message(s).`);
  }
  return `[Conversation summary] ${summaryParts.join(' | ')}`;
}

function prepareMessagesForChat(messages: ChatMessage[]) {
  const windowStart = Math.max(0, messages.length - MAX_CONTEXT_MESSAGES);
  const outsideWindow = messages.slice(0, windowStart);
  const insideWindow = messages.slice(windowStart);

  const prepared: { role: string; content: string; tool_call_id?: string; tool_calls?: unknown }[] = [];

  if (outsideWindow.length > 0) {
    prepared.push({ role: 'system', content: buildRollingSummary(outsideWindow) });
  }

  for (const m of insideWindow) {
    if (m.role === 'tool') {
      const tcId = (m.toolResults ?? [])[0]?.tool_call_id ?? '';
      prepared.push({ role: 'tool', tool_call_id: tcId, content: m.content });
    } else if (m.toolCalls && m.toolCalls.length > 0) {
      prepared.push({ role: m.role, content: m.content || '', tool_calls: m.toolCalls });
    } else {
      prepared.push({ role: m.role, content: m.content });
    }
  }

  return prepared;
}

/* ── Tool execution ─────────────────────────────────────────────── */
async function executeTool(name: string, args: Record<string, unknown>): Promise<unknown> {
  const sym = args.symbol as string;
  const now = new Date();
  const endDefault = now.toISOString().split('T')[0];
  const startDefault = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
  const start = (args.start ?? startDefault) as string;
  const end = (args.end ?? endDefault) as string;
  const interval = (args.interval ?? args.timeframe ?? '15m') as string;

  switch (name) {
    case 'get_data_range':
      return api(`/data/range?symbol=${sym}&interval=${interval}`);
    case 'get_candles':
      return api(`/candles?symbol=${sym}&interval=${interval}&start=${start}&end=${end}&limit=${(args.limit as number) ?? 500}`);
    case 'get_funding':
      return api(`/funding?symbol=${sym}&start=${start}&end=${end}`);
    case 'get_open_interest':
      return api(`/open-interest?symbol=${sym}&start=${start}&end=${end}`);
    case 'calculate_features':
    case 'list_features':
      return api(`/features?symbol=${sym}&interval=${interval}&start=${start}&end=${end}`);
    case 'create_strategy':
      return apiPost('/strategy/create', {
        name: args.name,
        type: args.type,
        parameters: typeof args.parameters === 'string' ? JSON.parse(args.parameters) : args.parameters,
        rules: typeof args.rules === 'string' ? JSON.parse(args.rules) : args.rules,
      });
    case 'get_strategy':
      return api(`/strategy/${args.id}`);
    case 'start_backtest':
      return apiPost('/backtest/start', {
        strategy_id: args.strategy_id,
        symbol: sym,
        interval,
        start,
        end,
        initial_capital: (args.initial_capital as number) ?? 10000,
      });
    case 'get_backtest_status':
      return api(`/backtest/status?id=${args.id}`);
    case 'get_backtest_result':
      return api(`/backtest/result?id=${args.id}`);
    case 'run_d1':
      return apiPost('/pipeline/d1', { symbol: sym, interval, start, end });
    case 'run_d4':
      return apiPost('/pipeline/d4', { symbol: sym, interval, start, end, strategy_id: args.strategy_id });
    case 'run_d5a':
      return apiPost('/pipeline/d5a', { symbol: sym, interval, start, end, strategy_id: args.strategy_id, initial_capital: args.initial_capital });
    case 'run_d5b':
      return apiPost('/pipeline/d5b', { symbol: sym, interval, start, end, strategy_id: args.strategy_id, initial_capital: args.initial_capital });
    case 'run_d5c':
      return apiPost('/pipeline/d5c', { symbol: sym, interval, start, end, strategy_id: args.strategy_id, initial_capital: args.initial_capital });
    case 'run_d6':
      return apiPost('/pipeline/d6', { symbol: sym, interval, start, end, strategy_id: args.strategy_id, leverage: args.leverage, initial_capital: args.initial_capital });
    case 'run_d7':
      return apiPost('/pipeline/d7', { symbol: sym, interval, start, end, strategy_id: args.strategy_id, initial_capital: args.initial_capital });
    case 'run_full_pipeline':
      return apiPost('/pipeline/full', { symbol: sym, interval, start, end, strategy_id: args.strategy_id, initial_capital: args.initial_capital });
    case 'probe_symbol':
      return apiPost('/probe/symbol', { symbol: sym, interval, start, end });
    case 'probe_compare':
      return apiPost('/probe/compare', { symbol: args.symbols, interval, start, end });
    case 'probe_suggest':
      return apiPost('/probe/suggest', { symbol: sym, interval, start, end, params: args.archetype ? { archetype: args.archetype } : {} });
    case 'create_decision':
      return apiPost('/decision/create', args);
    case 'store_memory':
      return apiPost('/memory/store', args);
    case 'get_calibration':
      return api('/calibrate/dqs');
    case 'get_min_dqs':
      return api(`/calibrate/min-dqs?target_win_rate=${(args.target_win_rate as number) ?? 55}`);
    case 'search_memory':
      return apiPost('/memory/search', { query: args.query });
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

async function api<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}/api/engine${path}`);
  if (!res.ok) throw new Error(`Engine API error ${res.status}: ${await res.text()}`);
  return res.json() as T;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/engine${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Engine API error ${res.status}: ${await res.text()}`);
  return res.json() as T;
}

/* ── Chat loop ──────────────────────────────────────────────────── */
async function callChat(msgs: ChatMessage[], model: string, withTools: boolean, signal?: AbortSignal) {
  const body: Record<string, unknown> = {
    model,
    messages: [
      { role: 'system', content: buildSystemContext() },
      ...prepareMessagesForChat(msgs),
    ],
    stream: false,
  };
  if (withTools) {
    body.tools = TOOLS;
  }

  const res = await fetch(`${API_BASE}/api/engine/ai/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) throw new Error(`Engine error ${res.status}: ${await res.text()}`);

  const data = await res.json();
  const choice = data.choices?.[0];
  const message = choice?.message ?? {};

  const toolCalls: ToolCall[] = (message.tool_calls ?? []).map(
    (tc: { id: string; function: { name: string; arguments: string } }) => ({
      id: tc.id,
      type: 'function' as const,
      function: { name: tc.function.name, arguments: tc.function.arguments },
    })
  );

  return { content: message.content ?? '', toolCalls };
}

async function runToolLoop(
  msgs: ChatMessage[],
  model: string,
  onContent: (text: string) => void,
  onThinkStep: (step: ThinkStep) => void,
  onToolStart: (name: string) => void,
  onToolEnd: (name: string, result: string) => void,
  thinking: ThinkStep[] = [],
  loopDepth: number = 0,
  signal?: AbortSignal
): Promise<ThinkStep[]> {
  if (signal?.aborted) return thinking;
  if (loopDepth >= 10) return thinking; // safety limit

  const { content, toolCalls } = await callChat(msgs, model, loopDepth === 0, signal);

  // Log AI reasoning before tool calls
  if (content && toolCalls.length > 0) {
    const step: ThinkStep = { type: 'reasoning', label: `Reasoning (turn ${loopDepth + 1})`, detail: content || '(planning tool calls)', timestamp: Date.now() };
    thinking.push(step);
    onThinkStep(step);
  }

  // Log each tool call decision
  if (toolCalls.length > 0) {
    for (const tc of toolCalls) {
      let parsedArgs: Record<string, unknown> = {};
      try { parsedArgs = JSON.parse(tc.function.arguments); } catch { /* ignore */ }
      const step: ThinkStep = { type: 'tool_call', label: `→ ${tc.function.name}`, detail: JSON.stringify(parsedArgs, null, 2), timestamp: Date.now() };
      thinking.push(step);
      onThinkStep(step);
    }
  }

  // No tools — final answer
  if (content && toolCalls.length === 0) {
    onContent(content);
    return thinking;
  }

  // Build assistant message with tool calls
  const assistantMsg: ChatMessage = {
    id: crypto.randomUUID(),
    role: 'assistant',
    content,
    toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
    timestamp: Date.now(),
  };

  const currentMessages = [...msgs, assistantMsg];

  // Execute tools
  if (toolCalls.length > 0) {
    const toolResults: ToolResult[] = [];
    for (const tc of toolCalls) {
      onToolStart(tc.function.name);
      try {
        const args = JSON.parse(tc.function.arguments);
        const result = await executeTool(tc.function.name, args);
        const resultStr = JSON.stringify(result).slice(0, 8000);
        toolResults.push({ tool_call_id: tc.id, role: 'tool', content: resultStr });

        const step: ThinkStep = { type: 'tool_result', label: `← ${tc.function.name} result`, detail: resultStr.slice(0, 500) + (resultStr.length > 500 ? '...' : ''), timestamp: Date.now() };
        thinking.push(step);
        onThinkStep(step);
        onToolEnd(tc.function.name, resultStr);
      } catch (err) {
        const errStr = `Error: ${err instanceof Error ? err.message : String(err)}`;
        toolResults.push({ tool_call_id: tc.id, role: 'tool', content: errStr });
        const step: ThinkStep = { type: 'tool_result', label: `✗ ${tc.function.name} error`, detail: errStr, timestamp: Date.now() };
        thinking.push(step);
        onThinkStep(step);
        onToolEnd(tc.function.name, errStr);
      }
    }

    // Add tool results to message history
    for (const tr of toolResults) {
      currentMessages.push({
        id: crypto.randomUUID(),
        role: 'tool',
        content: tr.content,
        toolResults: [tr],
        timestamp: Date.now(),
      });
    }

    // RECURSIVE: feed tool results back to AI for synthesis
    return runToolLoop(currentMessages, model, onContent, onThinkStep, onToolStart, onToolEnd, thinking, loopDepth + 1, signal);
  }

  return thinking;
}

/* ── Component ───────────────────────────────────────────────────── */
export default function AiChatPanel({ configContext }: Props) {
  const store = useBacktestStore();
  const { messages, thinkingSteps, isLoading, sessionIndex, sessionId, model } = store;
  const [input, setInput] = useState('');
  const [showThinkPad, setShowThinkPad] = useState(true);
  const [showSessions, setShowSessions] = useState(false);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    });
  };

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || isLoading) return;

    if (text.trim().toLowerCase() === 'wipe memory') {
      // Hard-wipe all backtest session data
      const keysToRemove: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && (k.startsWith('vlthr_bt_session_') || k === 'vlthr_bt_session_index')) {
          keysToRemove.push(k);
        }
      }
      keysToRemove.forEach(k => localStorage.removeItem(k));
      sessionStorage.removeItem('vlthr_bt_current_session');
      store.clearChat();
      store.newSession();
      store.addMessage({
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: 'All AI session memory wiped. Starting fresh.',
        timestamp: Date.now(),
      });
      return;
    }

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text.trim(),
      timestamp: Date.now(),
    };
    store.addMessage(userMsg);
    store.setIsLoading(true);
    store.clearThinking();
    setInput('');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const allMessages = [...messages, userMsg];
      const thinking = await runToolLoop(
        allMessages,
        model,
        (content) => {
          store.addMessage({
            id: `a-${Date.now()}`,
            role: 'assistant',
            content,
            timestamp: Date.now(),
          });
        },
        (step) => store.appendThinkingStep(step),
        (_name) => { /* tool start */ },
        (_name, _result) => { /* tool end */ },
        [],
        0,
        controller.signal
      );

      // If no final assistant message was added (tool loop only), add a summary
      const lastMsg = store.messages[store.messages.length - 1];
      if (!lastMsg || lastMsg.role !== 'assistant') {
        store.addMessage({
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: thinking.length > 0
            ? 'Done. I ran the requested operations. Check the Results tab for backtest output.'
            : 'I processed your request.',
          timestamp: Date.now(),
        });
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        store.addMessage({
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: 'Stopped by user.',
          timestamp: Date.now(),
        });
      } else {
        store.addMessage({
          id: `e-${Date.now()}`,
          role: 'assistant',
          content: `Error: ${err.message || 'Could not reach AI engine.'}`,
          timestamp: Date.now(),
        });
      }
    } finally {
      store.setIsLoading(false);
      abortRef.current = null;
    }
  }, [messages, isLoading, store]);

  const handleStop = () => {
    abortRef.current?.abort();
  };

  const hasMessages = messages.length > 0;

  return (
    <div className="bt-chat-container">
      {/* Session bar */}
      <div className="bt-chat-session-bar">
        <button className="bt-session-toggle" onClick={() => setShowSessions(v => !v)}>
          <Terminal size={14} />
          <span className="bt-session-label">{sessionIndex.find(s => s.id === sessionId)?.label || 'Session'}</span>
          {showSessions ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {/* Model selector — only when no messages (locked per session) */}
          {messages.length === 0 && (
            <button
              className="bt-session-btn"
              onClick={() => setShowModelPicker(v => !v)}
              title={`Model: ${MODELS.find(m => m.id === model)?.name || model}`}
            >
              <Sparkles size={14} />
            </button>
          )}
          <div className="bt-session-actions">
            <button className="bt-session-btn" onClick={() => store.clearChat()} title="Clear chat">
              <Trash2 size={14} />
            </button>
            <button className="bt-session-btn" onClick={() => store.newSession()} title="New session">
              <Plus size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Model picker */}
      {showModelPicker && messages.length === 0 && (
        <div className="bt-session-menu" style={{ padding: 8 }}>
          <div className="bt-section-title" style={{ marginBottom: 8 }}>Select AI Model</div>
          {MODELS.map((m) => (
            <button
              key={m.id}
              className={`bt-session-item ${m.id === model ? 'active' : ''}`}
              onClick={() => { store.setModel(m.id); setShowModelPicker(false); }}
              style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}
            >
              <span style={{ fontWeight: 600 }}>{m.name}</span>
              <span style={{ opacity: 0.6, fontSize: 10 }}>{m.params} params · {m.context} context · {m.desc}</span>
            </button>
          ))}
        </div>
      )}

      {showSessions && (
        <div className="bt-session-menu">
          {sessionIndex.map((s) => (
            <button
              key={s.id}
              className={`bt-session-item ${s.id === sessionId ? 'active' : ''}`}
              onClick={() => { store.switchSession(s.id); setShowSessions(false); }}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      {/* Thinking Pad */}
      {(thinkingSteps.length > 0 || isLoading) && (
        <div className="bt-thinking-pad">
          <button className="bt-thinking-toggle" onClick={() => setShowThinkPad(v => !v)}>
            <BrainCircuit size={14} />
            <span>Thinking Pad ({thinkingSteps.length})</span>
            {showThinkPad ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          {showThinkPad && (
            <div className="bt-thinking-steps">
              {thinkingSteps.map((step, i) => (
                <div key={i} className={`bt-thinking-step ${step.type}`}>
                  <div className="bt-thinking-label">{step.label}</div>
                  <div className="bt-thinking-detail">{step.detail}</div>
                </div>
              ))}
              {/* Loading state while AI is reasoning between tool calls */}
              {isLoading && (
                <div className="bt-thinking-step reasoning">
                  <div className="bt-thinking-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Loader2 size={12} className="animate-spin" />
                    <span>Reasoning...</span>
                  </div>
                  <div className="bt-thinking-detail">AI is processing tool results and planning next steps</div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Messages */}
      <div className="bt-chat-messages" ref={scrollRef}>
        {!hasMessages && (
          <div className="bt-chat-welcome">
            <Bot size={32} style={{ marginBottom: 12, opacity: 0.5 }} />
            <div className="headline-sm">AI Trading Advisor</div>
            <div className="body-md" style={{ opacity: 0.6, marginTop: 8 }}>
              I can probe any symbol, run backtests on any timeframe, and help you decide.<br />
              Default: {configContext.symbol} · {configContext.interval} · {configContext.strategyType}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`bt-chat-bubble ${msg.role}`}>
            <div className="bt-chat-avatar">
              {msg.role === 'assistant' ? <Bot size={16} /> : msg.role === 'tool' ? <Terminal size={16} /> : <User size={16} />}
            </div>
            <div className="bt-chat-content">
              <div className="bt-chat-text">{msg.content}</div>
              {msg.toolCalls && msg.toolCalls.length > 0 && (
                <div className="bt-chat-tools">
                  {msg.toolCalls.map((tc) => (
                    <span key={tc.id} className="bt-chat-tool-badge">
                      <Play size={10} /> {tc.function.name}
                    </span>
                  ))}
                </div>
              )}
              {msg.role === 'assistant' && (
                <button
                  className="bt-chat-copy"
                  onClick={() => handleCopy(msg.content, msg.id)}
                  title="Copy as markdown"
                >
                  {copiedId === msg.id ? <Check size={12} /> : <Copy size={12} />}
                  <span>{copiedId === msg.id ? 'Copied' : 'Copy'}</span>
                </button>
              )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="bt-chat-bubble assistant">
            <div className="bt-chat-avatar"><Bot size={16} /></div>
            <div className="bt-chat-content">
              <div className="bt-chat-thinking">
                <Loader2 size={14} className="animate-spin" />
                <span>AI is analyzing...</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="bt-chat-input-area">
        <div className="bt-chat-quick-actions">
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action}
              className="bt-chip"
              onClick={() => sendMessage(action)}
              disabled={isLoading}
            >
              <Sparkles size={12} />
              {action}
            </button>
          ))}
        </div>
        <div className="bt-chat-input-row">
          <input
            type="text"
            className="bt-chat-input"
            placeholder="Ask the AI about your backtest..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && sendMessage(input)}
            disabled={isLoading}
          />
          {isLoading ? (
            <button className="bt-chat-stop" onClick={handleStop} title="Stop">
              <Square size={16} fill="currentColor" />
            </button>
          ) : (
            <button
              className="bt-chat-send"
              onClick={() => sendMessage(input)}
              disabled={!input.trim()}
            >
              <Send size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── System prompt builder ──────────────────────────────────────── */
function buildSystemContext() {
  const store = useBacktestStore.getState();
  const cfg = store.config;
  const model = store.model;
  const nowIso = new Date().toISOString();
  return `You are VLTHR AI Trader, a quantitative trading advisor with access to a backtest engine.

CURRENT DATE: ${nowIso}

DEFAULT CONFIGURATION (user can override any field by asking):
- Symbol: ${cfg.symbol}
- Interval: ${cfg.interval}
- Date range: ${cfg.startDate} to ${cfg.endDate}
- Strategy archetype: ${cfg.strategyType}
- Parameters: RSI threshold ${cfg.parameters.rsi_threshold}, ADX min ${cfg.parameters.adx_min}, SL multiplier ${cfg.parameters.sl_mult}, TP multiplier ${cfg.parameters.tp_mult}
- Initial capital: $${cfg.parameters.capital}
- Selected pipeline stages: ${cfg.stages.join(', ')}
- AI Model: ${MODELS.find(m => m.id === model)?.name || model}

IMPORTANT: You are NOT limited to the default configuration above. When the user asks about any symbol, timeframe, or strategy type, use the tools with the parameters the user specifies. Do NOT fall back to the default config unless the user does not specify one.

DATA STORE:
All market data is stored as enriched Parquet files with OHLCV + indicators + funding + open interest.
Available symbols (crypto via Bybit): BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT, DOGEUSDT.
Available symbols (metals via Capital.com): XAUUSD.
Each symbol has 5m, 15m, 30m, 1h, 4h, 1d intervals.

MANDATORY WORKFLOW for date-bound tools (get_candles, probe_symbol, backtest, etc.):
1. Call get_data_range(symbol, interval) FIRST to discover the actual available date range in the store.
2. Use dates from that range. Never guess dates. The store only contains recent data (2025-2026), so requests for 2023-2024 will fail.

INDICATORS COMPUTED ON-DEMAND:
- RSI(14) — overbought>70, oversold<30
- ADX(14) — trend strength, >25 is trending
- EMA(8, 21) — short/long trend
- ATR(14) — volatility for stop/target sizing
- Bollinger Bands(20, 2)
- MACD(12, 26, 9)
- VWAP
- Log returns
- Session flags (London 07:00-12:00 UTC, NY Late 20:00-00:00 UTC)

PIPELINE STAGES:
- D1: Data audit — coverage, gaps, quality checks
- D4: Feature discovery — indicator importance, regime detection
- D5A: Mechanical backtest — rule-based entry/exit simulation
- D5B: Walk-forward — out-of-sample validation
- D5C: DQS filter — data quality score gate
- D6: Leverage risk — position sizing, drawdown, ruin prob
- D7: Reality probe — slippage, fees, latency, funding impact

You are an advisor first and a pipeline executor second.

MODE A — CONVERSATION (default): Answer questions, probe symbols, analyze data. Use read-only tools.
MODE B — EXECUTION (explicit request only): Run backtests, create strategies, store decisions.

Never start a backtest from a question. If intent is ambiguous, ask ONE short confirming question.`;
}

