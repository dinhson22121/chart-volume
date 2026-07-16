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
  top100_seed: "logs.action.top100_seed",
  half_session_morning: "logs.action.half_session_morning",
  half_session_afternoon: "logs.action.half_session_afternoon",
  daily_close: "logs.action.daily_close",
  crypto_analysis_refresh: "logs.action.crypto_analysis_refresh",
  potential_screen: "logs.action.potential_screen",
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

const FIELD_LABELS: Record<string, Record<string, string>> = {
  vi: {
    language: "Ngôn ngữ",
    strategy: "Chiến lược",
    narrative_provider: "Nhà cung cấp AI",
    anthropic_api_key: "Claude API Key",
    anthropic_model: "Claude Model",
    ollama_model: "Ollama Model",
    antigravity_model: "Antigravity Model",
    gemini_api_key: "Gemini API Key",
    openai_model: "OpenAI Model",
    openai_api_key: "OpenAI API Key",
    daily_lookback_days: "Số ngày lịch sử (ngày)",
    half_session_lookback_days: "Số ngày lịch sử (nửa phiên)",
    scheduler_enabled: "Tự động phân tích",
    half_morning_time: "Giờ chạy sáng",
    half_afternoon_time: "Giờ chạy chiều",
    daily_time: "Giờ chạy ngày",
    climax_vol_mult: "Hệ số Climax Volume",
    wide_spread_mult: "Hệ số Wide Spread",
    narrow_spread_mult: "Hệ số Narrow Spread",
    low_vol_mult: "Hệ số Low Volume",
    sos_vol_mult: "Hệ số SOS Volume",
    lps_lookback_bars: "Số nến xác nhận LPS",
    sonicr_dragon_period: "Sonic R Dragon Period",
    sonicr_t3_fast_period: "Sonic R T3 Fast Period",
    sonicr_t3_slow_period: "Sonic R T3 Slow Period",
    sonicr_t3_vfactor: "Sonic R T3 VFactor",
    sonicr_cci_fast_period: "Sonic R CCI Fast Period",
    sonicr_cci_slow_period: "Sonic R CCI Slow Period",
    sonicr_pullback_lookback_bars: "Sonic R Pullback Lookback",
    smc_swing_lookback: "SMC Swing Lookback",
    smc_ob_lookback_bars: "SMC OB Lookback",
    smc_fvg_min_gap_mult: "SMC FVG Min Gap",
    screener_enabled: "Crypto Screener",
    screener_mcap_max: "Screener Max Cap",
    screener_require_volume_rising: "Screener Require Vol Rising",
    screener_min_volume_change_pct: "Screener Min Vol Change %",
    screener_scan_interval: "Screener Interval",
    crypto_exchanges: "Sàn giao dịch crypto",
    crypto_analysis_enabled: "Phân tích crypto",
    crypto_analysis_interval: "Phân tích crypto Interval",
    top100_auto_refresh_enabled: "Cập nhật Top 100",
    top100_refresh_time: "Giờ cập nhật Top 100",
    ai_narrative_vn30: "AI cho VN30",
    ai_narrative_watchlist: "AI cho danh sách theo dõi",
    ai_narrative_top100: "AI cho Top 100",
    potential_screen_auto_enabled: "Tự động chạy AI Đánh giá tiềm năng",
    potential_screen_time: "Giờ chạy AI Đánh giá tiềm năng",
  },
  en: {
    language: "Language",
    strategy: "Strategy",
    narrative_provider: "AI Provider",
    anthropic_api_key: "Claude API Key",
    anthropic_model: "Claude Model",
    ollama_model: "Ollama Model",
    antigravity_model: "Antigravity Model",
    gemini_api_key: "Gemini API Key",
    openai_model: "OpenAI Model",
    openai_api_key: "OpenAI API Key",
    daily_lookback_days: "Lookback Days (Daily)",
    half_session_lookback_days: "Lookback Days (Half Session)",
    scheduler_enabled: "Auto Analysis",
    half_morning_time: "Morning Run Time",
    half_afternoon_time: "Afternoon Run Time",
    daily_time: "Daily Run Time",
    climax_vol_mult: "Climax Volume Multiplier",
    wide_spread_mult: "Wide Spread Multiplier",
    narrow_spread_mult: "Narrow Spread Multiplier",
    low_vol_mult: "Low Volume Multiplier",
    sos_vol_mult: "SOS Volume Multiplier",
    lps_lookback_bars: "LPS Lookback Bars",
    sonicr_dragon_period: "Sonic R Dragon Period",
    sonicr_t3_fast_period: "Sonic R T3 Fast Period",
    sonicr_t3_slow_period: "Sonic R T3 Slow Period",
    sonicr_t3_vfactor: "Sonic R T3 VFactor",
    sonicr_cci_fast_period: "Sonic R CCI Fast Period",
    sonicr_cci_slow_period: "Sonic R CCI Slow Period",
    sonicr_pullback_lookback_bars: "Sonic R Pullback Lookback",
    smc_swing_lookback: "SMC Swing Lookback",
    smc_ob_lookback_bars: "SMC OB Lookback",
    smc_fvg_min_gap_mult: "SMC FVG Min Gap",
    screener_enabled: "Crypto Screener",
    screener_mcap_max: "Screener Max Cap",
    screener_require_volume_rising: "Screener Require Vol Rising",
    screener_min_volume_change_pct: "Screener Min Vol Change %",
    screener_scan_interval: "Screener Interval",
    crypto_exchanges: "Crypto Exchanges",
    crypto_analysis_enabled: "Crypto Analysis",
    crypto_analysis_interval: "Crypto Analysis Interval",
    top100_auto_refresh_enabled: "Top 100 Auto Refresh",
    top100_refresh_time: "Top 100 Refresh Time",
    ai_narrative_vn30: "AI for VN30",
    ai_narrative_watchlist: "AI for Watchlist",
    ai_narrative_top100: "AI for Top 100",
    potential_screen_auto_enabled: "AI Potential Screen Auto-run",
    potential_screen_time: "AI Potential Screen Run Time",
  }
};

export function ActivityLogModal({ onClose }: Props) {
  const { t, language } = useI18n();
  const [tab, setTab] = useState<Tab>("config");
  const [configItems, setConfigItems] = useState<ConfigChangeLogEntry[] | null>(null);
  const [systemItems, setSystemItems] = useState<SystemActionLogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

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

  const handleExport = async () => {
    setExporting(true);
    setError(null);
    try {
      const { content } = await api.exportLogs();
      const blob = new Blob([content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 16);
      const a = document.createElement("a");
      a.href = url;
      a.download = `chart-volume-log-${stamp}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("logs.export.error"));
    } finally {
      setExporting(false);
    }
  };

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
                          <td className="mono">{FIELD_LABELS[language]?.[e.key] || e.key}</td>
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

        <footer className="settings-modal__footer" style={{ justifyContent: "space-between" }}>
          <button className="btn" onClick={() => void handleExport()} disabled={exporting}>
            {exporting ? t("logs.export.buttonLoading") : t("logs.export.button")}
          </button>
          <button className="btn" onClick={onClose}>
            {t("common.close")}
          </button>
        </footer>
      </div>
    </div>
  );
}
