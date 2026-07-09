import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { Analysis, Candle, SymbolItem, Timeframe } from "./types";
import { Watchlist } from "./components/watchlist/Watchlist";
import { CandleChart } from "./components/chart/CandleChart";
import { AnalysisPanel } from "./components/analysis/AnalysisPanel";

const TIMEFRAMES: { key: Timeframe; label: string }[] = [
  { key: "daily", label: "Ngày" },
  { key: "half_session", label: "Nửa phiên" },
];

export default function App() {
  const [symbols, setSymbols] = useState<SymbolItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("daily");

  const [candles, setCandles] = useState<Candle[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);

  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);

  const loadSymbols = useCallback(async () => {
    try {
      const list = await api.listSymbols();
      setSymbols(list);
      setSelected((prev) => prev ?? list.find((s) => s.is_watchlist)?.ticker ?? list[0]?.ticker ?? null);
    } catch (e) {
      setDataError(e instanceof Error ? e.message : "Không tải được danh sách mã");
    }
  }, []);

  useEffect(() => {
    void loadSymbols();
  }, [loadSymbols]);

  const loadData = useCallback(async (ticker: string, tf: Timeframe) => {
    setDataError(null);
    try {
      const [candleData, analysisData] = await Promise.all([
        api.getCandles(ticker, tf),
        api.getAnalysis(ticker, tf).catch(() => null), // 404 before first refresh is fine
      ]);
      setCandles(candleData);
      setAnalysis(analysisData);
    } catch (e) {
      setCandles([]);
      setAnalysis(null);
      setDataError(e instanceof Error ? e.message : "Không tải được dữ liệu");
    }
  }, []);

  useEffect(() => {
    if (selected) void loadData(selected, timeframe);
  }, [selected, timeframe, loadData]);

  const handleRefresh = useCallback(async () => {
    if (!selected) return;
    setRefreshing(true);
    setDataError(null);
    try {
      const result = await api.refresh(selected, timeframe);
      setAnalysis(result);
      setCandles(await api.getCandles(selected, timeframe));
      await loadSymbols();
    } catch (e) {
      setDataError(e instanceof Error ? e.message : "Cập nhật thất bại");
    } finally {
      setRefreshing(false);
    }
  }, [selected, timeframe, loadSymbols]);

  const withBusy = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }, []);

  const handleAdd = (ticker: string) =>
    void withBusy(async () => {
      await api.addSymbol(ticker);
      await loadSymbols();
      setSelected(ticker);
    });

  const handleRemove = (ticker: string) =>
    void withBusy(async () => {
      await api.removeSymbol(ticker);
      if (selected === ticker) setSelected(null);
      await loadSymbols();
    });

  const handleSeed = () => void withBusy(() => api.seedVn30().then(loadSymbols));

  const hasData = candles.length > 0;

  return (
    <div className="app">
      <header className="app__header">
        <div className="brand">
          <span className="brand__mark">
            Chart<span className="brand__accent">Volume</span>
          </span>
          <span className="brand__tag">Wyckoff · VN30</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)" }}>
          {selected && <span className="mono" style={{ fontWeight: 600 }}>{selected}</span>}
          <div className="tf-toggle">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf.key}
                className={timeframe === tf.key ? "is-active" : ""}
                onClick={() => setTimeframe(tf.key)}
              >
                {tf.label}
              </button>
            ))}
          </div>
          <button
            className="btn btn--primary"
            onClick={handleRefresh}
            disabled={!selected || refreshing}
          >
            {refreshing ? "Đang cập nhật…" : "Cập nhật"}
          </button>
        </div>
      </header>

      <div className="app__body">
        <aside className="panel panel--sidebar">
          <Watchlist
            symbols={symbols}
            selected={selected}
            onSelect={setSelected}
            onAdd={handleAdd}
            onRemove={handleRemove}
            onSeedVn30={handleSeed}
            busy={busy}
          />
        </aside>

        <main className="panel panel--main">
          {hasData ? (
            <CandleChart candles={candles} analysis={analysis} />
          ) : (
            <div
              className="faint"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                textAlign: "center",
                padding: "var(--space-6)",
              }}
            >
              {dataError
                ? dataError
                : selected
                  ? "Chưa có dữ liệu cho mã này. Bấm “Cập nhật” để tải và phân tích."
                  : "Chọn một mã ở danh sách bên trái."}
            </div>
          )}
        </main>

        <aside className="panel panel--aside analysis-panel-wrap">
          <AnalysisPanel analysis={analysis} loading={refreshing && !analysis} error={null} />
        </aside>
      </div>
    </div>
  );
}
