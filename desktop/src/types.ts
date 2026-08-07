export type Timeframe = "daily" | "half_session" | "1h" | "4h" | "1w";
export type AssetClass = "stock" | "crypto";

export interface SymbolItem {
  ticker: string;
  display_symbol: string;
  name: string;
  asset_class: AssetClass;
  is_vn30: boolean;
  exchange: "HOSE" | "HNX" | null;
  is_hose_hnx: boolean;
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
  // Wyckoff-only (see app.wyckoff.volume_profile on the backend) -- null for
  // SMC/SonicR events and for Wyckoff event types the engine doesn't check.
  volume_confirmed?: boolean | null;
  // SMC Order Blocks only (see app.smc.events.SMCEvent) -- the anchor
  // candle's own low/high, and whether price has already closed back
  // through the zone. null/false for every other event type.
  zone_low?: number | null;
  zone_high?: number | null;
  mitigated?: boolean;
  // SMC BOS/CHoCH only (see app.smc.events.SMCEvent) -- the swing high/low
  // this event broke: its own timestamp and price, so a chart can draw a
  // line from that swing point to this event's own (ts, price). null for
  // every other event type.
  structure_level_ts?: string | null;
  structure_level_price?: number | null;
}

export interface SmcZones {
  premium_low: number;
  discount_high: number;
  equilibrium_low: number;
  equilibrium_high: number;
  high_label: string;
  low_label: string;
}

export interface Levels {
  support: number;
  resistance: number;
  // Wyckoff-only Volume Profile fields -- null for SMC/SonicR or when there
  // isn't enough history yet to compute a profile.
  poc?: number | null;
  value_area_high?: number | null;
  value_area_low?: number | null;
  // SMC-only (see app.smc.zones on the backend) -- null for every other strategy.
  smc_zones?: SmcZones | null;
}

export interface DrawingPoint {
  time: string; // candle bucket_start ISO string, not a raw chart pixel
  price: number;
}

export interface DrawingShape {
  points: DrawingPoint[];
  color: string;
}

export interface TradeScenario {
  event_type: string;
  is_bullish: boolean;
  entry: number;
  stop_loss: number;
  take_profit: number;
  max_bars: number;
  status: "active" | "hit_tp" | "hit_sl" | "expired";
  explanation: string | null;
  close_reason: string | null;
}

export interface Analysis {
  ticker: string;
  timeframe: Timeframe;
  strategy: string;
  as_of: string;
  phase: string;
  confidence: number;
  signals: Signal[];
  actionable_signals: Signal[];
  levels: Levels;
  narrative: string | null;
  advice: string | null;
  sub_agents?: Array<{ name: string; role: string; model: string; status: string; output_length?: number }> | null;
  daily_trend: "bullish" | "bearish" | "neutral" | null;
  mtf_alignment: "aligned" | "conflicting" | null;
  vp_alignment?: "confirmed" | "unconfirmed" | null;
  created_at: string;
  scenario: TradeScenario | null;
}

export interface MoneyFlowEvent {
  type: "MoneyFlowIn" | "MoneyFlowOut";
  ts: string;
  price: number;
  volume_ratio: number;
  price_change_pct: number;
}

export interface MoneyFlowResult {
  net_signal: "inflow" | "outflow" | "neutral";
  recent_in_count: number;
  recent_out_count: number;
  recent_window: number;
  as_of: string | null;
  events: MoneyFlowEvent[];
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
  n_current_config?: number;
  significant_10: boolean | null;
  n_5: number;
  avg_return_5: number | null;
  win_rate_5: number | null;
  win_rate_5_ci: [number, number] | null;
  baseline_win_rate_5: number | null;
  edge_5: number | null;
  n_10: number;
  avg_return_10: number | null;
  win_rate_10: number | null;
  win_rate_10_ci: [number, number] | null;
  baseline_win_rate_10: number | null;
  edge_10: number | null;
  n_20: number;
  avg_return_20: number | null;
  win_rate_20: number | null;
  win_rate_20_ci: [number, number] | null;
  baseline_win_rate_20: number | null;
  edge_20: number | null;
}

