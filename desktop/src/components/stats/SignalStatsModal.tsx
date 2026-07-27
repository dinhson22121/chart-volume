import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AssetClass, SignalStat } from "../../types";
import { signalLabel } from "../../lib/wyckoff";
import { useI18n } from "../../i18n/I18nContext";
import "./stats.css";

interface Props {
  onClose: () => void;
}

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

function ret(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

function edgePct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${Math.round(v * 100)}pp`;
}

export function SignalStatsModal({ onClose }: Props) {
  const { t, language } = useI18n();
  const [stats, setStats] = useState<SignalStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [alignedOnly, setAlignedOnly] = useState(false);
  const [assetClass, setAssetClass] = useState<AssetClass | "">("");

  useEffect(() => {
    setStats(null);
    setError(null);
    api
      .getSignalStats(undefined, undefined, alignedOnly, assetClass || undefined)
      .then(setStats)
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : t("stats.error"));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alignedOnly, assetClass]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>{t("stats.title")}</h2>
          <button className="settings-modal__close has-tooltip" onClick={onClose} data-tooltip={t("common.close")} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint stats-hint">{t("stats.hint")}</p>
          <p className="faint stats-hint">{t("stats.significanceHint")}</p>

          <label
            className="settings-field--row"
            style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}
          >
            <input
              type="checkbox"
              checked={alignedOnly}
              onChange={(e) => setAlignedOnly(e.target.checked)}
            />
            <span className="faint">{t("stats.alignedOnly")}</span>
          </label>

          <label
            className="settings-field--row"
            style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-3)", alignItems: "center" }}
          >
            <span className="faint">{t("stats.filter.assetClass")}:</span>
            <select value={assetClass} onChange={(e) => setAssetClass(e.target.value as AssetClass | "")}>
              <option value="">{t("dashboard.filter.all")}</option>
              <option value="stock">{t("dashboard.filter.stock")}</option>
              <option value="crypto">{t("dashboard.filter.crypto")}</option>
            </select>
          </label>

          {error && <p className="settings-error">{error}</p>}
          {!stats && !error && <p className="faint">{t("common.loading")}</p>}
          {stats && stats.length === 0 && (
            <p className="faint">{t("stats.empty")}</p>
          )}

          {stats && stats.length > 0 && (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>{t("stats.table.signal")}</th>
                    <th>{t("stats.table.count")}</th>
                    <th>{t("stats.table.win5")}</th>
                    <th className="has-tooltip" data-tooltip={t("stats.table.win10")}>{`${t("stats.table.win10")} (CI 95%)`}</th>
                    <th>{t("stats.table.win20")}</th>
                    <th className="has-tooltip" data-tooltip={t("stats.edgeHint")}>{t("stats.table.edge10")}</th>
                    <th className="has-tooltip" data-tooltip={t("stats.expectancyHint")}>{t("stats.table.expectancy10")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.map((s) => (
                    <tr key={s.type}>
                      <td>
                        <span className={`stats-dot ${s.is_bullish ? "stats-dot--bull" : "stats-dot--bear"}`} />
                        {signalLabel(s.type, language)}
                        {s.significant_10 === true && (
                          <span className="has-tooltip" data-tooltip={t("stats.significantHint")} style={{ marginLeft: 4 }}>
                            ✓
                          </span>
                        )}
                      </td>
                      <td className="mono">
                        {s.count}
                        {s.n_current_config !== undefined && (
                          <span className="faint" style={{ marginLeft: 4, fontSize: "0.85em" }}>
                            ({t("stats.table.currentConfig", { n: s.n_current_config })})
                          </span>
                        )}
                      </td>
                      <td className="mono">{pct(s.win_rate_5)}</td>
                      <td className="mono">
                        {pct(s.win_rate_10)}
                        {s.win_rate_10_ci && (
                          <span className="faint" style={{ marginLeft: 4, fontSize: "0.85em" }}>
                            ({Math.round(s.win_rate_10_ci[0] * 100)}–{Math.round(s.win_rate_10_ci[1] * 100)}%)
                          </span>
                        )}
                      </td>
                      <td className="mono">{pct(s.win_rate_20)}</td>
                      <td
                        className="mono"
                        style={{
                          fontWeight: 700,
                          color:
                            s.edge_10 === null ? undefined : s.edge_10 >= 0 ? "var(--bull)" : "var(--bear)",
                        }}
                      >
                        {edgePct(s.edge_10)}
                      </td>
                      <td
                        className="mono"
                        style={{
                          fontWeight: 700,
                          color:
                            s.avg_return_10 === null
                              ? undefined
                              : s.avg_return_10 >= 0
                                ? "var(--bull)"
                                : "var(--bear)",
                        }}
                      >
                        {ret(s.avg_return_10)}
                      </td>
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
