import type { Analysis } from "../../types";
import { phaseColor, phaseLabel, signalLabel } from "../../lib/wyckoff";
import { formatPrice } from "../../lib/price";
import { formatDateTime } from "../../lib/datetime";
import { useI18n } from "../../i18n/I18nContext";
import "./analysis.css";

interface Props {
  analysis: Analysis | null;
  loading: boolean;
  error: string | null;
}

export function AnalysisPanel({ analysis, loading, error }: Props) {
  const { t, language } = useI18n();

  if (loading) {
    return <div className="analysis-panel analysis-panel--empty faint">{t("analysis.loading")}</div>;
  }
  if (error) {
    return <div className="analysis-panel analysis-panel--empty faint">{error}</div>;
  }
  if (!analysis) {
    return (
      <div className="analysis-panel analysis-panel--empty faint">
        {t("analysis.emptyPrefix")}
        <strong>{t("app.refresh.analyze")}</strong>
        {t("analysis.emptySuffix")}
      </div>
    );
  }

  const confidencePct = Math.round(analysis.confidence * 100);

  return (
    <div className="analysis-panel">
      <div className="analysis-scroll">
        <header className="ap-phase">
          <span className="ap-phase__badge" style={{ backgroundColor: phaseColor(analysis.phase) }}>
            {phaseLabel(analysis.phase, language)}
          </span>
          <div className="ap-phase__meta faint mono">
            <span>{t("analysis.confidence", { pct: confidencePct })}</span>
            <span>· {formatDateTime(analysis.as_of, language)}</span>
          </div>
          {analysis.mtf_alignment && (
            <span
              className={`ap-mtf-badge ${
                analysis.mtf_alignment === "aligned" ? "ap-mtf-badge--aligned" : "ap-mtf-badge--conflicting"
              }`}
            >
              {analysis.mtf_alignment === "aligned" ? t("analysis.mtf.aligned") : t("analysis.mtf.conflicting")}
            </span>
          )}
        </header>

        <div className="ap-levels">
          <div className="ap-level">
            <span className="ap-level__label faint">{t("chart.resistance")}</span>
            <span className="ap-level__value mono" style={{ color: "var(--warn)" }}>
              {formatPrice(analysis.levels.resistance)}
            </span>
          </div>
          <div className="ap-level">
            <span className="ap-level__label faint">{t("chart.support")}</span>
            <span className="ap-level__value mono" style={{ color: "var(--bull)" }}>
              {formatPrice(analysis.levels.support)}
            </span>
          </div>
        </div>

        {analysis.signals.length > 0 && (
          <section className="ap-section">
            <h4 className="ap-section__title">{t("analysis.section.signals")}</h4>
            <ul className="ap-signals">
              {analysis.signals.slice(-8).reverse().map((s, i) => (
                <li key={i} className="ap-signal">
                  <span className="ap-signal__type mono">{signalLabel(s.type, language)}</span>
                  <span className="ap-signal__note faint">{s.note}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {analysis.narrative && (
          <section className="ap-section">
            <h4 className="ap-section__title">{t("analysis.section.narrative")}</h4>
            <p className="ap-text">{analysis.narrative}</p>
          </section>
        )}

        {analysis.advice && (
          <section className="ap-section">
            <h4 className="ap-section__title">{t("analysis.section.advice")}</h4>
            <p className="ap-text ap-text--advice">{analysis.advice}</p>
          </section>
        )}

        {!analysis.narrative && (
          <p className="ap-hint faint">{t("analysis.noNarrative")}</p>
        )}
      </div>
    </div>
  );
}
