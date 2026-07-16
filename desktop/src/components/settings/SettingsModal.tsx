import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { NarrativeProvider, OllamaStatus, Settings, SettingsUpdate } from "../../types";
import { useI18n } from "../../i18n/I18nContext";
import type { Language } from "../../i18n/translations";
import { LicenseSection } from "../license/LicenseSection";
import "./settings.css";

interface Props {
  onClose: () => void;
  // App.tsx's header now owns the strategy dropdown (see App.tsx's
  // handleStrategyChange), which saves immediately -- if the user flips it
  // while this modal happens to already be open, sync form/loaded here too
  // so the right per-strategy threshold section shows without needing to
  // close and reopen Settings.
  strategy: string;
  onLicenseCleared: () => void;
}

const OLLAMA_SUGGESTIONS = ["qwen2.5:7b", "qwen2.5:3b", "llama3.1:8b", "deepseek-r1:7b", "mistral:7b"];
const OLLAMA_DOWNLOAD_URL = "https://ollama.com/download";

// Below this, even the lightest usable model risks starving the OS + the
// app itself of memory -- disable the local-AI option entirely rather than
// let the user hit an opaque hang/crash mid-analysis.
const MIN_RAM_GB_FOR_LOCAL_AI = 8;
// Below this, still usable but only comfortably for the smaller model.
const RAM_GB_FOR_7B_MODEL = 16;
const RECOMMENDED_MODEL_LOW_RAM = { name: "qwen2.5:3b", sizeLabel: "~2GB" };
const RECOMMENDED_MODEL_HIGH_RAM = { name: "qwen2.5:7b", sizeLabel: "~4.7GB" };

// Renderer has no direct OS access -- Electron's preload bridges totalMemGB
// from the main process (os.totalmem(), cross-platform on Mac/Mac ARM/Win64/
// Linux). In a plain-browser dev session (no preload) there's no real
// machine to gate, so default to "assume sufficient".
function getTotalMemGB(): number {
  return window.chartVolume?.totalMemGB ?? Infinity;
}

