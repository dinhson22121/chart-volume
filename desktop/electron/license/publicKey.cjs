// Public key used to verify offline license tokens (see tools/license/).
// Safe to commit -- this is the PUBLIC half of the Ed25519 key pair; only the
// private key (kept outside this repo) can mint valid tokens.
//
// Generate with: node tools/license/keygen.js <private-key-output-path>
// then paste the printed base64 value below.
module.exports = {
  PUBLIC_KEY_B64: "MCowBQYDK2VwAyEAJmuvTGwvDnYzHipj9VnxxBGzHwp83e11bK3uUq2hWGM=",
};
