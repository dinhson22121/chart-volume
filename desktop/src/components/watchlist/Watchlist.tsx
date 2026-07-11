import { useMemo, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { SymbolItem } from "../../types";
import { CryptoDiscovery } from "./CryptoDiscovery";
import { formatTime } from "../../lib/datetime";
import { useI18n } from "../../i18n/I18nContext";
import "./watchlist.css";

export type WatchlistTab = "vn30" | "crypto";

interface Props {
  symbols: SymbolItem[];
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => void;
  onRemove: (ticker: string) => void;
  onSeeded: () => void;
  onCryptoPromoted: (ticker: string) => void;
  activeTab: WatchlistTab;
  onTabChange: (tab: WatchlistTab) => void;
  busy: boolean;
}

interface SeedResult {
  completedAt: number;
  count: number;
  source: "live" | "fallback";
}

export function Watchlist({
  symbols,
  selected,
  onSelect,
  onAdd,
  onRemove,
  onSeeded,
  onCryptoPromoted,
  activeTab,
  onTabChange,
  busy,
}: Props) {
  const { t, language } = useI18n();
  const [input, setInput] = useState("");
  // Self-contained like CryptoDiscovery's own scan state, rather than routed
  // through App.tsx's generic busy flag -- so seeding gets its own status
  // line/progress bar/error display instead of just a disabled button.
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);
  const [lastSeed, setLastSeed] = useState<SeedResult | null>(null);

  const { vn30, watchlist } = useMemo(() => {
    const vn30 = symbols.filter((s) => s.is_vn30).sort((a, b) => a.ticker.localeCompare(b.ticker));
    const watchlist = symbols
      .filter((s) => s.is_watchlist && !s.is_vn30)
      .sort((a, b) => a.ticker.localeCompare(b.ticker));
    return { vn30, watchlist };
  }, [symbols]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const ticker = input.trim().toUpperCase();
    if (ticker) {
      onAdd(ticker);
      setInput("");
    }
  };

  const handleSeedVn30 = async () => {
    setSeeding(true);
    setSeedError(null);
    try {
      const result = await api.seedVn30();
      setLastSeed({ completedAt: Date.now(), count: result.count, source: result.source });
      onSeeded();
    } catch (e) {
      setSeedError(e instanceof Error ? e.message : t("watchlist.seed.error"));
    } finally {
      setSeeding(false);
    }
  };

  const renderRow = (s: SymbolItem, removable: boolean, card = false) => (
    <li key={s.ticker}>
      <button
        className={`${card ? "wl-crypto-card wl-row-card" : "wl-row"} ${
          selected === s.ticker ? "is-selected" : ""
        }`}
        onClick={() => onSelect(s.ticker)}
      >
        {card ? (
          <>
            <div className="wl-crypto-card__row1">
              <span className="wl-row__ticker mono">{s.display_symbol}</span>
              {removable && (
                <span
                  className="wl-row__remove"
                  role="button"
                  aria-label={t("watchlist.remove.ariaLabel", { ticker: s.display_symbol })}
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemove(s.ticker);
                  }}
                >
                  ×
                </span>
              )}
            </div>
            {s.name && <div className="wl-crypto-card__row2 faint">{s.name}</div>}
          </>
        ) : (
          <>
            <span className="wl-row__ticker mono">{s.display_symbol}</span>
            {s.asset_class === "crypto" && <span title="Crypto">🪙</span>}
            {s.name && <span className="wl-row__name faint">{s.name}</span>}
            {removable && (
              <span
                className="wl-row__remove"
                role="button"
                aria-label={t("watchlist.remove.ariaLabel", { ticker: s.display_symbol })}
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(s.ticker);
                }}
              >
                ×
              </span>
            )}
          </>
        )}
      </button>
    </li>
  );

  return (
    <div className="watchlist">
      <form className="wl-add" onSubmit={submit}>
        <input
          className="wl-add__input mono"
          placeholder={t("watchlist.addPlaceholder")}
          value={input}
          maxLength={8}
          onChange={(e) => setInput(e.target.value)}
        />
        <button type="submit" className="wl-add__btn" disabled={busy}>
          +
        </button>
      </form>

      <div className="wl-scroll">
        {watchlist.length > 0 && (
          <section className="wl-group">
            <h3 className="wl-group__title">{t("watchlist.section.tracked")}</h3>
            <ul>{watchlist.map((s) => renderRow(s, true))}</ul>
          </section>
        )}

        <section className="wl-group">
          <div className="wl-tabs">
            <button
              className={activeTab === "vn30" ? "is-active" : ""}
              onClick={() => onTabChange("vn30")}
            >
              {t("watchlist.tab.vn30")}
            </button>
            <button
              className={activeTab === "crypto" ? "is-active" : ""}
              onClick={() => onTabChange("crypto")}
            >
              {t("watchlist.tab.crypto")}
            </button>
          </div>

          {activeTab === "vn30" ? (
            <div className="wl-accordion__body">
              <div className="wl-scanbar">
                <span className="wl-status faint">
                  {seeding
                    ? t("watchlist.seed.loading")
                    : seedError
                      ? t("watchlist.seed.errorStatus")
                      : lastSeed
                        ? `${lastSeed.source === "fallback" ? t("watchlist.seed.fallbackPrefix") : ""}${t(
                            "watchlist.seed.doneAt",
                            { time: formatTime(lastSeed.completedAt, language), count: lastSeed.count },
                          )}`
                        : t("watchlist.seed.never")}
                </span>
                <button className="wl-seed" onClick={() => void handleSeedVn30()} disabled={seeding || busy}>
                  {seeding ? t("watchlist.seed.buttonLoading") : t("watchlist.seed.button")}
                </button>
              </div>

              {seeding && (
                <div className="wl-progress" role="progressbar" aria-label={t("watchlist.seed.loading")}>
                  <div className="wl-progress-fill" />
                </div>
              )}

              {seedError && <p className="wl-error">{seedError}</p>}

              {vn30.length === 0 && !seeding ? (
                <p className="wl-empty faint">{t("watchlist.empty")}</p>
              ) : (
                <ul className="wl-list--scroll wl-list--cards">{vn30.map((s) => renderRow(s, false, true))}</ul>
              )}
            </div>
          ) : (
            <CryptoDiscovery onPromoted={onCryptoPromoted} />
          )}
        </section>
      </div>
    </div>
  );
}