function openExternal(url: string): void {
  if (window.chartVolume?.openExternal) {
    void window.chartVolume.openExternal(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

// Local editable form shape: numbers become strings so inputs can hold
// intermediate/invalid text while typing, without fighting controlled-input state.
interface FormState {
  strategy: string;
  narrativeProvider: NarrativeProvider;
  anthropicApiKey: string;
  anthropicModel: string;
  ollamaModel: string;
  antigravityModel: string;
  geminiApiKey: string;
  openaiModel: string;
  openaiApiKey: string;
  dailyLookbackDays: string;
  halfSessionLookbackDays: string;
  schedulerEnabled: boolean;
  halfMorningTime: string;
  halfAfternoonTime: string;
  dailyTime: string;
  climaxVolMult: string;
  wideSpreadMult: string;
  narrowSpreadMult: string;
  lowVolMult: string;
  sosVolMult: string;
  lpsLookbackBars: string;
  sonicrDragonPeriod: string;
  sonicrT3FastPeriod: string;
  sonicrT3SlowPeriod: string;
  sonicrT3Vfactor: string;
  sonicrCciFastPeriod: string;
  sonicrCciSlowPeriod: string;
  sonicrPullbackLookbackBars: string;
  smcSwingLookback: string;
  smcObLookbackBars: string;
  smcFvgMinGapMult: string;
  screenerEnabled: boolean;
  screenerMcapMax: string;
  screenerRequireVolumeRising: boolean;
  screenerMinVolumeChangePct: string;
  screenerScanInterval: string;
  cryptoAnalysisEnabled: boolean;
  cryptoAnalysisInterval: string;
  top100AutoRefreshEnabled: boolean;
  top100RefreshTime: string;
  potentialScreenAutoEnabled: boolean;
  potentialScreenTime: string;
  aiNarrativeVn30: boolean;
  aiNarrativeWatchlist: boolean;
  aiNarrativeTop100: boolean;
}

function toForm(s: Settings): FormState {
  return {
    strategy: s.strategy,
    narrativeProvider: s.narrative_provider,
    anthropicApiKey: "",
    anthropicModel: s.anthropic_model,
    ollamaModel: s.ollama_model,
    antigravityModel: s.antigravity_model,
    geminiApiKey: "",
    openaiModel: s.openai_model,
    openaiApiKey: "",
    dailyLookbackDays: String(s.daily_lookback_days),
    halfSessionLookbackDays: String(s.half_session_lookback_days),
    schedulerEnabled: s.scheduler_enabled,
    halfMorningTime: s.half_morning_time,
    halfAfternoonTime: s.half_afternoon_time,
    dailyTime: s.daily_time,
    climaxVolMult: String(s.climax_vol_mult),
    wideSpreadMult: String(s.wide_spread_mult),
    narrowSpreadMult: String(s.narrow_spread_mult),
    lowVolMult: String(s.low_vol_mult),
    sosVolMult: String(s.sos_vol_mult),
    lpsLookbackBars: String(s.lps_lookback_bars),
    sonicrDragonPeriod: String(s.sonicr_dragon_period),
    sonicrT3FastPeriod: String(s.sonicr_t3_fast_period),
    sonicrT3SlowPeriod: String(s.sonicr_t3_slow_period),
    sonicrT3Vfactor: String(s.sonicr_t3_vfactor),
    sonicrCciFastPeriod: String(s.sonicr_cci_fast_period),
    sonicrCciSlowPeriod: String(s.sonicr_cci_slow_period),
    sonicrPullbackLookbackBars: String(s.sonicr_pullback_lookback_bars),
    smcSwingLookback: String(s.smc_swing_lookback),
    smcObLookbackBars: String(s.smc_ob_lookback_bars),
    smcFvgMinGapMult: String(s.smc_fvg_min_gap_mult),
    screenerEnabled: s.screener_enabled,
    screenerMcapMax: String(s.screener_mcap_max),
    screenerRequireVolumeRising: s.screener_require_volume_rising,
    screenerMinVolumeChangePct: String(s.screener_min_volume_change_pct),
    screenerScanInterval: s.screener_scan_interval,
    cryptoAnalysisEnabled: s.crypto_analysis_enabled,
    cryptoAnalysisInterval: s.crypto_analysis_interval,
    top100AutoRefreshEnabled: s.top100_auto_refresh_enabled,
    top100RefreshTime: s.top100_refresh_time,
    potentialScreenAutoEnabled: s.potential_screen_auto_enabled,
    potentialScreenTime: s.potential_screen_time,
    aiNarrativeVn30: s.ai_narrative_vn30,
    aiNarrativeWatchlist: s.ai_narrative_watchlist,
    aiNarrativeTop100: s.ai_narrative_top100,
  };
}

function toUpdate(f: FormState): SettingsUpdate {
  const update: SettingsUpdate = {
    strategy: f.strategy,
    narrative_provider: f.narrativeProvider,
    anthropic_model: f.anthropicModel,
    ollama_model: f.ollamaModel,
    antigravity_model: f.antigravityModel,
    openai_model: f.openaiModel,
    daily_lookback_days: Number(f.dailyLookbackDays),
    half_session_lookback_days: Number(f.halfSessionLookbackDays),
    scheduler_enabled: f.schedulerEnabled,
    half_morning_time: f.halfMorningTime,
    half_afternoon_time: f.halfAfternoonTime,
    daily_time: f.dailyTime,
    climax_vol_mult: Number(f.climaxVolMult),
    wide_spread_mult: Number(f.wideSpreadMult),
    narrow_spread_mult: Number(f.narrowSpreadMult),
    low_vol_mult: Number(f.lowVolMult),
    sos_vol_mult: Number(f.sosVolMult),
    lps_lookback_bars: Number(f.lpsLookbackBars),
    sonicr_dragon_period: Number(f.sonicrDragonPeriod),
    sonicr_t3_fast_period: Number(f.sonicrT3FastPeriod),
    sonicr_t3_slow_period: Number(f.sonicrT3SlowPeriod),
    sonicr_t3_vfactor: Number(f.sonicrT3Vfactor),
    sonicr_cci_fast_period: Number(f.sonicrCciFastPeriod),
    sonicr_cci_slow_period: Number(f.sonicrCciSlowPeriod),
    sonicr_pullback_lookback_bars: Number(f.sonicrPullbackLookbackBars),
    smc_swing_lookback: Number(f.smcSwingLookback),
    smc_ob_lookback_bars: Number(f.smcObLookbackBars),
    smc_fvg_min_gap_mult: Number(f.smcFvgMinGapMult),
    screener_enabled: f.screenerEnabled,
    screener_mcap_max: Number(f.screenerMcapMax),
    screener_require_volume_rising: f.screenerRequireVolumeRising,
    screener_min_volume_change_pct: Number(f.screenerMinVolumeChangePct),
    screener_scan_interval: f.screenerScanInterval,
    crypto_analysis_enabled: f.cryptoAnalysisEnabled,
    crypto_analysis_interval: f.cryptoAnalysisInterval,
    top100_auto_refresh_enabled: f.top100AutoRefreshEnabled,
    top100_refresh_time: f.top100RefreshTime,
    potential_screen_auto_enabled: f.potentialScreenAutoEnabled,
    potential_screen_time: f.potentialScreenTime,
    ai_narrative_vn30: f.aiNarrativeVn30,
    ai_narrative_watchlist: f.aiNarrativeWatchlist,
    ai_narrative_top100: f.aiNarrativeTop100,
  };
  if (f.anthropicApiKey.trim()) {
    update.anthropic_api_key = f.anthropicApiKey.trim();
  }
  if (f.geminiApiKey.trim()) {
    update.gemini_api_key = f.geminiApiKey.trim();
  }
  if (f.openaiApiKey.trim()) {
    update.openai_api_key = f.openaiApiKey.trim();
  }
  return update;
}

export function SettingsModal({ onClose, strategy, onLicenseCleared }: Props) {
  const { t, language, setLanguage } = useI18n();
  const [loaded, setLoaded] = useState<Settings | null>(null);
  const [form, setForm] = useState<FormState | null>(null);

  // Keeps an already-open modal in sync if the header's strategy dropdown
  // changes while this is open -- updates loaded+form in lockstep so isDirty
  // (which diffs form against toForm(loaded)) doesn't false-flag the Save
  // button just because the header changed strategy out from under it.
  useEffect(() => {
    setLoaded((l) => (l && l.strategy !== strategy ? { ...l, strategy } : l));
    setForm((f) => (f && f.strategy !== strategy ? { ...f, strategy } : f));
  }, [strategy]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [ollamaStatusLoading, setOllamaStatusLoading] = useState(false);
  const [pullModelName, setPullModelName] = useState("");
  const [pulling, setPulling] = useState(false);
  const [pullProgress, setPullProgress] = useState<string | null>(null);
  const [pullError, setPullError] = useState<string | null>(null);

  const MODEL_OPTIONS = [
    { value: "claude-sonnet-4-5", label: t("settings.ai.model.sonnet") },
    { value: "claude-opus-4-5", label: t("settings.ai.model.opus") },
    { value: "claude-haiku-4-5", label: t("settings.ai.model.haiku") },
  ];

  const MCAP_OPTIONS = [
    { value: "10000000", label: t("settings.screener.mcap.10m") },
    { value: "20000000", label: t("settings.screener.mcap.20m") },
    { value: "30000000", label: t("settings.screener.mcap.30m") },
    { value: "50000000", label: t("settings.screener.mcap.50m") },
  ];

  const SCAN_INTERVAL_OPTIONS = [
    { value: "10m", label: t("settings.interval.10m") },
    { value: "30m", label: t("settings.interval.30m") },
    { value: "1h", label: t("settings.interval.1h") },
    { value: "4h", label: t("settings.interval.4h") },
    { value: "12h", label: t("settings.interval.12h") },
    { value: "1d", label: t("settings.interval.1d") },
  ];

  useEffect(() => {
    void api.getSettings().then((s) => {
      setLoaded(s);
      setForm(toForm(s));
    });
  }, []);

  const refreshOllamaStatus = useCallback(async () => {
    setOllamaStatusLoading(true);
    try {
      setOllamaStatus(await api.getOllamaStatus());
    } catch {
      setOllamaStatus({ available: false, models: [] });
    } finally {
      setOllamaStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    if (form?.narrativeProvider === "ollama" && ollamaStatus === null && !ollamaStatusLoading) {
      void refreshOllamaStatus();
    }
  }, [form?.narrativeProvider, ollamaStatus, ollamaStatusLoading, refreshOllamaStatus]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  // Deep-compares against the last-saved values (not just "something changed
  // at some point") -- so flipping a setting and flipping it back leaves the
  // Save button in its normal state instead of staying flagged dirty.
  const isDirty = form !== null && loaded !== null && JSON.stringify(form) !== JSON.stringify(toForm(loaded));

  const totalMemGB = getTotalMemGB();
  const hasEnoughRamForLocalAI = totalMemGB >= MIN_RAM_GB_FOR_LOCAL_AI;
  const recommendedModel = totalMemGB >= RAM_GB_FOR_7B_MODEL ? RECOMMENDED_MODEL_HIGH_RAM : RECOMMENDED_MODEL_LOW_RAM;

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateSettings(toUpdate(form));
      setLoaded(updated);
      setForm(toForm(updated));
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("settings.error.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handlePullModel = async (modelOverride?: string) => {
    const model = (modelOverride ?? pullModelName).trim();
    if (!model) return;
    setPulling(true);
    setPullError(null);
    setPullProgress(t("settings.ai.pullStarting"));
    try {
      await api.pullOllamaModel(model, (event) => {
        if (event.error) {
          setPullError(event.error);
          return;
        }
        if (event.total && event.completed) {
          const pct = Math.round((event.completed / event.total) * 100);
          setPullProgress(`${event.status ?? t("settings.ai.pullStatusFallback")} — ${pct}%`);
        } else {
          setPullProgress(event.status ?? t("settings.ai.pullDownloading"));
        }
      });
      setPullProgress(t("settings.ai.pullDone"));
      set("ollamaModel", model);
      setPullModelName("");
      await refreshOllamaStatus();
    } catch (e) {
      setPullError(e instanceof Error ? e.message : t("settings.ai.pullError"));
    } finally {
      setPulling(false);
    }
  };

  const handleClearKey = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateSettings({ anthropic_api_key: "" });
      setLoaded(updated);
      setForm((prev) => (prev ? { ...toForm(updated), ...prev, anthropicApiKey: "" } : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("settings.error.clearKeyFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleClearGeminiKey = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateSettings({ gemini_api_key: "" });
      setLoaded(updated);
      setForm((prev) => (prev ? { ...toForm(updated), ...prev, geminiApiKey: "" } : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("settings.error.clearKeyFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleClearOpenaiKey = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateSettings({ openai_api_key: "" });
      setLoaded(updated);
      setForm((prev) => (prev ? { ...toForm(updated), ...prev, openaiApiKey: "" } : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("settings.error.clearKeyFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>{t("settings.title")}</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>

        {!form ? (
          <div className="settings-modal__body faint">{t("common.loading")}</div>
        ) : (
          <div className="settings-modal__body">
            <LicenseSection onCleared={onLicenseCleared} />

            <section className="settings-section">
              <h3>{t("settings.language.title")}</h3>
              <div className="tf-toggle settings-provider-toggle">
                <button
                  className={language === "vi" ? "is-active" : ""}
                  onClick={() => setLanguage("vi" as Language)}
                >
                  {t("settings.language.vi")}
                </button>
                <button
                  className={language === "en" ? "is-active" : ""}
                  onClick={() => setLanguage("en" as Language)}
                >
                  {t("settings.language.en")}
                </button>
              </div>
            </section>

            <section className="settings-section">
              <h3>{t("settings.section.ai")}</h3>
              <div className="tf-toggle settings-provider-toggle">
                <button
                  className={form.narrativeProvider === "anthropic" ? "is-active" : ""}
                  onClick={() => set("narrativeProvider", "anthropic")}
                >
                  {t("settings.ai.claude")}
                </button>
                <button
                  className={form.narrativeProvider === "antigravity" ? "is-active" : ""}
                  onClick={() => set("narrativeProvider", "antigravity")}
                >
                  {t("settings.ai.antigravity")}
                </button>
                <button
                  className={form.narrativeProvider === "codex" ? "is-active" : ""}
                  onClick={() => set("narrativeProvider", "codex")}
                >
                  {t("settings.ai.codex")}
                </button>
                <button
                  className={form.narrativeProvider === "ollama" ? "is-active" : ""}
                  onClick={() => set("narrativeProvider", "ollama")}
                  disabled={!hasEnoughRamForLocalAI}
                  title={
                    hasEnoughRamForLocalAI
                      ? undefined
                      : t("settings.ai.ollamaDisabledTitle", { min: MIN_RAM_GB_FOR_LOCAL_AI, total: totalMemGB })
                  }
                >
                  {t("settings.ai.ollama")}
                </button>
              </div>
              {!hasEnoughRamForLocalAI && form.narrativeProvider === "anthropic" && (
                <p className="settings-hint faint">
                  {t("settings.ai.notEnoughRamHint", { total: totalMemGB, min: MIN_RAM_GB_FOR_LOCAL_AI })}
                </p>
              )}

              {form.narrativeProvider === "anthropic" && (
                <>
                  <label className="settings-field">
                    <span>
                      {t("settings.ai.apiKeyLabel")}{" "}
                      {loaded?.has_anthropic_key && (
                        <em className="settings-badge">{t("settings.ai.apiKeySaved")}</em>
                      )}
                    </span>
                    <div className="settings-field__row">
                      <input
                        type="password"
                        placeholder={loaded?.has_anthropic_key ? t("settings.ai.apiKeyPlaceholderChange") : "sk-ant-..."}
                        value={form.anthropicApiKey}
                        onChange={(e) => set("anthropicApiKey", e.target.value)}
                      />
                      {loaded?.has_anthropic_key && (
                        <button className="btn" onClick={() => void handleClearKey()} disabled={saving}>
                          {t("settings.ai.apiKeyClear")}
                        </button>
                      )}
                    </div>
                    <span className="settings-hint faint">{t("settings.ai.apiKeyHint")}</span>
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.ai.modelLabel")}</span>
                    <select value={form.anthropicModel} onChange={(e) => set("anthropicModel", e.target.value)}>
                      {MODEL_OPTIONS.map((m) => (
                        <option key={m.value} value={m.value}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              )}

              {form.narrativeProvider === "antigravity" && (
                <>
                  <label className="settings-field">
                    <span>
                      {t("settings.ai.geminiApiKeyLabel")}{" "}
                      {loaded?.has_gemini_key && (
                        <em className="settings-badge">{t("settings.ai.apiKeySaved")}</em>
                      )}
                    </span>
                    <div className="settings-field__row">
                      <input
                        type="password"
                        placeholder={loaded?.has_gemini_key ? t("settings.ai.geminiApiKeyPlaceholderChange") : "AIzaSy..."}
                        value={form.geminiApiKey}
                        onChange={(e) => set("geminiApiKey", e.target.value)}
                      />
                      {loaded?.has_gemini_key && (
                        <button className="btn" onClick={() => void handleClearGeminiKey()} disabled={saving}>
                          {t("settings.ai.apiKeyClear")}
                        </button>
                      )}
                    </div>
                    <span className="settings-hint faint">{t("settings.ai.geminiApiKeyHint")}</span>
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.ai.antigravityModelLabel")}</span>
                    <select value={form.antigravityModel} onChange={(e) => set("antigravityModel", e.target.value)}>
                      <option value="gemini-3.5-flash">Gemini 3.5 Flash (Tối ưu/Nhanh)</option>
                      <option value="gemini-3.5-pro">Gemini 3.5 Pro (Thông minh/Sâu sắc)</option>
                      <option value="gemini-3.1-pro">Gemini 3.1 Pro (Bản Pro ổn định)</option>
                    </select>
                  </label>
                </>
              )}

              {form.narrativeProvider === "codex" && (
                <>
                  <label className="settings-field">
                    <span>
                      {t("settings.ai.apiKeyLabel")}{" "}
                      {loaded?.has_openai_key && (
                        <em className="settings-badge">{t("settings.ai.apiKeySaved")}</em>
                      )}
                    </span>
                    <div className="settings-field__row">
                      <input
                        type="password"
                        placeholder={loaded?.has_openai_key ? t("settings.ai.apiKeyPlaceholderChange") : "sk-..."}
                        value={form.openaiApiKey}
                        onChange={(e) => set("openaiApiKey", e.target.value)}
                      />
                      {loaded?.has_openai_key && (
                        <button className="btn" onClick={() => void handleClearOpenaiKey()} disabled={saving}>
                          {t("settings.ai.apiKeyClear")}
                        </button>
                      )}
                    </div>
                    <span className="settings-hint faint">{t("settings.ai.apiKeyHint")}</span>
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.ai.modelLabel")}</span>
                    <select value={form.openaiModel} onChange={(e) => set("openaiModel", e.target.value)}>
                      <option value="gpt-5">GPT-5 (khuyến nghị)</option>
                      <option value="gpt-5-mini">GPT-5 Mini (nhanh, rẻ)</option>
                      <option value="gpt-5-codex">GPT-5 Codex (chuyên code)</option>
                    </select>
                  </label>
                </>
              )}

              {form.narrativeProvider === "ollama" && (
                <div className="settings-ollama">
                  {!hasEnoughRamForLocalAI && (
                    <p className="settings-hint settings-hint--warn">
                      {t("settings.ai.notEnoughRamWarn", { total: totalMemGB, min: MIN_RAM_GB_FOR_LOCAL_AI })}
                    </p>
                  )}
                  <div className="settings-ollama__status">
                    {ollamaStatusLoading ? (
                      <span className="faint">{t("settings.ai.checkingOllama")}</span>
                    ) : ollamaStatus?.available ? (
                      <span className="settings-badge settings-badge--ok">{t("settings.ai.ollamaRunning")}</span>
                    ) : (
                      <span className="settings-badge settings-badge--off">
                        {t("settings.ai.ollamaNotConnected")}
                      </span>
                    )}
                    <button className="btn" onClick={() => void refreshOllamaStatus()} disabled={ollamaStatusLoading}>
                      {t("settings.ai.checkAgain")}
                    </button>
                  </div>

                  {!ollamaStatusLoading && !ollamaStatus?.available && (
                    <div className="settings-wizard-card">
                      <p className="settings-wizard-card__title">{t("settings.ai.wizard1Title")}</p>
                      <p className="settings-hint faint">{t("settings.ai.wizard1Hint")}</p>
                      <button className="btn btn--primary" onClick={() => openExternal(OLLAMA_DOWNLOAD_URL)}>
                        {t("settings.ai.downloadOllama")}
                      </button>
                      <p className="settings-hint faint">{t("settings.ai.wizard1AfterInstall")}</p>
                      <button className="btn" onClick={() => void refreshOllamaStatus()} disabled={ollamaStatusLoading}>
                        {t("settings.ai.checkAgain")}
                      </button>
                    </div>
                  )}

                  {ollamaStatus?.available && ollamaStatus.models.length === 0 && (
                    <div className="settings-wizard-card">
                      <p className="settings-wizard-card__title">{t("settings.ai.wizard2Title")}</p>
                      <p className="settings-hint faint">{t("settings.ai.wizard2Hint")}</p>
                      <button
                        className="btn btn--primary"
                        onClick={() => void handlePullModel(recommendedModel.name)}
                        disabled={pulling}
                      >
                        {pulling
                          ? t("settings.ai.pullDownloading")
                          : t("settings.ai.installRecommended", {
                              model: recommendedModel.name,
                              size: recommendedModel.sizeLabel,
                            })}
                      </button>
                      {pullProgress && !pullError && <p className="settings-hint faint">{pullProgress}</p>}
                      {pullError && <p className="settings-error">{pullError}</p>}
                    </div>
                  )}

                  {ollamaStatus?.models && ollamaStatus.models.length > 0 && (
                    <label className="settings-field">
                      <span>{t("settings.ai.installedModelLabel")}</span>
                      <select value={form.ollamaModel} onChange={(e) => set("ollamaModel", e.target.value)}>
                        <option value="">{t("settings.ai.chooseModelOption")}</option>
                        {ollamaStatus.models.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}

                  <label className="settings-field">
                    <span>{t("settings.ai.pullNewModelLabel")}</span>
                    <div className="settings-field__row">
                      <input
                        list="ollama-suggestions"
                        type="text"
                        placeholder="qwen2.5:7b"
                        value={pullModelName}
                        onChange={(e) => setPullModelName(e.target.value)}
                        disabled={pulling}
                      />
                      <button
                        className="btn"
                        onClick={() => void handlePullModel()}
                        disabled={pulling || !pullModelName.trim()}
                      >
                        {pulling ? t("settings.ai.pullDownloading") : t("settings.ai.pullButton")}
                      </button>
                    </div>
                    <datalist id="ollama-suggestions">
                      {OLLAMA_SUGGESTIONS.map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                    {pullProgress && !pullError && <span className="settings-hint faint">{pullProgress}</span>}
                    {pullError && <span className="settings-error">{pullError}</span>}
                  </label>
                </div>
              )}

              <div className="settings-field">
                <span>{t("settings.ai.narrativeGroupsLabel")}</span>
                <span className="settings-hint faint">{t("settings.ai.narrativeGroupsHint")}</span>
                <label className="settings-field--row" style={{ display: "flex", gap: "var(--space-2)" }}>
                  <input
                    type="checkbox"
                    checked={form.aiNarrativeVn30}
                    onChange={(e) => set("aiNarrativeVn30", e.target.checked)}
                  />
                  <span>{t("settings.ai.narrativeVn30")}</span>
                </label>
                <label className="settings-field--row" style={{ display: "flex", gap: "var(--space-2)" }}>
                  <input
                    type="checkbox"
                    checked={form.aiNarrativeWatchlist}
                    onChange={(e) => set("aiNarrativeWatchlist", e.target.checked)}
                  />
                  <span>{t("settings.ai.narrativeWatchlist")}</span>
                </label>
                <label className="settings-field--row" style={{ display: "flex", gap: "var(--space-2)" }}>
                  <input
                    type="checkbox"
                    checked={form.aiNarrativeTop100}
                    onChange={(e) => set("aiNarrativeTop100", e.target.checked)}
                  />
                  <span>{t("settings.ai.narrativeTop100")}</span>
                </label>
              </div>
            </section>

            <section className="settings-section">
              <h3>{t("settings.section.crawlDepth")}</h3>
              <div className="settings-grid">
                <label className="settings-field">
                  <span>{t("settings.crawl.dailyLabel")}</span>
                  <input
                    type="number"
                    min={30}
                    max={3650}
                    value={form.dailyLookbackDays}
                    onChange={(e) => set("dailyLookbackDays", e.target.value)}
                  />
                </label>
                <label className="settings-field">
                  <span>{t("settings.crawl.halfSessionLabel")}</span>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={form.halfSessionLookbackDays}
                    onChange={(e) => set("halfSessionLookbackDays", e.target.value)}
                  />
                </label>
              </div>
            </section>

            <section className="settings-section">
              <h3>{t("settings.section.autoUpdate")}</h3>
              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.schedulerEnabled}
                  onChange={(e) => set("schedulerEnabled", e.target.checked)}
                />
                <span>{t("settings.autoUpdate.enableStock")}</span>
              </label>
              <div className="settings-grid">
                <label className="settings-field">
                  <span>{t("settings.autoUpdate.afterMorning")}</span>
                  <input
                    type="time"
                    value={form.halfMorningTime}
                    disabled={!form.schedulerEnabled}
                    onChange={(e) => set("halfMorningTime", e.target.value)}
                  />
                </label>
                <label className="settings-field">
                  <span>{t("settings.autoUpdate.afterAfternoon")}</span>
                  <input
                    type="time"
                    value={form.halfAfternoonTime}
                    disabled={!form.schedulerEnabled}
                    onChange={(e) => set("halfAfternoonTime", e.target.value)}
                  />
                </label>
                <label className="settings-field">
                  <span>{t("settings.autoUpdate.afterClose")}</span>
                  <input
                    type="time"
                    value={form.dailyTime}
                    disabled={!form.schedulerEnabled}
                    onChange={(e) => set("dailyTime", e.target.value)}
                  />
                </label>
              </div>

              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.cryptoAnalysisEnabled}
                  onChange={(e) => set("cryptoAnalysisEnabled", e.target.checked)}
                />
                <span>{t("settings.autoUpdate.enableCrypto")}</span>
              </label>
              <label className="settings-field">
                <span>{t("settings.autoUpdate.cryptoInterval")}</span>
                <select
                  value={form.cryptoAnalysisInterval}
                  disabled={!form.cryptoAnalysisEnabled}
                  onChange={(e) => set("cryptoAnalysisInterval", e.target.value)}
                >
                  {SCAN_INTERVAL_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <span className="settings-hint faint">{t("settings.autoUpdate.cryptoIntervalHint")}</span>
              </label>

              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.top100AutoRefreshEnabled}
                  onChange={(e) => set("top100AutoRefreshEnabled", e.target.checked)}
                />
                <span>{t("settings.autoUpdate.enableTop100")}</span>
              </label>
              <label className="settings-field">
                <span>{t("settings.autoUpdate.top100Time")}</span>
                <input
                  type="time"
                  value={form.top100RefreshTime}
                  disabled={!form.top100AutoRefreshEnabled}
                  onChange={(e) => set("top100RefreshTime", e.target.value)}
                />
                <span className="settings-hint faint">{t("settings.autoUpdate.top100Hint")}</span>
              </label>

              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.potentialScreenAutoEnabled}
                  onChange={(e) => set("potentialScreenAutoEnabled", e.target.checked)}
                />
                <span>{t("settings.autoUpdate.enablePotentialScreen")}</span>
              </label>
              <label className="settings-field">
                <span>{t("settings.autoUpdate.potentialScreenTime")}</span>
                <input
                  type="time"
                  value={form.potentialScreenTime}
                  disabled={!form.potentialScreenAutoEnabled}
                  onChange={(e) => set("potentialScreenTime", e.target.value)}
                />
                <span className="settings-hint faint">{t("settings.autoUpdate.potentialScreenHint")}</span>
              </label>
            </section>

            {form.strategy === "wyckoff" && (
              <>
                <section className="settings-section">
                  <h3>{t("settings.section.wyckoffThresholds")}</h3>
                  <p className="settings-hint faint">{t("settings.wyckoff.hint")}</p>
                  <div className="settings-grid">
                    <label className="settings-field">
                      <span>{t("settings.wyckoff.climaxVol")}</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.climaxVolMult}
                        onChange={(e) => set("climaxVolMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>{t("settings.wyckoff.wideSpread")}</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.wideSpreadMult}
                        onChange={(e) => set("wideSpreadMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>{t("settings.wyckoff.narrowSpread")}</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.narrowSpreadMult}
                        onChange={(e) => set("narrowSpreadMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>{t("settings.wyckoff.lowVol")}</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.lowVolMult}
                        onChange={(e) => set("lowVolMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>{t("settings.wyckoff.sosVol")}</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.sosVolMult}
                        onChange={(e) => set("sosVolMult", e.target.value)}
                      />
                    </label>
                  </div>
                </section>

                <section className="settings-section">
                  <h3>{t("settings.section.lpsEntry")}</h3>
                  <p className="settings-hint faint">{t("settings.lps.hint")}</p>
                  <label className="settings-field">
                    <span>{t("settings.lps.lookback")}</span>
                    <input
                      type="number"
                      min={2}
                      max={60}
                      value={form.lpsLookbackBars}
                      onChange={(e) => set("lpsLookbackBars", e.target.value)}
                    />
                  </label>
                </section>
              </>
            )}

            {form.strategy === "sonicr" && (
              <section className="settings-section">
                <h3>{t("settings.section.sonicrThresholds")}</h3>
                <p className="settings-hint faint">{t("settings.sonicr.hint")}</p>
                <div className="settings-grid">
                  <label className="settings-field">
                    <span>{t("settings.sonicr.dragonPeriod")}</span>
                    <input
                      type="number"
                      min={2}
                      max={200}
                      value={form.sonicrDragonPeriod}
                      onChange={(e) => set("sonicrDragonPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.sonicr.t3Fast")}</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrT3FastPeriod}
                      onChange={(e) => set("sonicrT3FastPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.sonicr.t3Slow")}</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrT3SlowPeriod}
                      onChange={(e) => set("sonicrT3SlowPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.sonicr.t3Vfactor")}</span>
                    <input
                      type="number"
                      step="0.1"
                      min={0.1}
                      max={1}
                      value={form.sonicrT3Vfactor}
                      onChange={(e) => set("sonicrT3Vfactor", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.sonicr.cciFast")}</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrCciFastPeriod}
                      onChange={(e) => set("sonicrCciFastPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.sonicr.cciSlow")}</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrCciSlowPeriod}
                      onChange={(e) => set("sonicrCciSlowPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.sonicr.pullbackLookback")}</span>
                    <input
                      type="number"
                      min={2}
                      max={60}
                      value={form.sonicrPullbackLookbackBars}
                      onChange={(e) => set("sonicrPullbackLookbackBars", e.target.value)}
                    />
                  </label>
                </div>
              </section>
            )}

            {form.strategy === "smc" && (
              <section className="settings-section">
                <h3>{t("settings.section.smcThresholds")}</h3>
                <p className="settings-hint faint">{t("settings.smc.hint")}</p>
                <div className="settings-grid">
                  <label className="settings-field">
                    <span>{t("settings.smc.swingLookback")}</span>
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={form.smcSwingLookback}
                      onChange={(e) => set("smcSwingLookback", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.smc.obLookback")}</span>
                    <input
                      type="number"
                      min={2}
                      max={30}
                      value={form.smcObLookbackBars}
                      onChange={(e) => set("smcObLookbackBars", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>{t("settings.smc.fvgMinGap")}</span>
                    <input
                      type="number"
                      step="0.1"
                      min={0.1}
                      value={form.smcFvgMinGapMult}
                      onChange={(e) => set("smcFvgMinGapMult", e.target.value)}
                    />
                  </label>
                </div>
              </section>
            )}

            <section className="settings-section">
              <h3>{t("settings.section.screener")}</h3>
              <p className="settings-hint faint">{t("settings.screener.hint")}</p>
              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.screenerEnabled}
                  onChange={(e) => set("screenerEnabled", e.target.checked)}
                />
                <span>{t("settings.screener.enableScheduled")}</span>
              </label>
              <div className="settings-grid">
                <label className="settings-field">
                  <span>{t("settings.screener.mcapMax")}</span>
                  <select
                    value={form.screenerMcapMax}
                    onChange={(e) => set("screenerMcapMax", e.target.value)}
                  >
                    {MCAP_OPTIONS.map((m) => (
                      <option key={m.value} value={m.value}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="settings-field">
                  <span>{t("settings.screener.scanInterval")}</span>
                  <select
                    value={form.screenerScanInterval}
                    disabled={!form.screenerEnabled}
                    onChange={(e) => set("screenerScanInterval", e.target.value)}
                  >
                    {SCAN_INTERVAL_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.screenerRequireVolumeRising}
                  onChange={(e) => set("screenerRequireVolumeRising", e.target.checked)}
                />
                <span>{t("settings.screener.requireVolumeRising")}</span>
              </label>
              <label className="settings-field">
                <span>{t("settings.screener.minVolumeChangePct")}</span>
                <input
                  type="number"
                  step="1"
                  min={1}
                  disabled={!form.screenerRequireVolumeRising}
                  value={form.screenerMinVolumeChangePct}
                  onChange={(e) => set("screenerMinVolumeChangePct", e.target.value)}
                />
                <span className="settings-hint faint">{t("settings.screener.minVolumeChangeHint")}</span>
              </label>
            </section>

            {error && <p className="settings-error">{error}</p>}
          </div>
        )}

        <footer className="settings-modal__footer">
          {savedAt && !error && <span className="settings-saved faint">{t("settings.saved")}</span>}
          <button className="btn" onClick={onClose}>
            {t("common.close")}
          </button>
          <button
            className={`btn btn--primary${isDirty ? " btn--dirty" : ""}`}
            onClick={() => void handleSave()}
            disabled={!form || saving}
          >
            {saving ? t("common.saving") : t("common.save")}
          </button>
        </footer>
      </div>
    </div>
  );
}
