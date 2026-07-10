import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { NarrativeProvider, OllamaStatus, Settings, SettingsUpdate, StrategyOption } from "../../types";
import "./settings.css";

interface Props {
  onClose: () => void;
}

const MODEL_OPTIONS = [
  { value: "claude-sonnet-4-5", label: "Claude Sonnet 4.5 (khuyến nghị)" },
  { value: "claude-opus-4-5", label: "Claude Opus 4.5 (mạnh hơn, đắt hơn)" },
  { value: "claude-haiku-4-5", label: "Claude Haiku 4.5 (nhanh, rẻ)" },
];

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

const MCAP_OPTIONS = [
  { value: "10000000", label: "Dưới 10 triệu $" },
  { value: "20000000", label: "Dưới 20 triệu $" },
  { value: "30000000", label: "Dưới 30 triệu $" },
  { value: "50000000", label: "Dưới 50 triệu $" },
];

const SCAN_INTERVAL_OPTIONS = [
  { value: "10m", label: "10 phút" },
  { value: "30m", label: "30 phút" },
  { value: "1h", label: "1 giờ" },
  { value: "4h", label: "4 giờ" },
  { value: "12h", label: "12 giờ" },
  { value: "1d", label: "1 ngày" },
];

// Local editable form shape: numbers become strings so inputs can hold
// intermediate/invalid text while typing, without fighting controlled-input state.
interface FormState {
  strategy: string;
  narrativeProvider: NarrativeProvider;
  anthropicApiKey: string;
  anthropicModel: string;
  ollamaModel: string;
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
  screenerEnabled: boolean;
  screenerMcapMax: string;
  screenerRequireVolumeRising: boolean;
  screenerMinVolumeChangePct: string;
  screenerScanInterval: string;
  cryptoAnalysisEnabled: boolean;
  cryptoAnalysisInterval: string;
}

function toForm(s: Settings): FormState {
  return {
    strategy: s.strategy,
    narrativeProvider: s.narrative_provider,
    anthropicApiKey: "",
    anthropicModel: s.anthropic_model,
    ollamaModel: s.ollama_model,
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
    screenerEnabled: s.screener_enabled,
    screenerMcapMax: String(s.screener_mcap_max),
    screenerRequireVolumeRising: s.screener_require_volume_rising,
    screenerMinVolumeChangePct: String(s.screener_min_volume_change_pct),
    screenerScanInterval: s.screener_scan_interval,
    cryptoAnalysisEnabled: s.crypto_analysis_enabled,
    cryptoAnalysisInterval: s.crypto_analysis_interval,
  };
}

function toUpdate(f: FormState): SettingsUpdate {
  const update: SettingsUpdate = {
    strategy: f.strategy,
    narrative_provider: f.narrativeProvider,
    anthropic_model: f.anthropicModel,
    ollama_model: f.ollamaModel,
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
    screener_enabled: f.screenerEnabled,
    screener_mcap_max: Number(f.screenerMcapMax),
    screener_require_volume_rising: f.screenerRequireVolumeRising,
    screener_min_volume_change_pct: Number(f.screenerMinVolumeChangePct),
    screener_scan_interval: f.screenerScanInterval,
    crypto_analysis_enabled: f.cryptoAnalysisEnabled,
    crypto_analysis_interval: f.cryptoAnalysisInterval,
  };
  if (f.anthropicApiKey.trim()) {
    update.anthropic_api_key = f.anthropicApiKey.trim();
  }
  return update;
}

