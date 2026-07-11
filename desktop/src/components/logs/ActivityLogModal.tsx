import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { ConfigChangeLogEntry, SystemAction, SystemActionLogEntry } from "../../types";
import { formatDateTimeMedium } from "../../lib/datetime";
import { useI18n } from "../../i18n/I18nContext";
import "../stats/stats.css";

interface Props {
  onClose: () => void;
}

type Tab = "config" | "system";

const PAGE_SIZE = 20;

const ACTION_KEY: Record<SystemAction, string> = {
  screener_scan: "logs.action.screener_scan",
  vn30_seed: "logs.action.vn30_seed",
  half_session_morning: "logs.action.half_session_morning",
  half_session_afternoon: "logs.action.half_session_afternoon",
  daily_close: "logs.action.daily_close",
  crypto_analysis_refresh: "logs.action.crypto_analysis_refresh",
};

const TRIGGER_KEY: Record<SystemActionLogEntry["trigger"], string> = {
  manual: "logs.trigger.manual",
  scheduled: "logs.trigger.scheduled",
};

const STATUS_KEY: Record<SystemActionLogEntry["status"], string> = {
  running: "logs.status.running",
  success: "logs.status.success",
  error: "logs.status.error",
  cancelled: "logs.status.cancelled",
};

const STATUS_COLOR: Record<SystemActionLogEntry["status"], string> = {
  running: "var(--warn)",
  success: "var(--bull)",
  error: "var(--bear)",
  cancelled: "var(--text-faint)",
};

export function ActivityLogModal({ onClose }: Props) {
  const { t, language } = useI18n();
  const [tab, setTab] = useState<Tab>("config");
  const [configItems, setConfigItems] = useState<ConfigChangeLogEntry[] | null>(null);
  const [systemItems, setSystemItems] = useState<SystemActionLogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPage(1);
  }, [tab]);

  useEffect(() => {
    setError(null);
    if (tab === "config") {
      api
        .getConfigLogs(page, PAGE_SIZE)
        .then((res) => {
          setConfigItems(res.items);
          setTotal(res.total);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : t("logs.error")));
    } else {
      api
        .getSystemLogs(page, PAGE_SIZE)
        .then((res) => {
          setSystemItems(res.items);
          setTotal(res.total);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : t("logs.error")));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, page]);

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
          <h2>{t("logs.title")}</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <div className="wl-tabs" style={{ marginBottom: "var(--space-3)", maxWidth: 280 }}>
            <button className={tab === "config" ? "is-active" : ""} onClick={() => setTab("config")}>
              {t("logs.tab.config")}
            </button>
            <button className={tab === "system" ? "is-active" : ""} onClick={() => setTab("system")}>
              {t("logs.tab.system")}
            </button>
          </div>

          {error && <p className="settings-error">{error}</p>}

          {tab === "config" && (
            <>
              {!configItems && !error && <p className="faint">{t("common.loading")}</p>}
              {configItems && configItems.length === 0 && (
                <p className="faint">{t("logs.config.empty")}</p>
              )}
              {configItems && configItems.length > 0 && (
                <div className="stats-table-wrap">
                  <table className="stats-table">
                    <thead>
                      <tr>
                        <th>{t("logs.config.col.time")}</th>
                        <th>{t("logs.config.col.field")}</th>
                        <th>{t("logs.config.col.oldValue")}</th>
                        <th>{t("logs.config.col.newValue")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {configItems.map((e) => (
                        <tr key={e.id}>
                          <td className="faint">{formatDateTimeMedium(e.changed_at, language)}</td>
                          <td className="mono">{e.key}</td>
                          <td className="mono faint">{e.old_value || t("common.dash")}</td>
                          <td className="mono">{e.new_value || t("common.dash")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {tab === "system" && (
            <>
              {!systemItems && !error && <p className="faint">{t("common.loading")}</p>}
              {systemItems && systemItems.length === 0 && (
                <p className="faint">{t("logs.system.empty")}</p>
              )}
              {systemItems && systemItems.length > 0 && (
                <div className="stats-table-wrap">
                  <table className="stats-table">
                    <thead>
                      <tr>
                        <th>{t("logs.system.col.start")}</th>
                        <th>{t("logs.system.col.action")}</th>
                        <th>{t("logs.system.col.trigger")}</th>
                        <th>{t("logs.system.col.status")}</th>
                        <th>{t("logs.system.col.end")}</th>
                        <th>{t("logs.system.col.detail")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {systemItems.map((e) => (
                        <tr key={e.id}>
                          <td className="faint">{formatDateTimeMedium(e.started_at, language)}</td>
                          <td>{t(ACTION_KEY[e.action] ?? e.action)}</td>
                          <td className="faint">{t(TRIGGER_KEY[e.trigger])}</td>
                          <td>
                            <span
                              style={{
                                display: "inline-block",
                                padding: "3px 10px",
                                borderRadius: 999,
                                fontSize: "var(--text-xs)",
                                fontWeight: 700,
                                color: "oklch(18% 0.02 250)",
                                backgroundColor: STATUS_COLOR[e.status],
                              }}
                            >
                              {t(STATUS_KEY[e.status])}
                            </span>
                          </td>
                          <td className="faint">
                            {e.finished_at ? formatDateTimeMedium(e.finished_at, language) : t("common.dash")}
                          </td>
                          <td className="faint">{e.detail ?? t("common.dash")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
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
