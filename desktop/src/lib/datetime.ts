import type { Language } from "../i18n/translations";

// Consolidates the date/time formatting that used to be duplicated as a
// near-identical local `formatDate` in AnalysisPanel, DashboardModal,
// TracePanel and ActivityLogModal (all hardcoded to "vi-VN").
function locale(language: Language): string {
  return language === "en" ? "en-US" : "vi-VN";
}

export function formatDateTime(iso: string, language: Language): string {
  return new Date(iso).toLocaleString(locale(language), { dateStyle: "medium", timeStyle: "short" });
}

export function formatDateTimeMedium(iso: string, language: Language): string {
  return new Date(iso).toLocaleString(locale(language), { dateStyle: "medium", timeStyle: "medium" });
}

export function formatTime(epochMs: number, language: Language): string {
  return new Date(epochMs).toLocaleTimeString(locale(language));
}

export function formatDateOnly(iso: string, language: Language): string {
  return new Date(iso).toLocaleDateString(locale(language));
}
