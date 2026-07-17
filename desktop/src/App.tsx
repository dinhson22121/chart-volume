import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api/client";
import type { Analysis, Candle, StrategyOption, SymbolItem, Timeframe } from "./types";
import { Watchlist, type WatchlistTab } from "./components/watchlist/Watchlist";
import { CandleChart } from "./components/chart/CandleChart";
import { AnalysisPanel } from "./components/analysis/AnalysisPanel";
import { SettingsModal } from "./components/settings/SettingsModal";
import { TracePanel } from "./components/trace/TracePanel";
import { SignalStatsModal } from "./components/stats/SignalStatsModal";
import { DashboardModal } from "./components/dashboard/DashboardModal";
import { ActivityLogModal } from "./components/logs/ActivityLogModal";
import { PotentialScreenModal } from "./components/potential/PotentialScreenModal";
import { TradeHistoryModal } from "./components/trade-history/TradeHistoryModal";
import { useI18n } from "./i18n/I18nContext";
import logoIcon from "./assets/logo-icon.png";

const STOCK_TIMEFRAME_KEYS: { key: Timeframe; labelKey: string }[] = [
  { key: "daily", labelKey: "app.timeframe.daily" },
  { key: "half_session", labelKey: "app.timeframe.halfSession" },
  { key: "1w", labelKey: "app.timeframe.1w" },
];

const CRYPTO_TIMEFRAME_KEYS: { key: Timeframe; labelKey: string }[] = [
  { key: "1h", labelKey: "app.timeframe.1h" },
  { key: "4h", labelKey: "app.timeframe.4h" },
  { key: "daily", labelKey: "app.timeframe.dailyCrypto" },
  { key: "1w", labelKey: "app.timeframe.1w" },
];

interface Props {
  onLicenseCleared: () => void;
}

