export type Timeframe = "daily" | "half_session";

export interface SymbolItem {
  ticker: string;
  name: string;
  is_vn30: boolean;
  is_watchlist: boolean;
  added_at: string;
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
  as_of: string;
  phase: string;
  confidence: number;
  signals: Signal[];
  levels: Levels;
  narrative: string | null;
  advice: string | null;
  created_at: string;
}
