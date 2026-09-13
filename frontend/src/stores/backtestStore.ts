import { create } from 'zustand';
import type { ChatMessage, ThinkStep, BacktestRun, BacktestConfig, Decision, BacktestSessionMeta } from '../types';

const SESSION_INDEX_KEY = 'vlthr_bt_session_index';
const SESSION_PREFIX = 'vlthr_bt_session_';

function generateSessionId(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function generateSessionLabel(): string {
  return new Date().toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
    hour12: true,
  });
}

function getSessionIndex(): BacktestSessionMeta[] {
  try {
    return JSON.parse(localStorage.getItem(SESSION_INDEX_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveSessionIndex(index: BacktestSessionMeta[]) {
  localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(index));
}

function loadSessionData(id: string) {
  try {
    const raw = localStorage.getItem(`${SESSION_PREFIX}${id}`);
    if (!raw) return { messages: [], runs: [], decisions: [] };
    return JSON.parse(raw);
  } catch {
    return { messages: [], runs: [], decisions: [] };
  }
}

function saveSessionData(id: string, data: { messages: ChatMessage[]; runs: BacktestRun[]; decisions: Decision[] }) {
  localStorage.setItem(`${SESSION_PREFIX}${id}`, JSON.stringify(data));
}

const DEFAULT_MODEL = 'meta/llama-3.1-8b-instruct';

function initSession() {
  let id = sessionStorage.getItem('vlthr_bt_current_session');
  const index = getSessionIndex();
  const existing = index.find((s) => s.id === id);
  if (!id || !existing) {
    const meta: BacktestSessionMeta = {
      id: generateSessionId(),
      label: generateSessionLabel(),
      createdAt: Date.now(),
      model: DEFAULT_MODEL,
    };
    index.unshift(meta);
    if (index.length > 50) index.splice(50);
    saveSessionIndex(index);
    id = meta.id;
    sessionStorage.setItem('vlthr_bt_current_session', id);
  }
  const data = loadSessionData(id);
  return { id, model: existing?.model || DEFAULT_MODEL, ...data, sessionIndex: getSessionIndex() };
}

const init = initSession();

interface BacktestStore {
  sessionId: string;
  sessionIndex: BacktestSessionMeta[];
  model: string;
  messages: ChatMessage[];
  runs: BacktestRun[];
  decisions: Decision[];
  activeRunId: string | null;
  thinkingSteps: ThinkStep[];
  isLoading: boolean;
  config: BacktestConfig;

  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  appendThinkingStep: (step: ThinkStep) => void;
  clearThinking: () => void;
  addRun: (run: BacktestRun) => void;
  updateRun: (id: string, updates: Partial<BacktestRun>) => void;
  setActiveRunId: (id: string | null) => void;
  addDecision: (decision: Decision) => void;
  setIsLoading: (loading: boolean) => void;
  setConfig: (cfg: Partial<BacktestConfig>) => void;
  setModel: (model: string) => void;
  clearChat: () => void;
  newSession: () => void;
  switchSession: (id: string) => void;
  deleteRun: (id: string) => void;
}

export const useBacktestStore = create<BacktestStore>((set) => ({
  sessionId: init.id,
  sessionIndex: init.sessionIndex,
  model: init.model,
  messages: init.messages,
  runs: init.runs,
  decisions: init.decisions,
  activeRunId: null,
  thinkingSteps: [],
  isLoading: false,
  config: {
    symbol: 'BTCUSDT',
    interval: '15m',
    startDate: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    endDate: new Date().toISOString().split('T')[0],
    strategyType: 'pullback_to_trend',
    parameters: {
      rsi_threshold: 40,
      adx_min: 25,
      sl_mult: 1.5,
      tp_mult: 2.0,
      capital: 10000,
      leverage: 4,
      risk_per_trade_pct: 2.0,
      session_gating: true,
      direction: 'long',
    },
    stages: ['d1', 'd5a', 'd5b', 'd7'],
  },

  addMessage: (msg) =>
    set((state) => {
      const messages = [...state.messages, msg];
      saveSessionData(state.sessionId, { messages, runs: state.runs, decisions: state.decisions });
      return { messages };
    }),

  updateMessage: (id, updates) =>
    set((state) => {
      const messages = state.messages.map((m) => (m.id === id ? { ...m, ...updates } : m));
      saveSessionData(state.sessionId, { messages, runs: state.runs, decisions: state.decisions });
      return { messages };
    }),

  appendThinkingStep: (step) =>
    set((state) => ({
      thinkingSteps: [...state.thinkingSteps, step],
    })),

  clearThinking: () => set({ thinkingSteps: [] }),

  addRun: (run) =>
    set((state) => {
      const runs = [run, ...state.runs];
      saveSessionData(state.sessionId, { messages: state.messages, runs, decisions: state.decisions });
      return { runs, activeRunId: run.id };
    }),

  updateRun: (id, updates) =>
    set((state) => {
      const runs = state.runs.map((r) => (r.id === id ? { ...r, ...updates } : r));
      saveSessionData(state.sessionId, { messages: state.messages, runs, decisions: state.decisions });
      return { runs };
    }),

  setActiveRunId: (id) => set({ activeRunId: id }),

  addDecision: (decision) =>
    set((state) => {
      const decisions = [...state.decisions, decision];
      saveSessionData(state.sessionId, { messages: state.messages, runs: state.runs, decisions });
      return { decisions };
    }),

  setIsLoading: (loading) => set({ isLoading: loading }),

  setConfig: (cfg) =>
    set((state) => {
      const next = { ...state.config, ...cfg };
      if (cfg.parameters) {
        next.parameters = { ...state.config.parameters, ...cfg.parameters };
      }
      return { config: next };
    }),

  setModel: (model) =>
    set((state) => {
      const idx = state.sessionIndex.map((s) =>
        s.id === state.sessionId ? { ...s, model } : s
      );
      saveSessionIndex(idx);
      return { model, sessionIndex: idx };
    }),

  clearChat: () =>
    set((state) => {
      saveSessionData(state.sessionId, { messages: [], runs: state.runs, decisions: state.decisions });
      return { messages: [], thinkingSteps: [] };
    }),

  newSession: () => {
    const meta: BacktestSessionMeta = {
      id: generateSessionId(),
      label: generateSessionLabel(),
      createdAt: Date.now(),
      model: DEFAULT_MODEL,
    };
    const index = getSessionIndex();
    index.unshift(meta);
    if (index.length > 50) index.splice(50);
    saveSessionIndex(index);
    sessionStorage.setItem('vlthr_bt_current_session', meta.id);
    set({
      sessionId: meta.id,
      sessionIndex: index,
      model: DEFAULT_MODEL,
      messages: [],
      runs: [],
      decisions: [],
      activeRunId: null,
      thinkingSteps: [],
    });
  },

  switchSession: (id) => {
    sessionStorage.setItem('vlthr_bt_current_session', id);
    const data = loadSessionData(id);
    const idx = getSessionIndex();
    const meta = idx.find((s) => s.id === id);
    set({
      sessionId: id,
      model: meta?.model || DEFAULT_MODEL,
      messages: data.messages || [],
      runs: data.runs || [],
      decisions: data.decisions || [],
      activeRunId: null,
      thinkingSteps: [],
      sessionIndex: idx,
    });
  },

  deleteRun: (id) =>
    set((state) => {
      const runs = state.runs.filter((r) => r.id !== id);
      saveSessionData(state.sessionId, { messages: state.messages, runs, decisions: state.decisions });
      return { runs, activeRunId: state.activeRunId === id ? null : state.activeRunId };
    }),
}));
