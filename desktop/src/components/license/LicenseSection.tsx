import { useEffect, useState } from "react";
import { useI18n } from "../../i18n/I18nContext";
import { formatDateOnly } from "../../lib/datetime";
import "./license.css";

interface Props {
  onCleared: () => void;
}

export function LicenseSection({ onCleared }: Props) {
  const { t, language } = useI18n();
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    window.chartVolume?.getLicenseStatus().then(setStatus);
  }, []);

  const handleClear = async () => {
    if (!window.chartVolume) return;
    setClearing(true);
    await window.chartVolume.clearLicense();
    onCleared();
  };

  if (!status) return null;

  const payload = status.payload;
  const isMaster = payload?.master === true;
  const exp = payload?.exp ?? null;
  const daysLeft = exp !== null ? Math.ceil((exp * 1000 - Date.now()) / 86_400_000) : null;

  return (
    <section className="settings-section">
      <h3>{t("settings.section.license")}</h3>
      <div className="license-manage__row">
        <p className="license-manage__info">
          {isMaster
            ? t("license.manage.permanent")
            : exp !== null
              ? t("license.manage.expiresOn", {
                  date: formatDateOnly(new Date(exp * 1000).toISOString(), language),
                  days: daysLeft ?? 0,
                })
              : t("license.manage.unknown")}
        </p>
        {!confirming ? (
          <button className="btn btn--danger" onClick={() => setConfirming(true)}>
            {t("license.manage.clearButton")}
          </button>
        ) : (
          <div className="license-manage__confirm">
            <span>{t("license.manage.confirmText")}</span>
            <button className="btn btn--danger" onClick={() => void handleClear()} disabled={clearing}>
              {clearing ? t("license.manage.clearing") : t("license.manage.confirmYes")}
            </button>
            <button className="btn" onClick={() => setConfirming(false)} disabled={clearing}>
              {t("license.manage.confirmNo")}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
