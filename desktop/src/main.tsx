import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import AppGate from "./AppGate";
import { I18nProvider } from "./i18n/I18nContext";
import "./styles/global.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <AppGate />
    </I18nProvider>
  </StrictMode>,
);
