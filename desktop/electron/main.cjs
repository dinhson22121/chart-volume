// Electron main process: mint a per-launch token, spawn the Python backend as a
// child process (loopback), then open the window once /health is reachable.
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const http = require("http");
const path = require("path");

const API_PORT = 8787;
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const API_TOKEN = crypto.randomBytes(24).toString("base64url");

const BACKEND_DIR = path.join(__dirname, "..", "..", "backend");

let backendProc = null;
let mainWindow = null;

function pythonBin() {
  // Dev: backend virtualenv. Packaging (PyInstaller binary) comes later.
  const venv = path.join(BACKEND_DIR, ".venv", "bin", "python");
  return venv;
}

function startBackend() {
  const env = {
    ...process.env,
    LOCAL_API_TOKEN: API_TOKEN,
    DB_PATH: path.join(app.getPath("userData"), "chart_volume.db"),
  };
  backendProc = spawn(
    pythonBin(),
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)],
    { cwd: BACKEND_DIR, env }
  );
  backendProc.stdout.on("data", (d) => console.log("[backend]", d.toString().trim()));
  backendProc.stderr.on("data", (d) => console.error("[backend]", d.toString().trim()));
  backendProc.on("exit", (code) => console.log("[backend] exited", code));
}

function waitForHealth(onReady, attempts = 60) {
  const tick = (left) => {
    const req = http.get(`${API_BASE}/health`, (res) => {
      res.resume();
      if (res.statusCode === 200) return onReady();
      retry(left);
    });
    req.on("error", () => retry(left));
  };
  const retry = (left) => {
    if (left <= 0) {
      console.error("backend health check timed out");
      return onReady(); // open the window anyway; UI will show the error
    }
    setTimeout(() => tick(left - 1), 500);
  };
  tick(attempts);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    backgroundColor: "#0e1116",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    mainWindow.loadURL(devUrl);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

// Synchronous config handshake so the renderer has apiBase + token before first fetch.
ipcMain.on("get-config-sync", (event) => {
  event.returnValue = { apiBase: API_BASE, token: API_TOKEN };
});

function shutdownBackend() {
  if (backendProc) {
    backendProc.kill();
    backendProc = null;
  }
}

app.whenReady().then(() => {
  startBackend();
  waitForHealth(createWindow);
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  shutdownBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", shutdownBackend);
