import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type {
  AssetClass,
  StrategyOption,
  Timeframe,
  TradeHistoryEntry,
  TradeHistorySource,
  TradeHistoryStats,
} from "../../types";
import { formatDateTimeMedium } from "../../lib/datetime";
import { formatPrice } from "../../lib/price";
import { useI18n } from "../../i18n/I18nContext";
import "../stats/stats.css";
import "./trade-history.css";

interface Props {
  onClose: () => void;
}

const PAGE_SIZE = 20;

const BACKTEST_TIMEFRAMES: { key: Timeframe; labelKey: string }[] = [
  { key: "daily", labelKey: "app.timeframe.daily" },
  { key: "half_session", labelKey: "app.timeframe.halfSession" },
  { key: "1h", labelKey: "app.timeframe.1h" },
  { key: "4h", labelKey: "app.timeframe.4h" },
  { key: "1w", labelKey: "app.timeframe.1w" },
];

const SOURCE_KEY: Record<TradeHistoryEntry["source"], string> = {
  live: "tradeHistory.source.live",
  backtest: "tradeHistory.source.backtest",
};

const STATUS_KEY: Record<TradeHistoryEntry["status"], string> = {
  active: "tradeHistory.status.active",
  hit_tp: "tradeHistory.status.hitTp",
  hit_sl: "tradeHistory.status.hitSl",
  expired: "tradeHistory.status.expired",
};

const STATUS_COLOR: Record<TradeHistoryEntry["status"], string> = {
  active: "var(--warn)",
  hit_tp: "var(--bull)",
  hit_sl: "var(--bear)",
  expired: "var(--text-faint)",
};

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

