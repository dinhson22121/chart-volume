import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { PotentialScreenRow, PotentialScreenStatus } from "../../types";
import { formatDateTime } from "../../lib/datetime";
import { useI18n } from "../../i18n/I18nContext";
import "../stats/stats.css";

interface Props {
  onClose: () => void;
  onSelect: (ticker: string) => void;
}

const POLL_INTERVAL_MS = 2000;

export function PotentialScreenModal({ onClose, onSelect }: Props) {
  const { t, language } = useI18n();
  const [rows, setRows] = useState<PotentialScreenRow[] | null>(null);
  const [status, setStatus] = useState<PotentialScreenStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadResults = () => {
    api.getPotentialScreenResults().then(setRows).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : t("potential.error"));
    });
  };

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPolling = () => {
    if (!pollRef.current) {
      pollRef.current = setInterval(pollStatus, POLL_INTERVAL_MS);
    }
  };

  const pollStatus = () => {
    api.getPotentialScreenStatus().then((s) => {
      setStatus(s);
      if (!s.running) {
        stopPolling();
        loadResults();
      }
    });
  };

  useEffect(() => {
    api.getPotentialScreenStatus().then((s) => {
      setStatus(s);
      // The modal can mount while a run is already in progress (reopened
      // mid-run, or a scheduled run kicked in) -- start polling right away
      // instead of only when the button is clicked in this session.
      if (s.running) startPolling();
    });
    loadResults();
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleRun = async () => {
    setError(null);
    try {
      const result = await api.runPotentialScreen();
      if (result.status === "started") {
        startPolling();
      }
      pollStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("potential.error"));
    }
  };

  const handleRowClick = (ticker: string) => {
    onSelect(ticker);
    onClose();
  };

  const total = status?.total ?? 0;
  const scored = status?.scored ?? 0;
  const progressPct = total > 0 ? Math.min(100, Math.round((scored / total) * 100)) : 0;

  const statusLine = status?.running
    ? t("potential.status.running", { scored, total })
    : status?.last_completed_at
      ? t("potential.status.lastRun", { time: formatDateTime(status.last_completed_at, language) })
      : t("potential.status.never");

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>{t("potential.title")}</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint stats-hint">{t("potential.hint")}</p>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "var(--space-3)",
              marginBottom: "var(--space-3)",
            }}
          >
            <span className="faint">{statusLine}</span>
            <button className="btn btn--primary" onClick={() => void handleRun()} disabled={status?.running}>
              {status?.running ? t("potential.buttonRunning") : t("potential.button")}
            </button>
          </div>

          {status?.running && (
            <div
              className="wl-progress"
              role="progressbar"
              aria-valuenow={progressPct}
              aria-valuemin={0}
              aria-valuemax={100}
              style={{ marginBottom: "var(--space-3)" }}
            >
              <div
                className="potential-progress-fill"
                style={{ width: `${total > 0 ? progressPct : 5}%` }}
              />
            </div>
          )}

          {error && <p className="settings-error">{error}</p>}
          {!status?.running && status?.last_error && (
            <p className="settings-error">{t("potential.status.error", { error: status.last_error })}</p>
          )}
          {!rows && !error && <p className="faint">{t("common.loading")}</p>}
          {rows && rows.length === 0 && <p className="faint">{t("potential.empty")}</p>}

          {rows && rows.length > 0 && (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t("potential.table.ticker")}</th>
                    <th>{t("potential.table.score")}</th>
                    <th>{t("potential.table.reason")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.ticker} onClick={() => handleRowClick(r.ticker)} style={{ cursor: "pointer" }}>
                      <td className="faint">{i + 1}</td>
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>
                          {r.display_symbol}
                        </span>{" "}
                        {r.asset_class === "crypto" && <span title="Crypto">🪙</span>}
                        {r.name && <span className="faint"> {r.name}</span>}
                      </td>
                      <td className="mono" style={{ fontWeight: 700 }}>
                        {Math.round(r.score)}
                      </td>
                      <td style={{ whiteSpace: "normal" }}>{r.reason}</td>
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
