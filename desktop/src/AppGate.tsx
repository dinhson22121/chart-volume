import { useEffect, useState } from "react";
import App from "./App";
import { ActivationScreen } from "./components/license/ActivationScreen";

type GateStatus = "checking" | "active" | "inactive";

export default function AppGate() {
  const [status, setStatus] = useState<GateStatus>("checking");

  useEffect(() => {
    const bridge = window.chartVolume;
    if (!bridge) {
      // Plain-browser dev session (no Electron preload) -- nothing to gate.
      setStatus("active");
      return;
    }
    bridge.getLicenseStatus().then((r) => setStatus(r.valid ? "active" : "inactive"));
    bridge.onLicenseExpired(() => setStatus("inactive"));
  }, []);

  if (status === "checking") return null;
  if (status === "inactive") return <ActivationScreen onActivated={() => setStatus("active")} />;
  return <App onLicenseCleared={() => setStatus("inactive")} />;
}