function pnl(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

export function TradeHistoryModal({ onClose }: Props) {
  const { t, language } = useI18n();
  const [items, setItems] = useState<TradeHistoryEntry[] | null>(null);
  const [stats, setStats] = useState<TradeHistoryStats | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [tickerFilter, setTickerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("");
  const [assetClassFilter, setAssetClassFilter] = useState<AssetClass | "">("");
  const [sourceFilter, setSourceFilter] = useState<TradeHistorySource>("live");
  const [directionFilter, setDirectionFilter] = useState<"" | "long" | "short">("");
  const [strategies, setStrategies] = useState<StrategyOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [btTicker, setBtTicker] = useState("");
  const [btTimeframe, setBtTimeframe] = useState<Timeframe>("daily");
  const [btStrategy, setBtStrategy] = useState("");
  const [btRunning, setBtRunning] = useState(false);
  const [btMessage, setBtMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    void api.getStrategies().then(setStrategies);
  }, []);

  useEffect(() => {
    setPage(1);
  }, [tickerFilter, statusFilter, strategyFilter, assetClassFilter, sourceFilter, directionFilter]);

  useEffect(() => {
    setError(null);
    api
      .getTradeHistory(page, PAGE_SIZE, {
        ticker: tickerFilter,
        status: statusFilter,
        strategy: strategyFilter,
        assetClass: assetClassFilter || undefined,
        source: sourceFilter,
        isBullish: directionFilter ? directionFilter === "long" : undefined,
      })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : t("tradeHistory.error")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, tickerFilter, statusFilter, strategyFilter, assetClassFilter, sourceFilter, directionFilter]);

  useEffect(() => {
    api
      .getTradeHistoryStats({
        ticker: tickerFilter,
        strategy: strategyFilter,
        assetClass: assetClassFilter || undefined,
        source: sourceFilter,
        isBullish: directionFilter ? directionFilter === "long" : undefined,
      })
      .then(setStats)
      .catch(() => setStats(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickerFilter, strategyFilter, assetClassFilter, sourceFilter, directionFilter]);

  const handleRunBacktest = () => {
    const ticker = btTicker.trim().toUpperCase();
    if (!ticker) return;
    setBtRunning(true);
    setBtMessage(null);
    api
      .runBacktest(ticker, btTimeframe, btStrategy || undefined)
      .then((res) => {
        setBtMessage(
          res.created > 0
            ? { kind: "success", text: t("tradeHistory.backtest.success", { n: res.created }) }
            : { kind: "error", text: t("tradeHistory.backtest.noneCreated") },
        );
      })
      .catch((e: unknown) =>
        setBtMessage({ kind: "error", text: e instanceof Error ? e.message : t("tradeHistory.backtest.error") }),
      )
      .finally(() => setBtRunning(false));
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>{t("tradeHistory.title")}</h2>
          <button className="settings-modal__close has-tooltip" onClick={onClose} data-tooltip={t("common.close")} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <div className="th-filters" style={{ marginBottom: "var(--space-3)" }}>
            <div className="th-search">
              <span className="th-search__icon has-tooltip" data-tooltip={t("tradeHistory.filter.ticker")}>🔍</span>
              <input
                className="mono"
                placeholder={t("tradeHistory.filter.ticker")}
                value={tickerFilter}
                onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
              />
              {tickerFilter && (
                <button
                  type="button"
                  className="th-search__clear has-tooltip"
                  onClick={() => setTickerFilter("")}
                  data-tooltip={t("tradeHistory.filter.clearTicker")}
                  aria-label={t("tradeHistory.filter.clearTicker")}
                >
                  ×
                </button>
              )}
            </div>
            <div className="th-select">
              <select
                className={statusFilter ? "is-set" : ""}
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">{t("tradeHistory.filter.allStatuses")}</option>
                <option value="active">{t("tradeHistory.status.active")}</option>
                <option value="hit_tp">{t("tradeHistory.status.hitTp")}</option>
                <option value="hit_sl">{t("tradeHistory.status.hitSl")}</option>
                <option value="expired">{t("tradeHistory.status.expired")}</option>
              </select>
            </div>
            <div className="th-select">
              <select
                className={strategyFilter ? "is-set" : ""}
                value={strategyFilter}
                onChange={(e) => setStrategyFilter(e.target.value)}
              >
                <option value="">{t("tradeHistory.filter.allStrategies")}</option>
                {strategies.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="th-select">
              <select
                className={assetClassFilter ? "is-set" : ""}
                value={assetClassFilter}
                onChange={(e) => setAssetClassFilter(e.target.value as AssetClass | "")}
              >
                <option value="">{t("dashboard.filter.all")}</option>
                <option value="stock">{t("dashboard.filter.stock")}</option>
                <option value="crypto">{t("dashboard.filter.crypto")}</option>
              </select>
            </div>
            <div className="th-select">
              <select
                className={sourceFilter !== "live" ? "is-set" : ""}
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value as TradeHistorySource)}
              >
                <option value="live">{t("tradeHistory.filter.sourceLive")}</option>
                <option value="backtest">{t("tradeHistory.filter.sourceBacktest")}</option>
                <option value="all">{t("tradeHistory.filter.sourceAll")}</option>
              </select>
            </div>
            <span className="th-info has-tooltip" data-tooltip={t("tradeHistory.filter.sourceHint")}>ⓘ</span>
            <div className="th-select">
              <select
                className={directionFilter ? "is-set" : ""}
                value={directionFilter}
                onChange={(e) => setDirectionFilter(e.target.value as "" | "long" | "short")}
              >
                <option value="">{t("tradeHistory.filter.directionAll")}</option>
                <option value="long">{t("tradeHistory.filter.directionLong")}</option>
                <option value="short">{t("tradeHistory.filter.directionShort")}</option>
              </select>
            </div>
            <span className="th-info has-tooltip" data-tooltip={t("tradeHistory.filter.directionHint")}>ⓘ</span>
            {(tickerFilter || statusFilter || strategyFilter || assetClassFilter || sourceFilter !== "live" || directionFilter) && (
              <button
                type="button"
                className="th-clear-filters"
                onClick={() => {
                  setTickerFilter("");
                  setStatusFilter("");
                  setStrategyFilter("");
                  setAssetClassFilter("");
                  setSourceFilter("live");
                  setDirectionFilter("");
                }}
              >
                {t("tradeHistory.filter.clear")} ×
              </button>
            )}
          </div>

          <div className="th-backtest">
            <span className="th-backtest__label has-tooltip" data-tooltip={t("tradeHistory.backtest.hint")}>
              {t("tradeHistory.backtest.title")} ⓘ
            </span>
            <input
              className="mono"
              placeholder={t("tradeHistory.backtest.tickerPlaceholder")}
              value={btTicker}
              onChange={(e) => setBtTicker(e.target.value.toUpperCase())}
            />
            <select value={btTimeframe} onChange={(e) => setBtTimeframe(e.target.value as Timeframe)}>
              {BACKTEST_TIMEFRAMES.map((tf) => (
                <option key={tf.key} value={tf.key}>
                  {t(tf.labelKey)}
                </option>
              ))}
            </select>
            <select value={btStrategy} onChange={(e) => setBtStrategy(e.target.value)}>
              <option value="">{t("tradeHistory.backtest.activeStrategy")}</option>
              {strategies.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
            <button className="btn" disabled={!btTicker.trim() || btRunning} onClick={handleRunBacktest}>
              {btRunning ? t("tradeHistory.backtest.running") : t("tradeHistory.backtest.run")}
            </button>
            {btMessage && (
              <span
                className="th-backtest__message"
                style={{ color: btMessage.kind === "success" ? "var(--bull)" : "var(--bear)" }}
              >
                {btMessage.text}
              </span>
            )}
          </div>

          {stats && (
            <div
              className="faint"
              style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", marginBottom: "var(--space-3)" }}
            >
              <span>{t("tradeHistory.stats.total", { count: stats.total_count })}</span>
              <span>{t("tradeHistory.stats.winRate", { rate: pct(stats.win_rate) })}</span>
              <span>{t("tradeHistory.stats.avgPnl", { pnl: pnl(stats.avg_pnl_pct) })}</span>
              <span className="has-tooltip" data-tooltip={t("tradeHistory.stats.excludesExpiredHint")}>ⓘ</span>
              {stats.pnl_sample_count > 0 && (
                <>
                  <span
                    style={{
                      fontWeight: 700,
                      color:
                        stats.expectancy_r === null
                          ? undefined
                          : stats.expectancy_r >= 0
                            ? "var(--bull)"
                            : "var(--bear)",
                    }}
                  >
                    {t("tradeHistory.stats.expectancy", { r: stats.expectancy_r?.toFixed(2) ?? "—" })}
                  </span>
                  <span className="has-tooltip" data-tooltip={t("tradeHistory.stats.expectancyHint")}>ⓘ</span>
                  {stats.median_expectancy_r !== null && (
                    <span className="faint has-tooltip" data-tooltip={t("tradeHistory.stats.medianExpectancyHint")}>
                      {t("tradeHistory.stats.medianExpectancy", { r: stats.median_expectancy_r.toFixed(2) })}
                    </span>
                  )}
                  {stats.low_sample_size && (
                    <span style={{ color: "var(--warn)", fontWeight: 600 }}>
                      {t("tradeHistory.stats.lowSampleSize", { n: stats.pnl_sample_count })}
                    </span>
                  )}
                  {stats.total_pnl_amount !== null && (
                    <span>
                      {t("tradeHistory.stats.totalPnl", {
                        amount: stats.total_pnl_amount.toLocaleString(),
                      })}
                    </span>
                  )}
                  {stats.edge_vs_buy_hold_pct !== null && (
                    <span
                      className="has-tooltip"
                      style={{
                        fontWeight: 700,
                        color: stats.edge_vs_buy_hold_pct >= 0 ? "var(--bull)" : "var(--bear)",
                      }}
                      data-tooltip={t("tradeHistory.stats.buyHoldHint", {
                        pct: pnl(stats.benchmark_buy_hold_pct),
                      })}
                    >
                      {t("tradeHistory.stats.edgeVsBuyHold", { pct: pnl(stats.edge_vs_buy_hold_pct) })}
                    </span>
                  )}
                  {stats.max_drawdown_r !== null && (
                    <span className="has-tooltip" data-tooltip={t("tradeHistory.stats.drawdownHint")}>
                      {t("tradeHistory.stats.maxDrawdown", { r: stats.max_drawdown_r.toFixed(2) })}
                    </span>
                  )}
                  {stats.max_consecutive_losses !== null && stats.max_consecutive_losses > 0 && (
                    <span>
                      {t("tradeHistory.stats.maxConsecutiveLosses", { n: stats.max_consecutive_losses })}
                    </span>
                  )}
                  {stats.monte_carlo !== null && (
                    <span
                      className="has-tooltip"
                      style={{
                        color: stats.monte_carlo.p_value_max_drawdown_r > 0.7 ? "var(--warn)" : undefined,
                      }}
                      data-tooltip={t("tradeHistory.stats.monteCarloHint")}
                    >
                      {t("tradeHistory.stats.monteCarlo", {
                        p: stats.monte_carlo.p_value_max_drawdown_r.toFixed(2),
                      })}
                    </span>
                  )}
                  {stats.bootstrap !== null && (
                    <span
                      className="has-tooltip"
                      style={{
                        fontWeight: 700,
                        color:
                          stats.bootstrap.ci_lower > 0
                            ? "var(--bull)"
                            : stats.bootstrap.ci_upper < 0
                              ? "var(--bear)"
                              : "var(--warn)",
                      }}
                      data-tooltip={t("tradeHistory.stats.bootstrapHint")}
                    >
                      {t("tradeHistory.stats.bootstrap", {
                        lower: stats.bootstrap.ci_lower.toFixed(2),
                        upper: stats.bootstrap.ci_upper.toFixed(2),
                      })}
                    </span>
                  )}
                  {stats.walk_forward !== null && (
                    <span
                      className="has-tooltip"
                      style={{
                        color:
                          stats.walk_forward.consistency_ratio !== null &&
                          stats.walk_forward.consistency_ratio < 0.5
                            ? "var(--warn)"
                            : undefined,
                      }}
                      data-tooltip={t("tradeHistory.stats.walkForwardHint")}
                    >
                      {t("tradeHistory.stats.walkForward", {
                        positive: stats.walk_forward.positive_windows,
                        total: stats.walk_forward.n_windows,
                      })}
                    </span>
                  )}
                  {stats.current_config_count !== null && (
                    <span className="faint">
                      {t("tradeHistory.stats.currentConfig", { n: stats.current_config_count })}
                    </span>
                  )}
                </>
              )}
            </div>
          )}

          {error && <p className="settings-error">{error}</p>}

          {!items && !error && <p className="faint">{t("common.loading")}</p>}
          {items && items.length === 0 && <p className="faint">{t("tradeHistory.empty")}</p>}

          {items && items.length > 0 && (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>{t("tradeHistory.col.ticker")}</th>
                    <th>{t("tradeHistory.col.strategy")}</th>
                    <th>{t("tradeHistory.col.signal")}</th>
                    <th>{t("tradeHistory.col.entry")}</th>
                    <th>{t("tradeHistory.col.sl")}</th>
                    <th>{t("tradeHistory.col.tp")}</th>
                    <th>{t("tradeHistory.col.status")}</th>
                    <th>{t("tradeHistory.col.source")}</th>
                    <th>{t("tradeHistory.col.opened")}</th>
                    <th>{t("tradeHistory.col.closed")}</th>
                    <th>{t("tradeHistory.col.closeReason")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((s) => (
                    <tr key={s.id}>
                      <td className="mono">{s.ticker}</td>
                      <td className="faint">{s.strategy}</td>
                      <td>
                        {s.event_type} {s.is_bullish ? "▲" : "▼"}
                        {s.price_limit_caution && (
                          <span className="has-tooltip" data-tooltip={t("tradeHistory.priceLimitCautionHint")} style={{ marginLeft: 4 }}>
                            ⚠
                          </span>
                        )}
                      </td>
                      <td className="mono">{formatPrice(s.entry)}</td>
                      <td className="mono faint">{formatPrice(s.stop_loss)}</td>
                      <td className="mono faint">{formatPrice(s.take_profit)}</td>
                      <td>
                        <span
                          style={{
                            display: "inline-block",
                            padding: "3px 10px",
                            borderRadius: 999,
                            fontSize: "var(--text-xs)",
                            fontWeight: 700,
                            color: "oklch(18% 0.02 250)",
                            backgroundColor: STATUS_COLOR[s.status],
                          }}
                        >
                          {t(STATUS_KEY[s.status])}
                        </span>
                      </td>
                      <td className="faint">{t(SOURCE_KEY[s.source])}</td>
                      <td className="faint">{formatDateTimeMedium(s.event_ts, language)}</td>
                      <td className="faint">
                        {s.closed_at ? formatDateTimeMedium(s.closed_at, language) : t("common.dash")}
                      </td>
                      <td className="faint">{s.close_reason ?? t("common.dash")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {total > PAGE_SIZE && (
            <div style={{ display: "flex", justifyContent: "center", gap: "var(--space-3)", marginTop: "var(--space-3)" }}>
              <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                {t("logs.pagination.prev")}
              </button>
              <span className="faint mono" style={{ alignSelf: "center" }}>
                {page}/{totalPages}
              </span>
              <button className="btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                {t("logs.pagination.next")}
              </button>
            </div>
          )}
        </div>

        <footer className="settings-modal__footer">
          <button className="btn" onClick={onClose}>
            {t("common.close")}
          </button>
        </footer>
      </div>
    </div>
  );
}
