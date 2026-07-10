// Presentation helpers for Wyckoff phases and signals.

const PHASE_COLOR: Record<string, string> = {
  Accumulation: "var(--phase-accumulation)",
  Markup: "var(--phase-markup)",
  Distribution: "var(--phase-distribution)",
  Markdown: "var(--phase-markdown)",
  Ranging: "var(--phase-ranging)",
};

const PHASE_LABEL_VI: Record<string, string> = {
  Accumulation: "Tích lũy",
  Markup: "Tăng giá (Markup)",
  Distribution: "Phân phối",
  Markdown: "Giảm giá (Markdown)",
  Ranging: "Đi ngang",
  "Insufficient data": "Chưa đủ dữ liệu",
};

const SIGNAL_LABEL_VI: Record<string, string> = {
  SC: "Selling Climax — cao trào bán",
  BC: "Buying Climax — cao trào mua",
  Spring: "Spring — cú rũ bỏ",
  Upthrust: "Upthrust — cú vượt giả",
  SOS: "Sign of Strength — dấu hiệu mạnh",
  SOW: "Sign of Weakness — dấu hiệu yếu",
  NoDemand: "No Demand — thiếu cầu",
  NoSupply: "No Supply — thiếu cung",
  LPS: "LPS — điểm vào (pullback sau SOS)",
  LPSY: "LPSY — điểm vào (pullback sau SOW)",
  DragonCrossUp: "Dragon Cross Up — giá cắt lên Dragon EMA",
  DragonCrossDown: "Dragon Cross Down — giá cắt xuống Dragon EMA",
  SonicCrossUp: "Sonic Cross Up — T3 fast cắt lên T3 slow",
  SonicCrossDown: "Sonic Cross Down — T3 fast cắt xuống T3 slow",
  SonicEntryLong: "Sonic Entry Long — điểm vào mua (pullback + MTF khớp)",
  SonicEntryShort: "Sonic Entry Short — điểm vào bán (pullback + MTF khớp)",
};

const BULLISH_SIGNALS = new Set([
  "Spring", "SC", "SOS", "NoSupply", "LPS",
  "DragonCrossUp", "SonicCrossUp", "SonicEntryLong",
]);
// LPS/LPSY and SonicEntryLong/Short are confirmed entry points, not just raw
// detector signals -- given a distinct marker shape on the chart so they
// stand out from the rest.
const ENTRY_SIGNALS = new Set(["LPS", "LPSY", "SonicEntryLong", "SonicEntryShort"]);

export function phaseColor(phase: string): string {
  return PHASE_COLOR[phase] ?? "var(--phase-ranging)";
}

export function phaseLabel(phase: string): string {
  return PHASE_LABEL_VI[phase] ?? phase;
}

export function signalLabel(type: string): string {
  return SIGNAL_LABEL_VI[type] ?? type;
}

export function signalIsBullish(type: string): boolean {
  return BULLISH_SIGNALS.has(type);
}

export function signalIsEntry(type: string): boolean {
  return ENTRY_SIGNALS.has(type);
}