export function SettingsModal({ onClose }: Props) {
  const [loaded, setLoaded] = useState<Settings | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [strategies, setStrategies] = useState<StrategyOption[]>([]);

  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [ollamaStatusLoading, setOllamaStatusLoading] = useState(false);
  const [pullModelName, setPullModelName] = useState("");
  const [pulling, setPulling] = useState(false);
  const [pullProgress, setPullProgress] = useState<string | null>(null);
  const [pullError, setPullError] = useState<string | null>(null);

  useEffect(() => {
    void api.getSettings().then((s) => {
      setLoaded(s);
      setForm(toForm(s));
    });
    void api.getStrategies().then(setStrategies);
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
      setError(e instanceof Error ? e.message : "Lưu thất bại");
    } finally {
      setSaving(false);
    }
  };

  const handlePullModel = async (modelOverride?: string) => {
    const model = (modelOverride ?? pullModelName).trim();
    if (!model) return;
    setPulling(true);
    setPullError(null);
    setPullProgress("Đang bắt đầu…");
    try {
      await api.pullOllamaModel(model, (event) => {
        if (event.error) {
          setPullError(event.error);
          return;
        }
        if (event.total && event.completed) {
          const pct = Math.round((event.completed / event.total) * 100);
          setPullProgress(`${event.status ?? "Đang tải"} — ${pct}%`);
        } else {
          setPullProgress(event.status ?? "Đang tải…");
        }
      });
      setPullProgress("Hoàn tất!");
      set("ollamaModel", model);
      setPullModelName("");
      await refreshOllamaStatus();
    } catch (e) {
      setPullError(e instanceof Error ? e.message : "Tải model thất bại");
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
      setError(e instanceof Error ? e.message : "Xoá key thất bại");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>Cài đặt</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </header>

        {!form ? (
          <div className="settings-modal__body faint">Đang tải…</div>
        ) : (
          <div className="settings-modal__body">
            <section className="settings-section">
              <h3>Chiến lược phân tích</h3>
              <label className="settings-field">
                <span>Phương pháp</span>
                <select value={form.strategy} onChange={(e) => set("strategy", e.target.value)}>
                  {strategies.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <span className="settings-hint faint">
                  Áp dụng cho toàn bộ mã. Mục ngưỡng nâng cao bên dưới chỉ hiện đúng phần của chiến lược
                  đang chọn.
                </span>
              </label>
            </section>

            <section className="settings-section">
              <h3>Nhận định AI</h3>
              <div className="tf-toggle settings-provider-toggle">
                <button
                  className={form.narrativeProvider === "anthropic" ? "is-active" : ""}
                  onClick={() => set("narrativeProvider", "anthropic")}
                >
                  Claude API
                </button>
                <button
                  className={form.narrativeProvider === "ollama" ? "is-active" : ""}
                  onClick={() => set("narrativeProvider", "ollama")}
                  disabled={!hasEnoughRamForLocalAI}
                  title={
                    hasEnoughRamForLocalAI
                      ? undefined
                      : `Máy cần tối thiểu ${MIN_RAM_GB_FOR_LOCAL_AI}GB RAM để chạy AI local (hiện có ~${totalMemGB}GB)`
                  }
                >
                  Ollama (local, miễn phí)
                </button>
              </div>
              {!hasEnoughRamForLocalAI && form.narrativeProvider === "anthropic" && (
                <p className="settings-hint faint">
                  Máy này có ~{totalMemGB}GB RAM, dưới mức tối thiểu ({MIN_RAM_GB_FOR_LOCAL_AI}GB) để chạy AI
                  local mượt — mục Ollama đang tắt.
                </p>
              )}

              {form.narrativeProvider === "anthropic" ? (
                <>
                  <label className="settings-field">
                    <span>API key {loaded?.has_anthropic_key && <em className="settings-badge">đã lưu</em>}</span>
                    <div className="settings-field__row">
                      <input
                        type="password"
                        placeholder={loaded?.has_anthropic_key ? "•••••••••••••••• (nhập để thay)" : "sk-ant-..."}
                        value={form.anthropicApiKey}
                        onChange={(e) => set("anthropicApiKey", e.target.value)}
                      />
                      {loaded?.has_anthropic_key && (
                        <button className="btn" onClick={() => void handleClearKey()} disabled={saving}>
                          Xoá
                        </button>
                      )}
                    </div>
                    <span className="settings-hint faint">
                      Mã hoá khi lưu vào máy. Không có key thì vẫn phân tích Wyckoff định lượng, chỉ thiếu nhận định AI.
                    </span>
                  </label>
                  <label className="settings-field">
                    <span>Model</span>
                    <select value={form.anthropicModel} onChange={(e) => set("anthropicModel", e.target.value)}>
                      {MODEL_OPTIONS.map((m) => (
                        <option key={m.value} value={m.value}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              ) : (
                <div className="settings-ollama">
                  {!hasEnoughRamForLocalAI && (
                    <p className="settings-hint settings-hint--warn">
                      Máy này có ~{totalMemGB}GB RAM, dưới mức tối thiểu ({MIN_RAM_GB_FOR_LOCAL_AI}GB) khuyến
                      nghị để chạy AI local — có thể rất chậm hoặc treo máy khi phân tích.
                    </p>
                  )}
                  <div className="settings-ollama__status">
                    {ollamaStatusLoading ? (
                      <span className="faint">Đang kiểm tra Ollama…</span>
                    ) : ollamaStatus?.available ? (
                      <span className="settings-badge settings-badge--ok">● Ollama đang chạy</span>
                    ) : (
                      <span className="settings-badge settings-badge--off">
                        ● Không kết nối được Ollama
                      </span>
                    )}
                    <button className="btn" onClick={() => void refreshOllamaStatus()} disabled={ollamaStatusLoading}>
                      Kiểm tra lại
                    </button>
                  </div>

                  {!ollamaStatusLoading && !ollamaStatus?.available && (
                    <div className="settings-wizard-card">
                      <p className="settings-wizard-card__title">Bước 1 — Cài Ollama</p>
                      <p className="settings-hint faint">
                        Chưa phát hiện Ollama trên máy. Đây là app chạy AI local, miễn phí, riêng tư — dữ
                        liệu không rời khỏi máy bạn.
                      </p>
                      <button className="btn btn--primary" onClick={() => openExternal(OLLAMA_DOWNLOAD_URL)}>
                        Tải Ollama (mở trang)
                      </button>
                      <p className="settings-hint faint">
                        Cài xong, mở app Ollama lên rồi bấm nút bên dưới.
                      </p>
                      <button className="btn" onClick={() => void refreshOllamaStatus()} disabled={ollamaStatusLoading}>
                        Kiểm tra lại
                      </button>
                    </div>
                  )}

                  {ollamaStatus?.available && ollamaStatus.models.length === 0 && (
                    <div className="settings-wizard-card">
                      <p className="settings-wizard-card__title">✅ Ollama đang chạy — Bước 2, cài model</p>
                      <p className="settings-hint faint">Chưa có model nào được cài.</p>
                      <button
                        className="btn btn--primary"
                        onClick={() => void handlePullModel(recommendedModel.name)}
                        disabled={pulling}
                      >
                        {pulling ? "Đang tải…" : `Cài model khuyến nghị — ${recommendedModel.name} (${recommendedModel.sizeLabel})`}
                      </button>
                      {pullProgress && !pullError && <p className="settings-hint faint">{pullProgress}</p>}
                      {pullError && <p className="settings-error">{pullError}</p>}
                    </div>
                  )}

                  {ollamaStatus?.models && ollamaStatus.models.length > 0 && (
                    <label className="settings-field">
                      <span>Model đã cài</span>
                      <select value={form.ollamaModel} onChange={(e) => set("ollamaModel", e.target.value)}>
                        <option value="">— chọn model —</option>
                        {ollamaStatus.models.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}

                  <label className="settings-field">
                    <span>Tải model mới (gõ tên, vd qwen2.5:7b)</span>
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
                        {pulling ? "Đang tải…" : "Tải model"}
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
            </section>

            <section className="settings-section">
              <h3>Độ sâu crawl</h3>
              <div className="settings-grid">
                <label className="settings-field">
                  <span>Nến ngày (số ngày lịch sử)</span>
                  <input
                    type="number"
                    min={30}
                    max={3650}
                    value={form.dailyLookbackDays}
                    onChange={(e) => set("dailyLookbackDays", e.target.value)}
                  />
                </label>
                <label className="settings-field">
                  <span>Nửa phiên (số ngày lịch sử)</span>
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
              <h3>Tự động cập nhật</h3>
              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.schedulerEnabled}
                  onChange={(e) => set("schedulerEnabled", e.target.checked)}
                />
                <span>Bật tự động crawl + phân tích theo phiên</span>
              </label>
              <div className="settings-grid">
                <label className="settings-field">
                  <span>Sau phiên sáng</span>
                  <input
                    type="time"
                    value={form.halfMorningTime}
                    disabled={!form.schedulerEnabled}
                    onChange={(e) => set("halfMorningTime", e.target.value)}
                  />
                </label>
                <label className="settings-field">
                  <span>Sau phiên chiều</span>
                  <input
                    type="time"
                    value={form.halfAfternoonTime}
                    disabled={!form.schedulerEnabled}
                    onChange={(e) => set("halfAfternoonTime", e.target.value)}
                  />
                </label>
                <label className="settings-field">
                  <span>Sau khi đóng cửa (nến ngày)</span>
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
                <span>Bật tự động phân tích lại crypto đang theo dõi</span>
              </label>
              <label className="settings-field">
                <span>Chu kỳ phân tích lại crypto</span>
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
                <span className="settings-hint faint">
                  Áp dụng cho mọi coin đang theo dõi, cả 3 khung 1h/4h/ngày — riêng biệt với lịch quét coin
                  mới ở mục Screener bên dưới.
                </span>
              </label>
            </section>

            {form.strategy === "wyckoff" && (
              <>
                <section className="settings-section">
                  <h3>Ngưỡng Wyckoff</h3>
                  <p className="settings-hint faint">
                    Nâng cao — hệ số so với trung bình động (volume/spread) để nhận diện tín hiệu. Để mặc định nếu không chắc.
                  </p>
                  <div className="settings-grid">
                    <label className="settings-field">
                      <span>Volume cao trào (x lần TB)</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.climaxVolMult}
                        onChange={(e) => set("climaxVolMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>Spread rộng (x lần TB)</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.wideSpreadMult}
                        onChange={(e) => set("wideSpreadMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>Spread hẹp (x lần TB)</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.narrowSpreadMult}
                        onChange={(e) => set("narrowSpreadMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>Volume thấp (x lần TB)</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0.1}
                        value={form.lowVolMult}
                        onChange={(e) => set("lowVolMult", e.target.value)}
                      />
                    </label>
                    <label className="settings-field">
                      <span>Volume bứt phá SOS/SOW (x lần TB)</span>
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
                  <h3>Điểm vào lệnh (LPS/LPSY)</h3>
                  <p className="settings-hint faint">
                    Sau khi có SOS/SOW, hệ thống chờ giá pullback về test lại vùng vừa gãy với volume thấp —
                    đó là điểm vào an toàn hơn (LPS cho long, LPSY cho short) thay vì vào ngay lúc breakout.
                  </p>
                  <label className="settings-field">
                    <span>Số nến tối đa chờ pullback sau SOS/SOW</span>
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
                <h3>Ngưỡng Sonic R</h3>
                <p className="settings-hint faint">
                  Nâng cao — chu kỳ Dragon EMA/T3/CCI và số nến chờ pullback về test lại Dragon trước khi
                  vào lệnh (yêu cầu khớp xu hướng khung ngày). Để mặc định nếu không chắc.
                </p>
                <div className="settings-grid">
                  <label className="settings-field">
                    <span>Chu kỳ Dragon EMA</span>
                    <input
                      type="number"
                      min={2}
                      max={200}
                      value={form.sonicrDragonPeriod}
                      onChange={(e) => set("sonicrDragonPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>Chu kỳ T3 fast</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrT3FastPeriod}
                      onChange={(e) => set("sonicrT3FastPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>Chu kỳ T3 slow</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrT3SlowPeriod}
                      onChange={(e) => set("sonicrT3SlowPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>Hệ số T3 (vfactor)</span>
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
                    <span>Chu kỳ CCI fast</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrCciFastPeriod}
                      onChange={(e) => set("sonicrCciFastPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>Chu kỳ CCI slow</span>
                    <input
                      type="number"
                      min={2}
                      max={100}
                      value={form.sonicrCciSlowPeriod}
                      onChange={(e) => set("sonicrCciSlowPeriod", e.target.value)}
                    />
                  </label>
                  <label className="settings-field">
                    <span>Số nến tối đa chờ pullback về Dragon</span>
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

            <section className="settings-section">
              <h3>Screener Crypto</h3>
              <p className="settings-hint faint">
                Tự động (và thủ công qua mục "Crypto mới" trong danh sách theo dõi) tìm coin vốn hóa nhỏ —
                nguồn lọc CoinGecko. Chọn sàn lấy nến (Binance/KuCoin) ngay trong mục "Crypto mới". Một
                lần quét mất vài phút do giới hạn API.
              </p>
              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={form.screenerEnabled}
                  onChange={(e) => set("screenerEnabled", e.target.checked)}
                />
                <span>Bật tự động quét theo lịch</span>
              </label>
              <div className="settings-grid">
                <label className="settings-field">
                  <span>Vốn hóa tối đa</span>
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
                  <span>Chu kỳ quét tự động</span>
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
                <span>Chỉ lấy coin đang tăng volume</span>
              </label>
              <label className="settings-field">
                <span>% volume tăng tối thiểu</span>
                <input
                  type="number"
                  step="1"
                  min={1}
                  disabled={!form.screenerRequireVolumeRising}
                  value={form.screenerMinVolumeChangePct}
                  onChange={(e) => set("screenerMinVolumeChangePct", e.target.value)}
                />
                <span className="settings-hint faint">
                  Tắt tuỳ chọn trên để liệt kê mọi coin trong khoảng vốn hóa, bất kể volume.
                </span>
              </label>
            </section>

            {error && <p className="settings-error">{error}</p>}
          </div>
        )}

        <footer className="settings-modal__footer">
          {savedAt && !error && <span className="settings-saved faint">Đã lưu</span>}
          <button className="btn" onClick={onClose}>
            Đóng
          </button>
          <button
            className={`btn btn--primary${isDirty ? " btn--dirty" : ""}`}
            onClick={() => void handleSave()}
            disabled={!form || saving}
          >
            {saving ? "Đang lưu…" : "Lưu"}
          </button>
        </footer>
      </div>
    </div>
  );
}
