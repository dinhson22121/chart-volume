// Presentation helpers for Wyckoff phases and signals. Labels themselves live
// in i18n/translations.ts ("phase.*"/"signal.*" keys) -- this module only
// keeps the non-translatable color mapping and the boolean classifiers.
import type { Language } from "../i18n/translations";
import { TRANSLATIONS } from "../i18n/translations";

const PHASE_COLOR: Record<string, string> = {
  Accumulation: "var(--phase-accumulation)",
  Markup: "var(--phase-markup)",
  Distribution: "var(--phase-distribution)",
  Markdown: "var(--phase-markdown)",
  Ranging: "var(--phase-ranging)",
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

export function phaseLabel(phase: string, language: Language = "vi"): string {
  return TRANSLATIONS[language][`phase.${phase}`] ?? phase;
}

export function signalLabel(type: string, language: Language = "vi"): string {
  return TRANSLATIONS[language][`signal.${type}`] ?? type;
}

export function signalIsBullish(type: string): boolean {
  return BULLISH_SIGNALS.has(type);
}

export function signalIsEntry(type: string): boolean {
  return ENTRY_SIGNALS.has(type);
}
