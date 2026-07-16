// Offline license token verification -- no network calls, no server.
//
// Two ways a token can be valid:
//  1. A real Ed25519-signed token from tools/license/sign-token.js, checked
//     against publicKey.cjs and its own `exp` (TTL).
//  2. A local MASTER_KEY passphrase -- a permanent (no TTL), no-signature
//     bypass for the developer's own local testing.
//
// MASTER_KEY is intentionally NOT hardcoded here and NOT committed to git:
// this repo is public, and a bypass string baked into source would be
// public forever the moment it's pushed. Instead it's read from
// masterKey.local.cjs, a gitignored sibling file (see
// masterKey.local.cjs.example for the format) that only needs to exist on
// the developer's own machine for `npm run dev`. It is also excluded from
// electron-builder's packaged output (see package.json's `build.files`
// negation entry), so it never ships even if present on the machine that
// runs a production build. No local file -> MASTER_KEY is null -> the
// bypass is simply disabled, which is the default for anyone else who
// clones this repo.
const crypto = require("crypto");
const { PUBLIC_KEY_B64 } = require("./publicKey.cjs");

// Cosmetic only (an unbranded base64url blob doesn't read as "this is a real
// license key") -- stripped before parsing, so a token pasted with or
// without it verifies the same way.
const TOKEN_PREFIX = "CV-";

let MASTER_KEY = null;
try {
  ({ MASTER_KEY } = require("./masterKey.local.cjs"));
} catch {
  // masterKey.local.cjs doesn't exist -- bypass disabled, real tokens only.
}

let cachedPublicKey = null;
function getPublicKey() {
  if (!cachedPublicKey) {
    cachedPublicKey = crypto.createPublicKey({
      key: Buffer.from(PUBLIC_KEY_B64, "base64"),
      format: "der",
      type: "spki",
    });
  }
  return cachedPublicKey;
}

/**
 * Signature+TTL check against an explicit public key -- factored out of
 * verifyToken() so tests can exercise the real crypto logic with a
 * throwaway test key pair, without needing the actual embedded private key
 * (which deliberately never lives in this repo).
 * @param {string} trimmed
 * @param {import("crypto").KeyObject} publicKey
 */
function verifySignedToken(trimmed, publicKey) {
  const withoutPrefix = trimmed.startsWith(TOKEN_PREFIX) ? trimmed.slice(TOKEN_PREFIX.length) : trimmed;
  const parts = withoutPrefix.split(".");
  if (parts.length !== 2) {
    return { valid: false, reason: "bad_format" };
  }
  const [payloadPart, sigPart] = parts;

  let payloadBytes;
  let signature;
  try {
    payloadBytes = Buffer.from(payloadPart, "base64url");
    signature = Buffer.from(sigPart, "base64url");
  } catch {
    return { valid: false, reason: "bad_format" };
  }
  // The payload is just the 4-byte big-endian expiry (Unix seconds, valid
  // until year 2106) -- no JSON, no iat/note. Those fields were never read
  // by anything except a human generating the token, and JSON framing alone
  // cost ~30-40 extra characters in a string a person has to paste by hand.
  if (payloadBytes.length !== 4) {
    return { valid: false, reason: "bad_format" };
  }

  let verified;
  try {
    verified = crypto.verify(null, payloadBytes, publicKey, signature);
  } catch {
    return { valid: false, reason: "bad_format" };
  }
  if (!verified) {
    return { valid: false, reason: "bad_signature" };
  }

  const exp = payloadBytes.readUInt32BE(0);
  const payload = { exp };

  const nowSec = Math.floor(Date.now() / 1000);
  if (exp <= nowSec) {
    return { valid: false, reason: "expired", payload };
  }

  return { valid: true, payload };
}

/**
 * Factored out (like verifySignedToken) so tests can exercise the
 * master-key branch with an explicit fake key, without depending on
 * masterKey.local.cjs actually existing on disk.
 * @param {string} trimmed
 * @param {string | null} masterKey
 */
function matchesMasterKey(trimmed, masterKey) {
  if (!masterKey) return null;
  if (trimmed !== masterKey) return null;
  return { valid: true, payload: { iat: Math.floor(Date.now() / 1000), exp: null, master: true } };
}

/**
 * @param {string} token
 * @returns {{valid: boolean, payload?: {exp: number|null, iat?: number, master?: boolean}, reason?: "empty"|"bad_format"|"bad_signature"|"expired"}}
 */
function verifyToken(token) {
  if (!token || typeof token !== "string") {
    return { valid: false, reason: "empty" };
  }
  const trimmed = token.trim();
  if (!trimmed) {
    return { valid: false, reason: "empty" };
  }

  const masterMatch = matchesMasterKey(trimmed, MASTER_KEY);
  if (masterMatch) return masterMatch;

  return verifySignedToken(trimmed, getPublicKey());
}

module.exports = { verifyToken, verifySignedToken, matchesMasterKey };
