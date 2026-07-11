import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { TRANSLATIONS, type Language } from "./translations";

interface I18nContextValue {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  // Defaults to Vietnamese (matches the app's existing behavior) until the
  // persisted Settings value loads -- no flash for existing VN users, the
  // common case.
  const [language, setLanguageState] = useState<Language>("vi");

  useEffect(() => {
    void api.getSettings().then((s) => {
      if (s.language === "en" || s.language === "vi") setLanguageState(s.language);
    });
  }, []);

  const setLanguage = (lang: Language) => {
    setLanguageState(lang); // apply immediately, don't wait on the network
    void api.updateSettings({ language: lang });
  };

  const t = useMemo(() => {
    const dict = TRANSLATIONS[language];
    return (key: string, vars?: Record<string, string | number>) => {
      const template = dict[key];
      if (template === undefined) return key; // safe fallback, never throws
      return interpolate(template, vars);
    };
  }, [language]);

  const value = useMemo(() => ({ language, setLanguage, t }), [language, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within an I18nProvider");
  return ctx;
}
