// Reads/writes the locally-activated license token. The token itself isn't
// treated as a secret (its security comes from the Ed25519 signature check
// in verify.cjs, not from hiding this file) so it's stored as plain JSON.
const fs = require("fs");
const path = require("path");

function licensePath(userDataDir) {
  return path.join(userDataDir, "license.json");
}

function loadLicense(userDataDir) {
  try {
    const raw = fs.readFileSync(licensePath(userDataDir), "utf8");
    const parsed = JSON.parse(raw);
    return typeof parsed.token === "string" ? parsed.token : null;
  } catch {
    return null;
  }
}

function saveLicense(userDataDir, token) {
  const file = licensePath(userDataDir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ token }), { mode: 0o600 });
}

module.exports = { loadLicense, saveLicense };
