import type { Analysis } from "../../types";
import { phaseColor, phaseLabel, signalLabel } from "../../lib/wyckoff";
import "./analysis.css";

interface Props {
  analysis: Analysis | null;
  loading: boolean;
  error: string | null;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("vi-VN", { dateStyle: "medium", timeStyle: "short" });
}

export function AnalysisPanel({ analysis, loading, error }: Props) {
  if (loading) {
    return <div className="analysis-panel analysis-panel--empty faint">Đang phân tích…</div>;
  }
  if (error) {
    return <div className="analysis-panel analysis-panel--empty faint">{error}</div>;
  }
  if (!analysis) {
    return (
      <div className="analysis-panel analysis-panel--empty faint">
        Chưa có phân tích. Chọn một mã rồi bấm <strong>Cập nhật</strong>.
      </div>
    );
  }

  const confidencePct = Math.round(analysis.confidence * 100);

  return (
    <div className="analysis-panel">
      <div className="analysis-scroll">
        <header className="ap-phase">
          <span className="ap-phase__badge" style={{ backgroundColor: phaseColor(analysis.phase) }}>
            {phaseLabel(analysis.phase)}
          </span>
          <div className="ap-phase__meta faint mono">
            <span>Độ tin cậy {confidencePct}%</span>
            <span>· {formatDate(analysis.as_of)}</span>
          </div>
          {analysis.mtf_alignment && (
            <span
              className={`ap-mtf-badge ${
                analysis.mtf_alignment === "aligned" ? "ap-mtf-badge--aligned" : "ap-mtf-badge--conflicting"
              }`}
            >
              {analysis.mtf_alignment === "aligned" ? "Khớp xu hướng ngày" : "Ngược xu hướng ngày"}
            </span>
          )}
        </header>

        <div className="ap-levels">
          <div className="ap-level">
            <span className="ap-level__label faint">Kháng cự</span>
            <span className="ap-level__value mono" style={{ color: "var(--warn)" }}>
              {analysis.levels.resistance.toFixed(2)}
            </span>
          </div>
          <div className="ap-level">
            <span className="ap-level__label faint">Hỗ trợ</span>
            <span className="ap-level__value mono" style={{ color: "var(--bull)" }}>
              {analysis.levels.support.toFixed(2)}
            </span>
          </div>
        </div>

        {analysis.signals.length > 0 && (
          <section className="ap-section">
            <h4 className="ap-section__title">Tín hiệu Wyckoff</h4>
            <ul className="ap-signals">
              {analysis.signals.slice(-8).reverse().map((s, i) => (
                <li key={i} className="ap-signal">
                  <span className="ap-signal__type mono">{signalLabel(s.type)}</span>
                  <span className="ap-signal__note faint">{s.note}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {analysis.narrative && (
          <section className="ap-section">
            <h4 className="ap-section__title">Nhận định</h4>
            <p className="ap-text">{analysis.narrative}</p>
          </section>
        )}

        {analysis.advice && (
          <section className="ap-section">
            <h4 className="ap-section__title">Lời khuyên</h4>
            <p className="ap-text ap-text--advice">{analysis.advice}</p>
          </section>
        )}

        {!analysis.narrative && (
          <p className="ap-hint faint">
            Chưa có nhận định AI (thiếu ANTHROPIC_API_KEY hoặc chưa gọi). Phân tích định lượng vẫn hiển thị ở trên.
          </p>
        )}
      </div>
    </div>
  );
}
