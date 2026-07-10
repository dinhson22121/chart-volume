import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { CandidateSort, ScanStatus, ScreenerCandidate } from "../../types";

interface Props {
  onPromoted: (ticker: string) => void;
}

const POLL_MS = 2500;
const PAGE_SIZE = 50;
const SCROLL_THRESHOLD_PX = 40;
const SEARCH_DEBOUNCE_MS = 300;

const EXCHANGE_OPTIONS = [
  { value: "binance", label: "Binance" },
  { value: "kucoin", label: "KuCoin" },
  { value: "mexc", label: "MEXC" },
  { value: "geckoterminal", label: "GeckoTerminal (DEX, chậm hơn)" },
];

const SORT_OPTIONS: { value: CandidateSort; label: string }[] = [
  { value: "volume_change", label: "Volume" },
  { value: "market_cap", label: "Vốn hóa" },
];

// "" means no filter (Tất cả). Values match CryptoExchange on the backend --
// "geckoterminal" filters by source (DEX pool hits have no resolved exchange).
const EXCHANGE_FILTER_OPTIONS = [
  { value: "", label: "Tất cả" },
  { value: "binance", label: "Binance" },
  { value: "kucoin", label: "KuCoin" },
  { value: "mexc", label: "MEXC" },
  { value: "geckoterminal", label: "DEX" },
];

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

function scanPhaseLabel(phase: ScanStatus["phase"]): string {
  return phase === "dex_pools" ? "DEX (GeckoTerminal)" : "CoinGecko";
}

const EXCHANGE_LABEL: Record<string, string> = { binance: "Binance", kucoin: "KuCoin", mexc: "MEXC" };

// Nhiều coin khác nhau có thể trùng ký hiệu (vd nhiều "pepe" clone khác nhau) --
// nhãn nguồn giúp biết coin này thực sự lấy nến từ đâu. Ưu tiên hiện sàn thật
// (Binance/KuCoin/MEXC) nếu đã dò được lúc quét -- "CoinGecko" chỉ là nguồn
// phát hiện, không phải nơi sẽ lấy nến, nên không đủ thông tin nếu đứng một
// mình. Tên đầy đủ (c.name, hiển thị riêng ở card) mới là thứ phân biệt các
// coin trùng ký hiệu với nhau.
function sourceLabel(c: ScreenerCandidate): string {
  if (c.source === "geckoterminal") return `DEX${c.network ? ` · ${c.network}` : ""}`;
  if (c.exchange) return EXCHANGE_LABEL[c.exchange] ?? c.exchange;
  return "CoinGecko (chưa rõ sàn)";
}

// There's no reliable upfront "total pages" to compute a real percentage from
// (CoinGecko doesn't report one, and GeckoTerminal's page count varies), so
// the running line shows live page/hit counts instead -- paired with an
// indeterminate progress bar to signal "still working".
function scanningLabel(status: ScanStatus): string {
  const parts = [`Đang quét ${scanPhaseLabel(status.phase)}`];
  if (status.current_page != null) parts.push(`trang ${status.current_page}`);
  if (status.hits_so_far != null) parts.push(`${status.hits_so_far} coin`);
  return parts.join(" — ");
}

export function CryptoDiscovery({ onPromoted }: Props) {
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

  const loadFirstPage = useCallback(async (sortValue: CandidateSort, query: string, exchangeValue: string) => {
    try {
      const res = await api.getScreenerCandidates(sortValue, 1, PAGE_SIZE, query || undefined, exchangeValue || undefined);
      setCandidates(res.items);
      setTotal(res.total);
      setPage(1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không tải được danh sách candidate");
    }
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
      setCancelling(false); // scan actually stopped -- clear the "Đang hủy…" state
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
      setError(e instanceof Error ? e.message : "Không tải thêm được");
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
      setError(e instanceof Error ? e.message : "Kích hoạt quét thất bại");
    }
  };

  const handleCancel = async () => {
    setCancelling(true); // immediate feedback -- the scan itself can take a moment to actually stop
    try {
      await api.cancelScreenerScan();
      await refreshStatus();
    } catch (e) {
      setCancelling(false);
      setError(e instanceof Error ? e.message : "Hủy quét thất bại");
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
      setError(e instanceof Error ? e.message : "Thêm vào theo dõi thất bại");
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
      setError(e instanceof Error ? e.message : "Lưu sàn thất bại");
    } finally {
      setSavingExchanges(false);
    }
  };

  return (
    <div className="wl-accordion__body">
      <div className="wl-crypto__exchange-box">
        <div className="wl-crypto__exchanges">
          <span className="faint">Tìm coin:</span>
          <label className="wl-crypto__exchange">
            <input type="checkbox" checked disabled />
            CoinGecko
          </label>
        </div>

        <div className="wl-crypto__exchanges">
          <span className="faint">Sàn / DEX:</span>
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
        <p className="wl-crypto__hint faint">
          GeckoTerminal bật: quét thêm cả coin mới/hot trên DEX (ngoài danh sách CoinGecko), và dùng làm
          nguồn lấy nến dự phòng cho coin không có trên Binance/KuCoin.
        </p>
      )}

      <div className="wl-scanbar">
        <span className="wl-status faint">
          {status?.running
            ? cancelling
              ? "Đang hủy…"
              : scanningLabel(status)
            : status?.last_cancelled
              ? "Đã hủy quét"
              : status?.last_error
                ? "Lỗi lần quét trước"
                : status?.last_completed_at
                  ? new Date(status.last_completed_at).toLocaleDateString("vi-VN")
                  : "Chưa quét lần nào"}
        </span>
        {status?.running ? (
          <button
            className="wl-seed wl-seed--cancel"
            onClick={() => void handleCancel()}
            disabled={cancelling}
          >
            {cancelling ? "Đang hủy…" : "✕ Hủy"}
          </button>
        ) : (
          <button className="wl-seed" onClick={() => void handleScan()}>
            🔍 Quét
          </button>
        )}
      </div>

      {status?.running && (
        <div className="wl-progress" role="progressbar" aria-label="Đang quét coin">
          <div className="wl-progress-fill" />
        </div>
      )}

      {status?.last_error && <p className="wl-error">{status.last_error}</p>}
      {error && <p className="wl-error">{error}</p>}

      {candidates && (
        <input
          type="text"
          className="wl-crypto__search"
          placeholder="Tìm trong danh sách (mã hoặc tên)…"
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
            ? `Không tìm thấy coin nào khớp "${searchQuery}".`
            : exchangeFilter
              ? "Không có candidate nào trên sàn này."
              : 'Chưa có candidate. Bấm "Quét" để tìm coin vốn hóa nhỏ.'}
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
                    aria-label={`Theo dõi ${c.symbol}`}
                    title="Thêm vào theo dõi"
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
            {loadingMore && <li className="wl-crypto__loading-more faint">Đang tải thêm…</li>}
          </ul>
        </>
      )}
    </div>
  );
}
