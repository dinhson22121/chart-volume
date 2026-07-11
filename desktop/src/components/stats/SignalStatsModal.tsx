import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { SignalStat } from "../../types";
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

export function SignalStatsModal({ onClose }: Props) {
  const { t, language } = useI18n();
  const [stats, setStats] = useState<SignalStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSignalStats().then(setStats).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : t("stats.error"));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          <button className="settings-modal__close" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint stats-hint">{t("stats.hint")}</p>

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
                    <th>{t("stats.table.win10")}</th>
                    <th>{t("stats.table.win20")}</th>
                    <th>{t("stats.table.avgReturn10")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.map((s) => (
                    <tr key={s.type}>
                      <td>
                        <span className={`stats-dot ${s.is_bullish ? "stats-dot--bull" : "stats-dot--bear"}`} />
                        {signalLabel(s.type, language)}
                      </td>
                      <td className="mono">{s.count}</td>
                      <td className="mono">{pct(s.win_rate_5)}</td>
                      <td className="mono">{pct(s.win_rate_10)}</td>
                      <td className="mono">{pct(s.win_rate_20)}</td>
                      <td className="mono">{ret(s.avg_return_10)}</td>
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
