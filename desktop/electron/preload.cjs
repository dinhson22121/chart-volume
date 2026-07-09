// Expose the loopback API base + per-launch token to the renderer under a
// single namespaced object. Uses a synchronous handshake with the main process.
const { contextBridge, ipcRenderer } = require("electron");

const config = ipcRenderer.sendSync("get-config-sync");

contextBridge.exposeInMainWorld("chartVolume", {
  apiBase: config.apiBase,
  token: config.token,
});
