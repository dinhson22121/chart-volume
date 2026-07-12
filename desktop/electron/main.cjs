// Electron main process: mint a per-launch token, spawn the Python backend as a
// child process (loopback), then open the window once /health is reachable.
const { app, BrowserWindow, ipcMain, safeStorage, shell } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { verifyToken } = require("./license/verify.cjs");
const { loadLicense, saveLicense } = require("./license/store.cjs");

// How often to re-check the stored license against its own TTL while the app
// stays open across the expiry boundary. Deliberately does NOT kill an
// already-running backend/in-flight scan on expiry -- it only tells the
// renderer to show the activation screen again; a full restart re-applies
// the hard gate in app.whenReady() below.
const LICENSE_RECHECK_MS = 15 * 60 * 1000;

// Rounded down: used only to gate/recommend local-AI (Ollama) setup in
// Settings, not a hard resource limit -- os.totalmem() is cross-platform
// (Mac/Mac ARM/Windows/Linux) via Node itself, no native deps needed.
const TOTAL_MEM_GB = Math.floor(os.totalmem() / 1024 ** 3);

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
    // PyInstaller doesn't cross-compile -- the bundled binary name/extension
    // matches whatever OS it was built on (see package.json's per-platform
    // extraResources: .exe on Windows, extension-less on Mac/Linux).
    const binName = process.platform === "win32" ? "chart-volume-backend.exe" : "chart-volume-backend";
    const bin = path.join(process.resourcesPath, "backend", binName);
    return { cmd: bin, args: [], options: { env } };
  }
  // venv layout differs on Windows (Scripts/python.exe) vs Mac/Linux (bin/python).
  const py = process.platform === "win32"
    ? path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
    : path.join(BACKEND_DIR, ".venv", "bin", "python");
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

function startBackendAndWaitReady() {
  return new Promise((resolve) => {
    startBackend();
    waitForHealth(resolve);
  });
}

function getLicenseStatus() {
  const token = loadLicense(app.getPath("userData"));
  if (!token) return { valid: false, reason: "empty" };
  return verifyToken(token);
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
  event.returnValue = {
    apiBase: API_BASE,
    token: API_TOKEN,
    totalMemGB: TOTAL_MEM_GB,
    platform: process.platform, // "darwin" | "win32" | "linux" -- Mac (incl. Apple Silicon), Win64, Linux
  };
});

// Opens a URL in the user's default browser (e.g. the Ollama download page)
// -- restricted to http(s) so this channel can never be used to open
// arbitrary local files or custom protocol handlers from the renderer.
ipcMain.handle("open-external", (_event, url) => {
  if (typeof url === "string" && /^https:\/\//.test(url)) {
    shell.openExternal(url);
  }
});

ipcMain.handle("license:get-status", () => getLicenseStatus());

// Verifies the token, persists it, then starts the backend and only resolves
// once it's healthy -- the renderer's activation screen awaits this single
// promise instead of juggling a separate "backend ready" event channel.
ipcMain.handle("license:activate", async (_event, token) => {
  const result = verifyToken(token);
  if (!result.valid) return result;
  saveLicense(app.getPath("userData"), token);
  await startBackendAndWaitReady();
  return result;
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

  // Gate: only auto-start the backend if a still-valid license is already on
  // disk from a previous activation. Otherwise open the window right away
  // (no backend, no health wait) -- the renderer sees an inactive license
  // status and renders the activation screen; the backend only starts once
  // the user submits a valid token (see the license:activate handler above).
  if (getLicenseStatus().valid) {
    startBackend();
    waitForHealth(createWindow);
  } else {
    createWindow();
  }

  setInterval(() => {
    const status = getLicenseStatus();
    if (!status.valid && mainWindow) {
      mainWindow.webContents.send("license:expired");
    }
  }, LICENSE_RECHECK_MS);

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  shutdownBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", shutdownBackend);
