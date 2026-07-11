import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { CandidateSort, ScanStatus, ScreenerCandidate } from "../../types";
import { formatDateOnly } from "../../lib/datetime";
import { useI18n } from "../../i18n/I18nContext";

interface Props {
  onPromoted: (ticker: string) => void;
}

const POLL_MS = 2500;
const PAGE_SIZE = 50;
const SCROLL_THRESHOLD_PX = 40;
const SEARCH_DEBOUNCE_MS = 300;

const EXCHANGE_LABEL: Record<string, string> = { binance: "Binance", kucoin: "KuCoin", mexc: "MEXC" };

function formatUsd(v: number): string {
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(2)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

function formatPct(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function CryptoDiscovery({ onPromoted }: Props) {
  const { t, language } = useI18n();
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [sort, setSort] = useState<CandidateSort>("volume_change");
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [exchangeFilter, setExchangeFilter] = useState("");
  const [candidates, setCandidates] = useState<ScreenerCandidate[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [promoting, setPromoting] = useState<string | null>(null);
  const [exchanges, setExchanges] = useState<string[] | null>(null);
  const [savingExchanges, setSavingExchanges] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const EXCHANGE_OPTIONS = [
    { value: "binance", label: "Binance" },
    { value: "kucoin", label: "KuCoin" },
    { value: "mexc", label: "MEXC" },
    { value: "geckoterminal", label: t("crypto.exchange.geckoterminal") },
  ];

  const SORT_OPTIONS: { value: CandidateSort; label: string }[] = [
    { value: "volume_change", label: t("crypto.sort.volume") },
    { value: "market_cap", label: t("crypto.sort.marketCap") },
  ];

  // "" means no filter (Tất cả/All). Values match CryptoExchange on the
  // backend -- "geckoterminal" filters by source (DEX pool hits have no
  // resolved exchange).
  const EXCHANGE_FILTER_OPTIONS = [
    { value: "", label: t("crypto.filter.all") },
    { value: "binance", label: "Binance" },
    { value: "kucoin", label: "KuCoin" },
    { value: "mexc", label: "MEXC" },
    { value: "geckoterminal", label: t("crypto.filter.dex") },
  ];

  const scanPhaseLabel = (phase: ScanStatus["phase"]): string =>
    phase === "dex_pools" ? t("crypto.scanPhase.dex") : t("crypto.scanPhase.coingecko");

  // Multiple different coins can share a ticker symbol (e.g. many different
  // "pepe" clones) -- the source label shows where this coin's candles would
  // actually come from. A resolved real exchange (Binance/KuCoin/MEXC) is
  // preferred when known at scan time -- "CoinGecko" is only the discovery
  // source, not where candles get fetched from, so it's not informative
  // enough on its own. The full name (c.name, shown separately on the card)
  // is what actually distinguishes same-symbol coins from each other.
  const sourceLabel = (c: ScreenerCandidate): string => {
    if (c.source === "geckoterminal") return `DEX${c.network ? ` · ${c.network}` : ""}`;
    if (c.exchange) return EXCHANGE_LABEL[c.exchange] ?? c.exchange;
    return t("crypto.sourceUnknown");
  };

  // There's no reliable upfront "total pages" to compute a real percentage from
  // (CoinGecko doesn't report one, and GeckoTerminal's page count varies), so
  // the running line shows live page/hit counts instead -- paired with an
  // indeterminate progress bar to signal "still working".
  const scanningLabel = (s: ScanStatus): string => {
    const parts = [t("crypto.scanning", { phase: scanPhaseLabel(s.phase) })];
    if (s.current_page != null) parts.push(t("crypto.scanningPage", { page: s.current_page }));
    if (s.hits_so_far != null) parts.push(t("crypto.scanningHits", { count: s.hits_so_far }));
    return parts.join(" — ");
  };

  const loadFirstPage = useCallback(async (sortValue: CandidateSort, query: string, exchangeValue: string) => {
    try {
      const res = await api.getScreenerCandidates(sortValue, 1, PAGE_SIZE, query || undefined, exchangeValue || undefined);
      setCandidates(res.items);
      setTotal(res.total);
      setPage(1);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("crypto.error.loadCandidates"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.getScreenerStatus();
      setStatus(s);
      return s;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    void loadFirstPage("volume_change", "", "");
    void api.getSettings().then((s) => setExchanges(s.crypto_exchanges));
    // Only on mount -- sort/search/exchange changes are handled by their own handlers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!status?.running) {
      setCancelling(false); // scan actually stopped -- clear the "cancelling" state
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    pollRef.current = setInterval(() => {
      // Reload every tick, not just once at completion, so newly-found
      // candidates appear while the scan is still running.
      void refreshStatus().then(() => void loadFirstPage(sort, searchQuery, exchangeFilter));
    }, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [status?.running, refreshStatus, loadFirstPage, sort, searchQuery, exchangeFilter]);

  useEffect(() => {
    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, []);

  const loadNextPage = async () => {
    if (loadingMore || !candidates || candidates.length >= total) return;
    setLoadingMore(true);
    try {
      const nextPage = page + 1;
      const res = await api.getScreenerCandidates(
        sort, nextPage, PAGE_SIZE, searchQuery || undefined, exchangeFilter || undefined,
      );
      setCandidates((prev) => [...(prev ?? []), ...res.items]);
      setPage(nextPage);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("crypto.error.loadMore"));
    } finally {
      setLoadingMore(false);
    }
  };

  const handleScroll = (e: React.UIEvent<HTMLUListElement>) => {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD_PX) {
      void loadNextPage();
    }
  };

  const handleSortChange = (value: CandidateSort) => {
    setSort(value);
    void loadFirstPage(value, searchQuery, exchangeFilter);
  };

  const handleSearchChange = (value: string) => {
    setSearchInput(value);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      setSearchQuery(value);
      void loadFirstPage(sort, value, exchangeFilter);
    }, SEARCH_DEBOUNCE_MS);
  };

  const handleExchangeFilterChange = (value: string) => {
    setExchangeFilter(value);
    void loadFirstPage(sort, searchQuery, value);
  };

  const handleScan = async () => {
    setError(null);
    try {
      await api.triggerScreenerScan();
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("crypto.error.scanTrigger"));
    }
  };

  const handleCancel = async () => {
    setCancelling(true); // immediate feedback -- the scan itself can take a moment to actually stop
    try {
      await api.cancelScreenerScan();
      await refreshStatus();
    } catch (e) {
      setCancelling(false);
      setError(e instanceof Error ? e.message : t("crypto.error.cancel"));
    }
  };

  const handlePromote = async (coinId: string) => {
    setPromoting(coinId);
    setError(null);
    try {
      const result = await api.promoteCandidate(coinId);
      onPromoted(result.ticker);
      await loadFirstPage(sort, searchQuery, exchangeFilter);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("crypto.error.promote"));
    } finally {
      setPromoting(null);
    }
  };

  const toggleExchange = async (exchange: string, checked: boolean) => {
    if (!exchanges) return;
    if (!checked && exchanges.length === 1) return; // keep at least one enabled
    const prev = exchanges;
    const next = checked ? [...exchanges, exchange] : exchanges.filter((e) => e !== exchange);
    setExchanges(next);
    setSavingExchanges(true);
    try {
      await api.updateSettings({ crypto_exchanges: next });
    } catch (e) {
      setExchanges(prev);
      setError(e instanceof Error ? e.message : t("crypto.error.saveExchanges"));
    } finally {
      setSavingExchanges(false);
    }
  };

  return (
    <div className="wl-accordion__body">
      <div className="wl-crypto__exchange-box">
        <div className="wl-crypto__exchanges">
          <span className="faint">{t("crypto.findCoin")}</span>
          <label className="wl-crypto__exchange">
            <input type="checkbox" checked disabled />
            CoinGecko
          </label>
        </div>

        <div className="wl-crypto__exchanges">
          <span className="faint">{t("crypto.exchangeDex")}</span>
          {EXCHANGE_OPTIONS.map((ex) => (
            <label key={ex.value} className="wl-crypto__exchange">
              <input
                type="checkbox"
                checked={exchanges?.includes(ex.value) ?? false}
                disabled={!exchanges || savingExchanges}
                onChange={(e) => void toggleExchange(ex.value, e.target.checked)}
              />
              {ex.label}
            </label>
          ))}
        </div>
      </div>
      {exchanges?.includes("geckoterminal") && (
        <p className="wl-crypto__hint faint">{t("crypto.geckoterminalHint")}</p>
      )}

      <div className="wl-scanbar">
        <span className="wl-status faint">
          {status?.running
            ? cancelling
              ? t("crypto.status.cancelling")
              : scanningLabel(status)
            : status?.last_cancelled
              ? t("crypto.status.cancelled")
              : status?.last_error
                ? t("crypto.status.errorPrevious")
                : status?.last_completed_at
                  ? formatDateOnly(status.last_completed_at, language)
                  : t("crypto.status.never")}
        </span>
        {status?.running ? (
          <button
            className="wl-seed wl-seed--cancel"
            onClick={() => void handleCancel()}
            disabled={cancelling}
          >
            {cancelling ? t("crypto.status.cancelling") : t("crypto.button.cancel")}
          </button>
        ) : (
          <button className="wl-seed" onClick={() => void handleScan()}>
            {t("crypto.button.scan")}
          </button>
        )}
      </div>

      {status?.running && (
        <div className="wl-progress" role="progressbar" aria-label={t("crypto.progressAriaLabel")}>
          <div className="wl-progress-fill" />
        </div>
      )}

      {status?.last_error && <p className="wl-error">{status.last_error}</p>}
      {error && <p className="wl-error">{error}</p>}

      {candidates && (
        <input
          type="text"
          className="wl-crypto__search"
          placeholder={t("crypto.searchPlaceholder")}
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
        />
      )}

      {candidates && (
        <div className="wl-tabs wl-tabs--exchange-filter">
          {EXCHANGE_FILTER_OPTIONS.map((o) => (
            <button
              key={o.value || "all"}
              className={exchangeFilter === o.value ? "is-active" : ""}
              onClick={() => handleExchangeFilterChange(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}

      {candidates && candidates.length === 0 && !error && (
        <p className="wl-empty faint">
          {searchQuery
            ? t("crypto.emptyNoMatch", { query: searchQuery })
            : exchangeFilter
              ? t("crypto.emptyNoExchange")
              : t("crypto.emptyNoScan")}
        </p>
      )}

      {candidates && candidates.length > 0 && (
        <>
          <div className="wl-crypto__listhead">
            <div className="wl-tabs wl-tabs--sort">
              {SORT_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  className={sort === o.value ? "is-active" : ""}
                  onClick={() => handleSortChange(o.value)}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <span className="faint wl-crypto__count">
              {candidates.length}/{total}
            </span>
          </div>

          <ul className="wl-crypto__list" onScroll={handleScroll}>
            {candidates.map((c) => (
              <li key={c.coin_id} className="wl-crypto-card">
                <div className="wl-crypto-card__row1">
                  <span className="wl-row__ticker mono">🪙 {c.symbol.toUpperCase()}</span>
                  <button
                    className="wl-crypto-card__add"
                    onClick={() => void handlePromote(c.coin_id)}
                    disabled={promoting === c.coin_id}
                    aria-label={t("crypto.watch.ariaLabel", { symbol: c.symbol })}
                    title={t("crypto.watch.title")}
                  >
                    {promoting === c.coin_id ? "…" : "+"}
                  </button>
                </div>
                {c.name && (
                  <div className="wl-crypto-card__name faint" title={c.coin_id}>
                    {c.name}
                  </div>
                )}
                <div className="wl-crypto-card__row2 faint mono">
                  <span>{formatUsd(c.market_cap)}</span>
                  <span>·</span>
                  <span>{formatPct(c.volume_change_pct)} vol</span>
                  <span>·</span>
                  <span className="wl-crypto-card__source">{sourceLabel(c)}</span>
                </div>
              </li>
            ))}
            {loadingMore && <li className="wl-crypto__loading-more faint">{t("crypto.loadingMore")}</li>}
          </ul>
        </>
      )}
    </div>
  );
}
