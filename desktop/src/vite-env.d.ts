/// <reference types="vite/client" />

interface Window {
  chartVolume?: {
    apiBase: string;
    token: string;
    totalMemGB: number;
    platform: string;
    openExternal: (url: string) => Promise<void>;
  };
}
