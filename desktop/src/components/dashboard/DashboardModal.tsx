import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { DashboardRow } from "../../types";
import { phaseColor, phaseLabel, signalLabel } from "../../lib/wyckoff";
import "../stats/stats.css";

interface Props {
  onClose: () => void;
  onSelect: (ticker: string) => void;
}

type Filter = "all" | "stock" | "crypto";

const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: "all", label: "Tất cả" },
  { value: "stock", label: "Cổ phiếu" },
  { value: "crypto", label: "Crypto" },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("vi-VN", { dateStyle: "medium", timeStyle: "short" });
}

export function DashboardModal({ onClose, onSelect }: Props) {
  const [rows, setRows] = useState<DashboardRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    api.getDashboard().then(setRows).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : "Không tải được dashboard");
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filtered = useMemo(() => {
    if (!rows) return null;
    if (filter === "all") return rows;
    return rows.filter((r) => r.asset_class === filter);
  }, [rows, filter]);

  const handleRowClick = (ticker: string) => {
    onSelect(ticker);
    onClose();
  };

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>Dashboard theo dõi</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint stats-hint">
            Dữ liệu từ lần phân tích ngày (daily) gần nhất đã lưu — không crawl lại. Bấm "Cập nhật" ở mã
            tương ứng nếu muốn làm mới.
          </p>

          <div className="wl-tabs" style={{ marginBottom: "var(--space-3)", maxWidth: 280 }}>
            {FILTER_OPTIONS.map((o) => (
              <button
                key={o.value}
                className={filter === o.value ? "is-active" : ""}
                onClick={() => setFilter(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>

          {error && <p className="settings-error">{error}</p>}
          {!rows && !error && <p className="faint">Đang tải…</p>}
          {filtered && filtered.length === 0 && (
            <p className="faint">Chưa có mã nào trong danh mục này.</p>
          )}

          {filtered && filtered.length > 0 && (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>Mã</th>
                    <th>Phase</th>
                    <th>Độ tin cậy</th>
                    <th>Tín hiệu gần nhất</th>
                    <th>Cập nhật lúc</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <tr
                      key={r.ticker}
                      onClick={() => handleRowClick(r.ticker)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>
                          {r.ticker}
                        </span>{" "}
                        {r.asset_class === "crypto" && <span title="Crypto">🪙</span>}
                        {r.name && <span className="faint"> {r.name}</span>}
                      </td>
                      {r.has_data ? (
                        <>
                          <td>
                            <span
                              style={{
                                display: "inline-block",
                                padding: "3px 10px",
                                borderRadius: 999,
                                fontSize: "var(--text-xs)",
                                fontWeight: 700,
                                color: "oklch(18% 0.02 250)",
                                backgroundColor: phaseColor(r.phase ?? ""),
                              }}
                            >
                              {phaseLabel(r.phase ?? "")}
                            </span>
                          </td>
                          <td className="mono">
                            {r.confidence !== null ? `${Math.round(r.confidence * 100)}%` : "—"}
                          </td>
                          <td>{r.latest_signal ? signalLabel(r.latest_signal.type) : "—"}</td>
                          <td className="faint">{r.as_of ? formatDate(r.as_of) : "—"}</td>
                        </>
                      ) : (
                        <td colSpan={4} className="faint">
                          Chưa phân tích
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <footer className="settings-modal__footer">
          <button className="btn" onClick={onClose}>
            Đóng
          </button>
        </footer>
      </div>
    </div>
  );
}
