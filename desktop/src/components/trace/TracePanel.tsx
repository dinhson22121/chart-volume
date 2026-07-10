import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { BarTrace, Timeframe } from "../../types";
import { signalLabel } from "../../lib/wyckoff";
import "./trace.css";

interface Props {
  ticker: string;
  displaySymbol: string;
  timeframe: Timeframe;
  barTs: string;
  onClose: () => void;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("vi-VN", { dateStyle: "medium", timeStyle: "short" });
}

export function TracePanel({ ticker, displaySymbol, timeframe, barTs, onClose }: Props) {
  const [trace, setTrace] = useState<BarTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTrace(null);
    setError(null);
    api.getTrace(ticker, timeframe, barTs).then(setTrace).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : "Không tải được giải thích");
    });
  }, [ticker, timeframe, barTs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const matchedTypes = trace?.detectors.filter((d) => d.matched) ?? [];
  const notMatchedTypes = trace?.detectors.filter((d) => !d.matched) ?? [];

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="trace-modal" onClick={(e) => e.stopPropagation()}>
        <header className="settings-modal__header">
          <h2>
            Vì sao nến này? <span className="trace-ticker mono">{displaySymbol}</span>
          </h2>
          <button className="settings-modal__close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </header>

        <div className="settings-modal__body">
          <p className="faint trace-date mono">{formatDate(barTs)}</p>

          {error && <p className="settings-error">{error}</p>}
          {!trace && !error && <p className="faint">Đang tải…</p>}

          {trace && (
            <>
              {matchedTypes.length > 0 && (
                <section className="settings-section">
                  <h3>Tín hiệu khớp</h3>
                  {matchedTypes.map((d) => (
                    <div key={d.type} className="trace-detector trace-detector--matched">
                      <div className="trace-detector__title">{signalLabel(d.type)}</div>
                      <ul className="trace-checks">
                        {d.checks.map((c, i) => (
                          <li key={i} className="trace-check trace-check--pass">
                            <span className="trace-check__icon">✓</span>
                            <span>
                              {c.label}: <span className="mono faint">{c.detail}</span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </section>
              )}

              {notMatchedTypes.length > 0 && (
                <section className="settings-section">
                  <h3>Không khớp (và lý do)</h3>
                  {notMatchedTypes.map((d) => (
                    <details key={d.type} className="trace-detector">
                      <summary className="trace-detector__title">{signalLabel(d.type)}</summary>
                      <ul className="trace-checks">
                        {d.checks.map((c, i) => (
                          <li
                            key={i}
                            className={`trace-check ${c.passed ? "trace-check--pass" : "trace-check--fail"}`}
                          >
                            <span className="trace-check__icon">{c.passed ? "✓" : "✗"}</span>
                            <span>
                              {c.label}: <span className="mono faint">{c.detail}</span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </section>
              )}
            </>
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
