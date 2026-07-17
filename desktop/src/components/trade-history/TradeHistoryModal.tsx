import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AssetClass, StrategyOption, TradeHistoryEntry, TradeHistoryStats } from "../../types";
import { formatDateTimeMedium } from "../../lib/datetime";
import { formatPrice } from "../../lib/price";
import { useI18n } from "../../i18n/I18nContext";
import "../stats/stats.css";
import "./trade-history.css";

interface Props {
  onClose: () => void;
}

const PAGE_SIZE = 20;

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
  const [strategies, setStrategies] = useState<StrategyOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.getStrategies().then(setStrategies);
  }, []);

  useEffect(() => {
    setPage(1);
  }, [tickerFilter, statusFilter, strategyFilter, assetClassFilter]);

  useEffect(() => {
    setError(null);
    api
      .getTradeHistory(page, PAGE_SIZE, {
        ticker: tickerFilter,
        status: statusFilter,
        strategy: strategyFilter,
        assetClass: assetClassFilter || undefined,
      })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : t("tradeHistory.error")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, tickerFilter, statusFilter, strategyFilter, assetClassFilter]);

  useEffect(() => {
    api
      .getTradeHistoryStats({ ticker: tickerFilter, strategy: strategyFilter, assetClass: assetClassFilter || undefined })
      .then(setStats)
      .catch(() => setStats(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickerFilter, strategyFilter, assetClassFilter]);

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
          <button className="settings-modal__close" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <div className="th-filters" style={{ marginBottom: "var(--space-3)" }}>
            <div className="th-search">
              <span className="th-search__icon">🔍</span>
              <input
                className="mono"
                placeholder={t("tradeHistory.filter.ticker")}
                value={tickerFilter}
                onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
              />
              {tickerFilter && (
                <button
                  type="button"
                  className="th-search__clear"
                  onClick={() => setTickerFilter("")}
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
            {(tickerFilter || statusFilter || strategyFilter || assetClassFilter) && (
              <button
                type="button"
                className="th-clear-filters"
                onClick={() => {
                  setTickerFilter("");
                  setStatusFilter("");
                  setStrategyFilter("");
                  setAssetClassFilter("");
                }}
              >
                {t("tradeHistory.filter.clear")} ×
              </button>
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
              <span title={t("tradeHistory.stats.excludesExpiredHint")}>ⓘ</span>
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
                  <span title={t("tradeHistory.stats.expectancyHint")}>ⓘ</span>
                  {stats.total_pnl_amount !== null && (
                    <span>
                      {t("tradeHistory.stats.totalPnl", {
                        amount: stats.total_pnl_amount.toLocaleString(),
                      })}
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
