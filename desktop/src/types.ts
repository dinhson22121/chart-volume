export type Timeframe = "daily" | "half_session" | "1h" | "4h";
export type AssetClass = "stock" | "crypto";

export interface SymbolItem {
  ticker: string;
  display_symbol: string;
  name: string;
  asset_class: AssetClass;
  is_vn30: boolean;
  is_watchlist: boolean;
  is_top100: boolean;
  top100_rank: number | null;
  added_at: string;
}

export interface SeedVn30Result {
  count: number;
  source: "live" | "fallback";
}

export interface Candle {
  ticker: string;
  timeframe: string;
  session_part: string | null;
  bucket_start: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Signal {
  type: string;
  ts: string | null;
  price: number;
  note: string;
}

export interface Levels {
  support: number;
  resistance: number;
}

export interface Analysis {
  ticker: string;
  timeframe: Timeframe;
  strategy: string;
  as_of: string;
  phase: string;
  confidence: number;
  signals: Signal[];
  levels: Levels;
  narrative: string | null;
  advice: string | null;
  sub_agents?: Array<{ name: string; role: string; model: string; status: string; output_length?: number }> | null;
  daily_trend: "bullish" | "bearish" | "neutral" | null;
  mtf_alignment: "aligned" | "conflicting" | null;
  created_at: string;
}

export interface TraceCheck {
  label: string;
  passed: boolean;
  detail: string;
}

export interface TraceDetector {
  type: string;
  matched: boolean;
  checks: TraceCheck[];
}

export interface BarTrace {
  ticker: string;
  timeframe: Timeframe;
  bar_ts: string;
  detectors: TraceDetector[];
}

export interface SignalStat {
  type: string;
  count: number;
  is_bullish: boolean;
  n_5: number;
  avg_return_5: number | null;
  win_rate_5: number | null;
  n_10: number;
  avg_return_10: number | null;
  win_rate_10: number | null;
  n_20: number;
  avg_return_20: number | null;
  win_rate_20: number | null;
}

export type NarrativeProvider = "anthropic" | "ollama" | "antigravity";

export interface StrategyOption {
  key: string;
  label: string;
}

export interface Settings {
  language: "vi" | "en";
  strategy: string;
  narrative_provider: NarrativeProvider;
  anthropic_model: string;
  ollama_model: string;
  antigravity_model: string;
  daily_lookback_days: number;
  half_session_lookback_days: number;
  scheduler_enabled: boolean;
  half_morning_time: string;
  half_afternoon_time: string;
  daily_time: string;
  climax_vol_mult: number;
  wide_spread_mult: number;
  narrow_spread_mult: number;
  low_vol_mult: number;
  sos_vol_mult: number;
  lps_lookback_bars: number;
  sonicr_dragon_period: number;
  sonicr_t3_fast_period: number;
  sonicr_t3_slow_period: number;
  sonicr_t3_vfactor: number;
  sonicr_cci_fast_period: number;
  sonicr_cci_slow_period: number;
  sonicr_pullback_lookback_bars: number;
  smc_swing_lookback: number;
  smc_ob_lookback_bars: number;
  smc_fvg_min_gap_mult: number;
  screener_enabled: boolean;
  screener_mcap_max: number;
  screener_require_volume_rising: boolean;
  screener_min_volume_change_pct: number;
  screener_scan_interval: string;
  crypto_exchanges: string[];
  crypto_analysis_enabled: boolean;
  crypto_analysis_interval: string;
  top100_auto_refresh_enabled: boolean;
  top100_refresh_time: string;
  ai_narrative_vn30: boolean;
  ai_narrative_watchlist: boolean;
  ai_narrative_top100: boolean;
  has_anthropic_key: boolean;
  has_gemini_key: boolean;
}

export type SettingsUpdate = Partial<Omit<Settings, "has_anthropic_key" | "has_gemini_key">> & {
  anthropic_api_key?: string;
  gemini_api_key?: string;
};

export interface OllamaStatus {
  available: boolean;
  models: string[];
}

export interface OllamaPullEvent {
  status?: string;
  completed?: number;
  total?: number;
  error?: string;
}

export interface ScreenerCandidate {
  coin_id: string;
  symbol: string;
  name: string;
  market_cap: number;
  volume_24h: number;
  volume_change_pct: number | null;
  last_seen_at: string;
  source: "coingecko" | "geckoterminal";
  network: string | null;
  exchange: "binance" | "kucoin" | "mexc" | null;
}

export type CandidateSort = "volume_change" | "market_cap";

export interface CandidatesPage {
  items: ScreenerCandidate[];
  total: number;
  page: number;
  page_size: number;
}

export interface ScanStatus {
  running: boolean;
  last_completed_at: string | null;
  last_hits: number | null;
  last_error: string | null;
  last_cancelled: boolean;
  phase: "coingecko" | "dex_pools" | null;
  current_page: number | null;
  hits_so_far: number | null;
}

export interface ConfigChangeLogEntry {
  id: number;
  changed_at: string;
  key: string;
  old_value: string;
  new_value: string;
}

export interface ConfigLogPage {
  items: ConfigChangeLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

export type SystemAction =
  | "screener_scan"
  | "vn30_seed"
  | "top100_seed"
  | "half_session_morning"
  | "half_session_afternoon"
  | "daily_close"
  | "crypto_analysis_refresh";

export interface SystemActionLogEntry {
  id: number;
  action: SystemAction;
  trigger: "manual" | "scheduled";
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "error" | "cancelled";
  detail: string | null;
}

export interface SystemLogPage {
  items: SystemActionLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface DashboardSignal {
  type: string;
  ts: string;
}

export interface IndicatorPoint {
  ts: string;
  value: number;
}

export interface IndicatorSeries {
  dragon: IndicatorPoint[];
  t3_fast: IndicatorPoint[];
  t3_slow: IndicatorPoint[];
}

export interface DashboardRow {
  ticker: string;
  display_symbol: string;
  name: string;
  asset_class: AssetClass;
  phase: string | null;
  confidence: number | null;
  as_of: string | null;
  latest_signal: DashboardSignal | null;
  has_data: boolean;
  is_bullish: boolean | null;
  opportunity_score: number | null;
}
