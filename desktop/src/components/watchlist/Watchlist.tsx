import { useMemo, useState, type FormEvent } from "react";
import type { SymbolItem } from "../../types";
import "./watchlist.css";

interface Props {
  symbols: SymbolItem[];
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => void;
  onRemove: (ticker: string) => void;
  onSeedVn30: () => void;
  busy: boolean;
}

export function Watchlist({ symbols, selected, onSelect, onAdd, onRemove, onSeedVn30, busy }: Props) {
  const [input, setInput] = useState("");

  const { vn30, watchlist } = useMemo(() => {
    const vn30 = symbols.filter((s) => s.is_vn30).sort((a, b) => a.ticker.localeCompare(b.ticker));
    const watchlist = symbols
      .filter((s) => s.is_watchlist && !s.is_vn30)
      .sort((a, b) => a.ticker.localeCompare(b.ticker));
    return { vn30, watchlist };
  }, [symbols]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (t) {
      onAdd(t);
      setInput("");
    }
  };

  const renderRow = (s: SymbolItem, removable: boolean) => (
    <li key={s.ticker}>
      <button
        className={`wl-row ${selected === s.ticker ? "is-selected" : ""}`}
        onClick={() => onSelect(s.ticker)}
      >
        <span className="wl-row__ticker mono">{s.ticker}</span>
        {s.name && <span className="wl-row__name faint">{s.name}</span>}
        {removable && (
          <span
            className="wl-row__remove"
            role="button"
            aria-label={`Bỏ ${s.ticker}`}
            onClick={(e) => {
              e.stopPropagation();
              onRemove(s.ticker);
            }}
          >
            ×
          </span>
        )}
      </button>
    </li>
  );

  return (
    <div className="watchlist">
      <form className="wl-add" onSubmit={submit}>
        <input
          className="wl-add__input mono"
          placeholder="Thêm mã (vd HPG)"
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
            <h3 className="wl-group__title">Theo dõi</h3>
            <ul>{watchlist.map((s) => renderRow(s, true))}</ul>
          </section>
        )}

        <section className="wl-group">
          <div className="wl-group__head">
            <h3 className="wl-group__title">VN30</h3>
            {vn30.length === 0 && (
              <button className="wl-seed" onClick={onSeedVn30} disabled={busy}>
                Tải VN30
              </button>
            )}
          </div>
          {vn30.length === 0 ? (
            <p className="wl-empty faint">Chưa có dữ liệu VN30.</p>
          ) : (
            <ul>{vn30.map((s) => renderRow(s, false))}</ul>
          )}
        </section>
      </div>
    </div>
  );
}
