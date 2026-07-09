import type { Analysis, Candle, SymbolItem, Timeframe } from "../types";

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
  addSymbol: (ticker: string) =>
    req<SymbolItem>("/symbols", { method: "POST", body: JSON.stringify({ ticker }) }),
  removeSymbol: (ticker: string) => req<unknown>(`/symbols/${ticker}`, { method: "DELETE" }),
  seedVn30: () => req<{ count: number }>("/symbols/seed-vn30", { method: "POST" }),
  getCandles: (ticker: string, timeframe: Timeframe) =>
    req<Candle[]>(`/candles/${ticker}?timeframe=${timeframe}`),
  getAnalysis: (ticker: string, timeframe: Timeframe) =>
    req<Analysis>(`/analysis/${ticker}?timeframe=${timeframe}`),
  refresh: (ticker: string, timeframe: Timeframe, force = false) =>
    req<Analysis>(`/analysis/${ticker}/refresh?timeframe=${timeframe}&force=${force}`, {
      method: "POST",
    }),
};

export { API_BASE };
