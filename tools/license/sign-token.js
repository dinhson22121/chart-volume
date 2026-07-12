#!/usr/bin/env node
/**
 * Signs a new offline license token with a TTL, using the private key from
 * tools/license/keygen.js. Run on the licensor's machine; paste the printed
 * token into the app's activation screen.
 *
 * Usage:
 *   node tools/license/sign-token.js <private-key-pem-path> --days 365 [--note "customer name"]
 */

const fs = require("fs");
const crypto = require("crypto");

function parseArgs(argv) {
  const [keyPath, ...rest] = argv;
  let days = null;
  let note;
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === "--days") {
      days = Number(rest[i + 1]);
      i++;
    } else if (rest[i] === "--note") {
      note = rest[i + 1];
      i++;
    }
  }
  return { keyPath, days, note };
}

const { keyPath, days, note } = parseArgs(process.argv.slice(2));

if (!keyPath || !Number.isFinite(days)) {
  console.error(
    'Usage: node tools/license/sign-token.js <private-key-pem-path> --days <N> [--note "..."]',
  );
  process.exit(1);
}

const privateKeyPem = fs.readFileSync(keyPath, "utf8");
const privateKey = crypto.createPrivateKey(privateKeyPem);

const nowSec = Math.floor(Date.now() / 1000);
const payload = {
  iat: nowSec,
  exp: nowSec + Math.round(days * 86400),
  ...(note ? { note } : {}),
};

const payloadBytes = Buffer.from(JSON.stringify(payload), "utf8");
const signature = crypto.sign(null, payloadBytes, privateKey);

const token = `${payloadBytes.toString("base64url")}.${signature.toString("base64url")}`;

console.log("License token (paste into the app's activation screen):\n");
console.log(token);
console.log("\nPayload:", payload);
console.log("Expires at:", new Date(payload.exp * 1000).toISOString());
