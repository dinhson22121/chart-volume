import { useState } from "react";
import { useI18n } from "../../i18n/I18nContext";
import "./license.css";

interface Props {
  onActivated: () => void;
}

export function ActivationScreen({ onActivated }: Props) {
  const { t } = useI18n();
  const [token, setToken] = useState("");
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const trimmed = token.trim();
    if (!trimmed) return;
    setActivating(true);
    setError(null);
    try {
      const result = await window.chartVolume?.activateLicense(trimmed);
      if (result?.valid) {
        onActivated();
      } else {
        setError(t(`license.error.${result?.reason ?? "bad_format"}`));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("license.error.bad_format"));
    } finally {
      setActivating(false);
    }
  };

  return (
    <div className="license-gate">
      <div className="license-card">
        <h2>{t("license.title")}</h2>
        <p className="faint">{t("license.hint")}</p>
        <textarea
          className="mono"
          placeholder={t("license.placeholder")}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          disabled={activating}
        />
        {error && <p className="settings-error">{error}</p>}
        <div className="license-card__actions">
          <button className="btn btn--primary" onClick={() => void submit()} disabled={activating || !token.trim()}>
            {activating ? t("license.activating") : t("license.button")}
          </button>
        </div>
      </div>
    </div>
  );
}
