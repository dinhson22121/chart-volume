#!/usr/bin/env node
/**
 * Generates an Ed25519 key pair for offline license signing.
 *
 * Run this ONCE, on the machine that will issue licenses -- never on a
 * machine that ships the app. The private key file must never be committed
 * to git or copied onto a user's machine; only the public key (printed to
 * stdout) gets pasted into desktop/electron/license/publicKey.cjs.
 *
 * Usage:
 *   node tools/license/keygen.js <output-private-key-path>
 *   node tools/license/keygen.js /path/outside/repo/license-private.pem
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const outPath = process.argv[2];
if (!outPath) {
  console.error("Usage: node tools/license/keygen.js <output-private-key-path>");
  process.exit(1);
}

const resolved = path.resolve(outPath);
const repoRoot = path.resolve(__dirname, "..", "..");
if (resolved.startsWith(repoRoot)) {
  console.error(
    "Refusing to write the private key inside the repo (" +
      repoRoot +
      "). Choose a path outside the project, e.g. ~/chart-volume-license-private.pem",
  );
  process.exit(1);
}

const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");

const publicKeyB64 = publicKey.export({ type: "spki", format: "der" }).toString("base64");
const privateKeyPem = privateKey.export({ type: "pkcs8", format: "pem" });

fs.mkdirSync(path.dirname(resolved), { recursive: true });
fs.writeFileSync(resolved, privateKeyPem, { mode: 0o600 });

console.log("Private key written to:", resolved);
console.log("KEEP THIS FILE SAFE AND OUT OF GIT. Anyone with it can mint valid licenses.\n");
console.log("Paste this into desktop/electron/license/publicKey.cjs as PUBLIC_KEY_B64:\n");
console.log(publicKeyB64);
