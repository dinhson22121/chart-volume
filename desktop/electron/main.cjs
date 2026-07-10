// Electron main process: mint a per-launch token, spawn the Python backend as a
// child process (loopback), then open the window once /health is reachable.
const { app, BrowserWindow, ipcMain, safeStorage } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const API_PORT = 8787;
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const API_TOKEN = crypto.randomBytes(24).toString("base64url");

const BACKEND_DIR = path.join(__dirname, "..", "..", "backend");

let backendProc = null;
let mainWindow = null;

// Persistent 32-byte hex key used by the backend to encrypt secrets (e.g. the
// Anthropic API key) at rest in SQLite. Encrypted via the OS keychain
// (safeStorage) when available; falls back to a plaintext file otherwise so
// the app still works on platforms/configs without keychain access.
function loadOrCreateSettingsKey() {
  const keyPath = path.join(app.getPath("userData"), "settings-key.enc");
  if (fs.existsSync(keyPath)) {
    const raw = fs.readFileSync(keyPath);
    try {
      return safeStorage.isEncryptionAvailable() ? safeStorage.decryptString(raw) : raw.toString("utf8");
    } catch (err) {
      // Corrupt, tampered, or unreadable (e.g. moved to a machine/OS-user
      // that can't unwrap it via safeStorage): fall through to regenerate --
      // but surface it, since this silently strands any settings that were
      // encrypted with the old key (the Anthropic API key won't decrypt
      // anymore and effectively looks "unset" with no error shown anywhere).
      console.warn(
        "[main] settings-key.enc exists but failed to decrypt, regenerating a new key -- " +
          "any previously-encrypted settings (e.g. the Anthropic API key) will need to be re-entered:",
        err,
      );
    }
  }
  const hexKey = crypto.randomBytes(32).toString("hex");
  const toWrite = safeStorage.isEncryptionAvailable()
    ? safeStorage.encryptString(hexKey)
    : Buffer.from(hexKey, "utf8");
  fs.mkdirSync(path.dirname(keyPath), { recursive: true });
  fs.writeFileSync(keyPath, toWrite, { mode: 0o600 });
  return hexKey;
}

function backendCommand() {
  // Packaged: run the PyInstaller-bundled binary from resources.
  // Dev: run uvicorn from the backend virtualenv.
  const env = {
    ...process.env,
    HOST: "127.0.0.1",
    PORT: String(API_PORT),
    LOCAL_API_TOKEN: API_TOKEN,
    DB_PATH: path.join(app.getPath("userData"), "chart_volume.db"),
    SETTINGS_KEY: loadOrCreateSettingsKey(),
  };
  if (app.isPackaged) {
    const bin = path.join(process.resourcesPath, "backend", "chart-volume-backend");
    return { cmd: bin, args: [], options: { env } };
  }
  const py = path.join(BACKEND_DIR, ".venv", "bin", "python");
  return {
    cmd: py,
    args: ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)],
    options: { cwd: BACKEND_DIR, env },
  };
}

function startBackend() {
  const { cmd, args, options } = backendCommand();
  backendProc = spawn(cmd, args, options);
  backendProc.stdout.on("data", (d) => console.log("[backend]", d.toString().trim()));
  backendProc.stderr.on("data", (d) => console.error("[backend]", d.toString().trim()));
  backendProc.on("exit", (code) => console.log("[backend] exited", code));
}

function waitForHealth(onReady, attempts = 120) {
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

// nativeImage (used by BrowserWindow's `icon` and app.dock.setIcon) loads
// PNG/JPEG, not .icns -- the .icns is only for electron-builder's packaged
// app icon (build.mac.icon in package.json), a separate mechanism.
const ICON_PATH = path.join(__dirname, "..", "build", "icon.png");

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    backgroundColor: "#0e1116",
    icon: ICON_PATH,
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
  if (process.platform === "darwin" && app.dock) {
    app.dock.setIcon(ICON_PATH); // dev-mode dock icon; packaged builds use build.mac.icon instead
  }
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
