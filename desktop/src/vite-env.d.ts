/// <reference types="vite/client" />

interface LicenseStatus {
  valid: boolean;
  payload?: { exp: number | null; iat?: number; master?: boolean };
  reason?: "empty" | "bad_format" | "bad_signature" | "expired";
}

interface Window {
  chartVolume?: {
    apiBase: string;
    token: string;
    totalMemGB: number;
    platform: string;
    openExternal: (url: string) => Promise<void>;
    getLicenseStatus: () => Promise<LicenseStatus>;
    activateLicense: (token: string) => Promise<LicenseStatus>;
    clearLicense: () => Promise<void>;
    onLicenseExpired: (cb: () => void) => void;
  };
}