export default function App({ onLicenseCleared }: Props) {
  const { t } = useI18n();
  const [symbols, setSymbols] = useState<SymbolItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("daily");

  const [candles, setCandles] = useState<Candle[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);

  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [statsOpen, setStatsOpen] = useState(false);
  const [dashboardOpen, setDashboardOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [tradeHistoryOpen, setTradeHistoryOpen] = useState(false);
  const [potentialScreenOpen, setPotentialScreenOpen] = useState(false);
  const [traceBarTs, setTraceBarTs] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<WatchlistTab>("vn30");
  const [strategies, setStrategies] = useState<StrategyOption[]>([]);
  const [strategy, setStrategy] = useState<string>("");

  const selectedSymbol = useMemo(
    () => symbols.find((s) => s.ticker === selected) ?? null,
    [symbols, selected],
  );
  // Falls back to the active sidebar tab when nothing (matching) is selected
  // yet, so the timeframe toggle previews the right options as soon as you
  // switch tabs, not just after clicking a specific ticker.
  const timeframeAssetClass =
    selectedSymbol?.asset_class ?? (sidebarTab === "vn30" ? "stock" : "crypto");
  const availableTimeframes = useMemo(
    () =>
      (timeframeAssetClass === "crypto" ? CRYPTO_TIMEFRAME_KEYS : STOCK_TIMEFRAME_KEYS).map((tf) => ({
        key: tf.key,
        label: t(tf.labelKey),
      })),
    [timeframeAssetClass, t],
  );

  const loadSymbols = useCallback(async () => {
    try {
      const list = await api.listSymbols();
      setSymbols(list);
      setSelected((prev) => prev ?? list.find((s) => s.is_watchlist)?.ticker ?? list[0]?.ticker ?? null);
    } catch (e) {
      setDataError(e instanceof Error ? e.message : t("app.error.loadSymbols"));
    }
  }, [t]);

  useEffect(() => {
    void loadSymbols();
  }, [loadSymbols]);

  useEffect(() => {
    void api.getStrategies().then(setStrategies);
    void api.getSettings().then((s) => setStrategy(s.strategy));
  }, []);

  // Moved here (next to the analyze button) rather than living only inside
  // Settings, so switching strategy and re-analyzing is a single quick flow
  // instead of a detour through the Settings modal. Saves immediately
  // (unlike the rest of Settings' fields, which batch behind their own Save
  // button) since there's no surrounding form here to batch it with.
  const handleStrategyChange = async (value: string) => {
    const previous = strategy;
    setStrategy(value);
    try {
      await api.updateSettings({ strategy: value });
    } catch (e) {
      setStrategy(previous);
      setDataError(e instanceof Error ? e.message : t("app.error.strategyChange"));
    }
  };

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
      setDataError(e instanceof Error ? e.message : t("app.error.loadData"));
    }
  }, [t]);

  useEffect(() => {
    // Switching to a symbol whose asset class doesn't support the current
    // timeframe (e.g. a stock's "half_session" while viewing a crypto ticker)
    // snaps to that asset class's first valid option instead of erroring.
    if (!availableTimeframes.some((tf) => tf.key === timeframe)) {
      setTimeframe(availableTimeframes[0].key);
    }
  }, [availableTimeframes, timeframe]);

  useEffect(() => {
    if (selected) void loadData(selected, timeframe);
    setTraceBarTs(null); // switching ticker/timeframe invalidates any open trace popup
  }, [selected, timeframe, loadData]);

  const handleRefresh = useCallback(async () => {
    if (!selected) return;
    setRefreshing(true);
    setDataError(null);
    try {
      // Refresh every timeframe for this asset class, not just the one being
      // viewed, so switching timeframes afterward already has fresh data.
      // Sequential + per-timeframe try/catch mirrors the backend's own batch
      // job isolation (one bad timeframe shouldn't block the others).
      const failedLabels: string[] = [];
      for (const tf of availableTimeframes) {
        try {
          await api.refresh(selected, tf.key);
        } catch {
          failedLabels.push(tf.label);
        }
      }

      setAnalysis(await api.getAnalysis(selected, timeframe).catch(() => null));
      setCandles(await api.getCandles(selected, timeframe));
      await loadSymbols();

      if (failedLabels.length > 0) {
        setDataError(t("app.error.analysisFailedTimeframes", { timeframes: failedLabels.join(", ") }));
      }
    } catch (e) {
      setDataError(e instanceof Error ? e.message : t("app.error.analysisFailed"));
    } finally {
      setRefreshing(false);
    }
  }, [selected, timeframe, availableTimeframes, loadSymbols, t]);

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

  const handleCryptoPromoted = (ticker: string) =>
    void withBusy(async () => {
      await loadSymbols();
      setSelected(ticker);
    });

  const handleTabChange = (tab: WatchlistTab) => {
    setSidebarTab(tab);
    // Switching tabs jumps to a matching ticker so the chart/timeframe
    // toggle stay in sync with what's shown in the sidebar, instead of
    // leaving a stock selected while browsing the crypto tab (or vice versa).
    const match =
      tab === "vn30"
        ? symbols.filter((s) => s.is_vn30).sort((a, b) => a.ticker.localeCompare(b.ticker))[0]
        : tab === "top100"
          ? symbols
              .filter((s) => s.is_top100)
              .sort((a, b) => (a.top100_rank ?? Infinity) - (b.top100_rank ?? Infinity))[0]
          : symbols
              .filter((s) => s.asset_class === "crypto" && s.is_watchlist)
              .sort((a, b) => a.ticker.localeCompare(b.ticker))[0];
    setSelected(match?.ticker ?? null);
  };

  const hasData = candles.length > 0;

  return (
    <div className="app">
      <header className="app__header">
        <div className="brand">
          <img src={logoIcon} alt="" className="brand__icon" />
          <span className="brand__mark">
            Chart<span className="brand__accent">Volume</span>
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)" }}>
          {selected && (
            <span className="mono" style={{ fontWeight: 600 }}>
              {selectedSymbol?.display_symbol ?? selected}
            </span>
          )}
          <div className="tf-toggle">
            {availableTimeframes.map((tf) => (
              <button
                key={tf.key}
                className={timeframe === tf.key ? "is-active" : ""}
                onClick={() => setTimeframe(tf.key)}
              >
                {tf.label}
              </button>
            ))}
          </div>
          <select
            className="strategy-select"
            value={strategy}
            onChange={(e) => void handleStrategyChange(e.target.value)}
            disabled={!strategies.length}
            title={t("settings.strategy.hint")}
          >
            {strategies.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
          <button
            className="btn btn--primary"
            onClick={handleRefresh}
            disabled={!selected || refreshing}
          >
            {refreshing ? t("app.refresh.analyzing") : t("app.refresh.analyze")}
          </button>
          <button
            className="btn btn--icon"
            onClick={() => setDashboardOpen(true)}
            aria-label={t("app.header.dashboard")}
            title={t("app.header.dashboard")}
          >
            🗂️
          </button>
          <button
            className="btn btn--icon"
            onClick={() => setStatsOpen(true)}
            aria-label={t("app.header.stats")}
            title={t("app.header.stats")}
          >
            📊
          </button>
          <button
            className="btn btn--icon"
            onClick={() => setLogsOpen(true)}
            aria-label={t("app.header.logs")}
            title={t("app.header.logs")}
          >
            📜
          </button>
          <button
            className="btn btn--icon"
            onClick={() => setTradeHistoryOpen(true)}
            aria-label={t("app.header.tradeHistory")}
            title={t("app.header.tradeHistory")}
          >
            🧾
          </button>
          <button
            className="btn btn--icon"
            onClick={() => setPotentialScreenOpen(true)}
            aria-label={t("app.header.potentialScreen")}
            title={t("app.header.potentialScreen")}
          >
            🔮
          </button>
          <button
            className="btn btn--icon"
            onClick={() => setSettingsOpen(true)}
            aria-label={t("app.header.settings")}
            title={t("app.header.settings")}
          >
            ⚙
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
            onSeeded={loadSymbols}
            onCryptoPromoted={handleCryptoPromoted}
            activeTab={sidebarTab}
            onTabChange={handleTabChange}
            busy={busy}
          />
        </aside>

        <main className="panel panel--main">
          {hasData ? (
            <CandleChart candles={candles} analysis={analysis} onBarClick={setTraceBarTs} />
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
                  ? t("app.empty.noData")
                  : t("app.empty.selectTicker")}
            </div>
          )}
        </main>

        <aside className="panel panel--aside analysis-panel-wrap">
          <AnalysisPanel analysis={analysis} loading={refreshing && !analysis} error={null} />
        </aside>
      </div>

      {settingsOpen && (
        <SettingsModal strategy={strategy} onClose={() => setSettingsOpen(false)} onLicenseCleared={onLicenseCleared} />
      )}
      {statsOpen && <SignalStatsModal onClose={() => setStatsOpen(false)} />}
      {dashboardOpen && (
        <DashboardModal onClose={() => setDashboardOpen(false)} onSelect={setSelected} />
      )}
      {logsOpen && <ActivityLogModal onClose={() => setLogsOpen(false)} />}
      {tradeHistoryOpen && <TradeHistoryModal onClose={() => setTradeHistoryOpen(false)} />}
      {potentialScreenOpen && (
        <PotentialScreenModal onClose={() => setPotentialScreenOpen(false)} onSelect={setSelected} />
      )}
      {traceBarTs && selected && (
        <TracePanel
          ticker={selected}
          displaySymbol={selectedSymbol?.display_symbol ?? selected}
          timeframe={timeframe}
          barTs={traceBarTs}
          onClose={() => setTraceBarTs(null)}
        />
      )}
    </div>
  );
}
