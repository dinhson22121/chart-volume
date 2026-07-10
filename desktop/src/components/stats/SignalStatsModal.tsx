import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { SignalStat } from "../../types";
import { signalLabel } from "../../lib/wyckoff";
import "./stats.css";

interface Props {
  onClose: () => void;
}

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

function ret(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

export function SignalStatsModal({ onClose }: Props) {
  const [stats, setStats] = useState<SignalStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSignalStats().then(setStats).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : "Không tải được thống kê");
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="stats-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>Thống kê hiệu quả tín hiệu</h2>
          <button className="settings-modal__close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint stats-hint">
            Tỷ lệ thắng (win-rate) = % số lần tín hiệu này xảy ra và giá đi đúng chiều kỳ vọng, sau N nến.
            Dữ liệu tổng hợp từ toàn bộ mã đã crawl.
          </p>

          {error && <p className="settings-error">{error}</p>}
          {!stats && !error && <p className="faint">Đang tải…</p>}
          {stats && stats.length === 0 && (
            <p className="faint">Chưa có dữ liệu. Hãy "Phân tích" vài mã trước.</p>
          )}

          {stats && stats.length > 0 && (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>Tín hiệu</th>
                    <th>Số lần</th>
                    <th>Win 5 nến</th>
                    <th>Win 10 nến</th>
                    <th>Win 20 nến</th>
                    <th>Return TB (10 nến)</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.map((s) => (
                    <tr key={s.type}>
                      <td>
                        <span className={`stats-dot ${s.is_bullish ? "stats-dot--bull" : "stats-dot--bear"}`} />
                        {signalLabel(s.type)}
                      </td>
                      <td className="mono">{s.count}</td>
                      <td className="mono">{pct(s.win_rate_5)}</td>
                      <td className="mono">{pct(s.win_rate_10)}</td>
                      <td className="mono">{pct(s.win_rate_20)}</td>
                      <td className="mono">{ret(s.avg_return_10)}</td>
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
