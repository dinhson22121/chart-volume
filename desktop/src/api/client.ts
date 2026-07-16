import type {
  Analysis,
  AssetClass,
  BarTrace,
  CandidatesPage,
  CandidateSort,
  Candle,
  DashboardRow,
  IndicatorSeries,
  ConfigLogPage,
  OllamaPullEvent,
  OllamaStatus,
  PotentialScreenRow,
  PotentialScreenStatus,
  ScanStatus,
  SeedVn30Result,
  Settings,
  SettingsUpdate,
  SignalStat,
  StrategyOption,
  SymbolItem,
  SystemLogPage,
  Timeframe,
} from "../types";

// In Electron the preload injects apiBase + token; in a plain browser dev
// session fall back to Vite env vars.
const bridge = window.chartVolume;
const API_BASE =
  bridge?.apiBase ?? (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://127.0.0.1:8787";
const TOKEN = bridge?.token ?? (import.meta.env.VITE_API_TOKEN as string | undefined) ?? "";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
      ...(opts.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  listSymbols: () => req<SymbolItem[]>("/symbols"),
  addSymbol: (ticker: string, assetClass: AssetClass = "stock") =>
    req<SymbolItem>("/symbols", {
      method: "POST",
      body: JSON.stringify({ ticker, asset_class: assetClass }),
    }),
  removeSymbol: (ticker: string) => req<unknown>(`/symbols/${ticker}`, { method: "DELETE" }),
  seedVn30: () => req<SeedVn30Result>("/symbols/seed-vn30", { method: "POST" }),
  seedTop100: () => req<{ count: number }>("/symbols/seed-top100", { method: "POST" }),
  getCandles: (ticker: string, timeframe: Timeframe) =>
    req<Candle[]>(`/candles/${ticker}?timeframe=${timeframe}`),
  getAnalysis: (ticker: string, timeframe: Timeframe) =>
    req<Analysis>(`/analysis/${ticker}?timeframe=${timeframe}`),
  refresh: (ticker: string, timeframe: Timeframe, force = false) =>
    req<Analysis>(`/analysis/${ticker}/refresh?timeframe=${timeframe}&force=${force}`, {
      method: "POST",
    }),
  getSettings: () => req<Settings>("/settings"),
  getStrategies: () => req<StrategyOption[]>("/strategies"),
  updateSettings: (partial: SettingsUpdate) =>
    req<Settings>("/settings", { method: "PUT", body: JSON.stringify(partial) }),
  getTrace: (ticker: string, timeframe: Timeframe, barTs: string) =>
    req<BarTrace>(`/analysis/${ticker}/trace?timeframe=${timeframe}&bar_ts=${encodeURIComponent(barTs)}`),
  getIndicators: (ticker: string, timeframe: Timeframe) =>
    req<IndicatorSeries>(`/analysis/${ticker}/indicators?timeframe=${timeframe}`),
  getDashboard: () => req<DashboardRow[]>("/analysis/dashboard"),
  getSignalStats: (ticker?: string, timeframe?: Timeframe, alignedOnly?: boolean) => {
    const params = new URLSearchParams();
    if (ticker) params.set("ticker", ticker);
    if (timeframe) params.set("timeframe", timeframe);
    if (alignedOnly) params.set("aligned_only", "true");
    const qs = params.toString();
    return req<SignalStat[]>(`/signals/stats${qs ? `?${qs}` : ""}`);
  },
  getOllamaStatus: () => req<OllamaStatus>("/ollama/status"),
  pullOllamaModel: async (model: string, onProgress: (event: OllamaPullEvent) => void): Promise<void> => {
    const res = await fetch(`${API_BASE}/ollama/pull`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ model }),
    });
    if (!res.ok || !res.body) {
      const body = (await res.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(body?.detail ?? `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          onProgress(JSON.parse(line) as OllamaPullEvent);
        } catch {
          // ignore a malformed progress line rather than aborting the pull
        }
      }
    }
  },
  triggerScreenerScan: () => req<{ status: string }>("/crypto/screener/scan", { method: "POST" }),
  cancelScreenerScan: () => req<ScanStatus>("/crypto/screener/cancel", { method: "POST" }),
  getScreenerStatus: () => req<ScanStatus>("/crypto/screener/status"),
  getScreenerCandidates: (
    sort: CandidateSort, page: number, pageSize: number, q?: string, exchange?: string,
  ) => {
    const params = new URLSearchParams({ sort, page: String(page), page_size: String(pageSize) });
    if (q) params.set("q", q);
    if (exchange) params.set("exchange", exchange);
    return req<CandidatesPage>(`/crypto/screener/candidates?${params.toString()}`);
  },
  promoteCandidate: (coinId: string) =>
    req<{ ticker: string; asset_class: string }>(`/crypto/screener/candidates/${coinId}/promote`, {
      method: "POST",
    }),
  getConfigLogs: (page: number, pageSize: number) =>
    req<ConfigLogPage>(`/logs/config?page=${page}&page_size=${pageSize}`),
  getSystemLogs: (page: number, pageSize: number) =>
    req<SystemLogPage>(`/logs/system?page=${page}&page_size=${pageSize}`),
  exportLogs: () => req<{ content: string; generated_at: string }>("/logs/export"),
  runPotentialScreen: () => req<{ status: string }>("/potential-screen/run", { method: "POST" }),
  getPotentialScreenStatus: () => req<PotentialScreenStatus>("/potential-screen/status"),
  getPotentialScreenResults: () => req<PotentialScreenRow[]>("/potential-screen/results"),
};

export { API_BASE };
