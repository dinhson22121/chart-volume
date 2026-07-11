import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { DashboardRow } from "../../types";
import { phaseColor, phaseLabel, signalLabel } from "../../lib/wyckoff";
import { formatDateTime } from "../../lib/datetime";
import { useI18n } from "../../i18n/I18nContext";
import "../stats/stats.css";

interface Props {
  onClose: () => void;
  onSelect: (ticker: string) => void;
}

type Filter = "all" | "stock" | "crypto";

export function DashboardModal({ onClose, onSelect }: Props) {
  const { t, language } = useI18n();
  const [rows, setRows] = useState<DashboardRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  const FILTER_OPTIONS: { value: Filter; label: string }[] = [
    { value: "all", label: t("dashboard.filter.all") },
    { value: "stock", label: t("dashboard.filter.stock") },
    { value: "crypto", label: t("dashboard.filter.crypto") },
  ];

  useEffect(() => {
    api.getDashboard().then(setRows).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : t("dashboard.error"));
    });
    // Only on mount -- the error fallback string is stable enough across
    // language switches for the rare case where the initial load failed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filtered = useMemo(() => {
    if (!rows) return null;
    if (filter === "all") return rows;
    return rows.filter((r) => r.asset_class === filter);
  }, [rows, filter]);

  const handleRowClick = (ticker: string) => {
    onSelect(ticker);
    onClose();
  };

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>{t("dashboard.title")}</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint stats-hint">{t("dashboard.hint")}</p>

          <div className="wl-tabs" style={{ marginBottom: "var(--space-3)", maxWidth: 280 }}>
            {FILTER_OPTIONS.map((o) => (
              <button
                key={o.value}
                className={filter === o.value ? "is-active" : ""}
                onClick={() => setFilter(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>

          {error && <p className="settings-error">{error}</p>}
          {!rows && !error && <p className="faint">{t("common.loading")}</p>}
          {filtered && filtered.length === 0 && (
            <p className="faint">{t("dashboard.empty")}</p>
          )}

          {filtered && filtered.length > 0 && (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>{t("dashboard.table.ticker")}</th>
                    <th>{t("dashboard.table.phase")}</th>
                    <th>{t("dashboard.table.confidence")}</th>
                    <th>{t("dashboard.table.latestSignal")}</th>
                    <th>{t("dashboard.table.updatedAt")}</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <tr
                      key={r.ticker}
                      onClick={() => handleRowClick(r.ticker)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>
                          {r.display_symbol}
                        </span>{" "}
                        {r.asset_class === "crypto" && <span title="Crypto">🪙</span>}
                        {r.name && <span className="faint"> {r.name}</span>}
                      </td>
                      {r.has_data ? (
                        <>
                          <td>
                            <span
                              style={{
                                display: "inline-block",
                                padding: "3px 10px",
                                borderRadius: 999,
                                fontSize: "var(--text-xs)",
                                fontWeight: 700,
                                color: "oklch(18% 0.02 250)",
                                backgroundColor: phaseColor(r.phase ?? ""),
                              }}
                            >
                              {phaseLabel(r.phase ?? "", language)}
                            </span>
                          </td>
                          <td className="mono">
                            {r.confidence !== null ? `${Math.round(r.confidence * 100)}%` : t("common.dash")}
                          </td>
                          <td>{r.latest_signal ? signalLabel(r.latest_signal.type, language) : t("common.dash")}</td>
                          <td className="faint">{r.as_of ? formatDateTime(r.as_of, language) : t("common.dash")}</td>
                        </>
                      ) : (
                        <td colSpan={4} className="faint">
                          {t("dashboard.noAnalysis")}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
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