export type NarrativeProvider = "anthropic" | "ollama" | "antigravity" | "codex";

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
  openai_model: string;
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
  potential_screen_auto_enabled: boolean;
  potential_screen_time: string;
  ai_narrative_vn30: boolean;
  ai_narrative_watchlist: boolean;
  ai_narrative_top100: boolean;
  shadow_strategy_keys: string[];
  notional_capital: number;
  risk_pct_per_trade: number;
  slippage_pct_stock: number;
  slippage_pct_crypto: number;
  trading_fee_pct_crypto: number;
  max_concurrent_scenarios: number;
  max_concurrent_scenarios_crypto: number;
  broker_fee_pct_stock: number;
  sell_tax_pct_stock: number;
  stock_daily_price_limit_pct: number;
  stock_daily_price_limit_pct_hnx: number;
  stock_min_avg_value_vnd: number;
  hose_hnx_auto_refresh_enabled: boolean;
  hose_hnx_refresh_time: string;
  stock_batch_analysis_auto_enabled: boolean;
  stock_batch_analysis_time: string;
  has_anthropic_key: boolean;
  has_gemini_key: boolean;
  has_openai_key: boolean;
}

export type SettingsUpdate = Partial<Omit<Settings, "has_anthropic_key" | "has_gemini_key" | "has_openai_key">> & {
  anthropic_api_key?: string;
  gemini_api_key?: string;
  openai_api_key?: string;
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

export interface PotentialScreenRow {
  ticker: string;
  display_symbol: string;
  name: string;
  asset_class: AssetClass;
  score: number;
  reason: string;
  updated_at: string;
}

export interface PotentialScreenStatus {
  running: boolean;
  total: number | null;
  scored: number | null;
  last_completed_at: string | null;
  last_error: string | null;
}

export interface StockBatchAnalysisStatus {
  running: boolean;
  total: number | null;
  completed: number | null;
  failed: number | null;
  current_ticker: string | null;
  last_error: string | null;
  last_cancelled: boolean;
  last_completed_at: string | null;
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

export type TradeHistorySource = "live" | "backtest" | "all";

export interface TradeHistoryEntry {
  id: number;
  ticker: string;
  timeframe: string;
  strategy: string;
  event_type: string;
  event_ts: string;
  is_bullish: boolean;
  entry: number;
  stop_loss: number;
  take_profit: number;
  max_bars: number;
  status: "active" | "hit_tp" | "hit_sl" | "expired";
  close_reason: string | null;
  closed_at: string | null;
  price_limit_caution: boolean;
  source: "live" | "backtest";
}

export interface TradeHistoryPage {
  items: TradeHistoryEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface TradeHistoryStats {
  total_count: number;
  current_config_count: number | null;
  decided_count: number;
  win_count: number;
  loss_count: number;
  win_rate: number | null;
  avg_pnl_pct: number | null;
  pnl_sample_count: number;
  expectancy_r: number | null;
  median_expectancy_r: number | null;
  low_sample_size: boolean | null;
  risk_amount_per_trade: number;
  total_pnl_amount: number | null;
  benchmark_buy_hold_pct: number | null;
  strategy_return_pct: number | null;
  edge_vs_buy_hold_pct: number | null;
  max_drawdown_r: number | null;
  max_drawdown_amount: number | null;
  max_consecutive_losses: number | null;
  monte_carlo: MonteCarloResult | null;
  bootstrap: BootstrapResult | null;
  walk_forward: WalkForwardResult | null;
}

export interface MonteCarloResult {
  actual_r_sharpe: number;
  actual_max_drawdown_r: number;
  p_value_r_sharpe: number;
  p_value_max_drawdown_r: number;
  simulated_r_sharpe_mean: number;
  simulated_r_sharpe_p5: number;
  simulated_r_sharpe_p95: number;
  n_simulations: number;
  n_trades: number;
}

export interface BootstrapResult {
  observed_r_sharpe: number;
  ci_lower: number;
  ci_upper: number;
  median_r_sharpe: number;
  prob_positive: number;
  confidence: number;
  n_bootstrap: number;
  n_trades: number;
}

export interface WalkForwardWindow {
  n_trades: number;
  expectancy_r: number | null;
}

export interface WalkForwardResult {
  per_window: WalkForwardWindow[];
  n_windows: number;
  positive_windows: number;
  consistency_ratio: number | null;
  expectancy_std_across_windows: number | null;
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
  | "crypto_analysis_refresh"
  | "potential_screen"
  | "hose_hnx_seed"
  | "stock_batch_analysis";

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
