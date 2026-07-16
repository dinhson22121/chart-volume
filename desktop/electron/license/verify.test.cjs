const { test } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("crypto");
const { verifyToken, verifySignedToken, matchesMasterKey } = require("./verify.cjs");

// Uses a throwaway test key pair (never the real embedded one) so these
// tests never depend on the actual private key, which deliberately never
// lives in this repo.
const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");

// Payload is just the 4-byte big-endian expiry -- no JSON, no iat/note (see
// verify.cjs's verifySignedToken for why).
function makeToken(exp, { key = privateKey, prefix = "CV-" } = {}) {
  const payloadBytes = Buffer.alloc(4);
  payloadBytes.writeUInt32BE(exp);
  const signature = crypto.sign(null, payloadBytes, key);
  return `${prefix}${payloadBytes.toString("base64url")}.${signature.toString("base64url")}`;
}

test("valid signature + future exp -> valid", () => {
  const token = makeToken(Math.floor(Date.now() / 1000) + 3600);
  const result = verifySignedToken(token, publicKey);
  assert.equal(result.valid, true);
  assert.equal(result.payload.exp > Date.now() / 1000, true);
});

test("valid signature without the CV- prefix -> still valid", () => {
  // A user pasting just the base64url part (stripped the cosmetic prefix by
  // hand) must still work.
  const token = makeToken(Math.floor(Date.now() / 1000) + 3600, { prefix: "" });
  const result = verifySignedToken(token, publicKey);
  assert.equal(result.valid, true);
});

test("valid signature + past exp -> expired", () => {
  const token = makeToken(Math.floor(Date.now() / 1000) - 10);
  const result = verifySignedToken(token, publicKey);
  assert.equal(result.valid, false);
  assert.equal(result.reason, "expired");
});

test("wrong key's signature -> bad_signature", () => {
  const other = crypto.generateKeyPairSync("ed25519");
  const token = makeToken(Math.floor(Date.now() / 1000) + 3600, { key: other.privateKey });
  const result = verifySignedToken(token, publicKey);
  assert.equal(result.valid, false);
  assert.equal(result.reason, "bad_signature");
});

test("tampered payload after signing -> bad_signature", () => {
  const token = makeToken(Math.floor(Date.now() / 1000) + 3600);
  const [, sig] = token.split(".");
  const tamperedPayload = Buffer.alloc(4);
  tamperedPayload.writeUInt32BE(9999999999 % 0xffffffff);
  const result = verifySignedToken(`CV-${tamperedPayload.toString("base64url")}.${sig}`, publicKey);
  assert.equal(result.valid, false);
  assert.equal(result.reason, "bad_signature");
});

test("payload of the wrong byte length -> bad_format", () => {
  // e.g. someone pastes an old-format (JSON-payload) token against the new
  // verifier -- must reject cleanly, not throw or misparse the expiry.
  const oldStyleJsonPayload = Buffer.from(JSON.stringify({ exp: 9999999999 }), "utf8");
  const signature = crypto.sign(null, oldStyleJsonPayload, privateKey);
  const token = `CV-${oldStyleJsonPayload.toString("base64url")}.${signature.toString("base64url")}`;
  const result = verifySignedToken(token, publicKey);
  assert.equal(result.valid, false);
  assert.equal(result.reason, "bad_format");
});

test("malformed token (no dot separator) -> bad_format", () => {
  const result = verifySignedToken("not-a-real-token", publicKey);
  assert.equal(result.valid, false);
  assert.equal(result.reason, "bad_format");
});

test("verifyToken: empty/whitespace input -> empty", () => {
  assert.equal(verifyToken("").reason, "empty");
  assert.equal(verifyToken("   ").reason, "empty");
  assert.equal(verifyToken(undefined).reason, "empty");
});

test("matchesMasterKey: matching key -> valid with no TTL", () => {
  const result = matchesMasterKey("some-passphrase", "some-passphrase");
  assert.equal(result.valid, true);
  assert.equal(result.payload.exp, null);
  assert.equal(result.payload.master, true);
});

test("matchesMasterKey: non-matching key -> null (falls through to signature check)", () => {
  assert.equal(matchesMasterKey("wrong", "some-passphrase"), null);
});

test("matchesMasterKey: no master key configured -> always null", () => {
  assert.equal(matchesMasterKey("anything", null), null);
});

test("verifyToken: no local masterKey.local.cjs in this checkout -> bypass disabled", () => {
  // This repo never commits masterKey.local.cjs (see .gitignore), so in any
  // fresh checkout the bypass must be off and only real signed tokens work.
  const result = verifyToken("whatever-someone-might-guess");
  assert.equal(result.valid, false);
  assert.notEqual(result.reason, undefined);
});
