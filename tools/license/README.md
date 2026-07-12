# Offline license tooling

Signs/generates license tokens for Chart-Volume's activation gate
(`desktop/electron/license/`). Runs entirely offline, no server involved.

## One-time setup (once, on the licensor's machine only)

```bash
node tools/license/keygen.js /path/outside/repo/license-private.pem
```

Prints a base64 public key -- paste it into
`desktop/electron/license/publicKey.cjs` as `PUBLIC_KEY_B64`. Keep the
private key file safe, outside this repo, and never share it: anyone who has
it can mint a valid license for any copy of the app.

## Issuing a license

```bash
node tools/license/sign-token.js /path/outside/repo/license-private.pem --days 365 --note "customer name"
```

Prints a token string. Send that token to the user; they paste it into the
app's activation screen. `--note` is optional, for your own bookkeeping only
(not enforced or displayed anywhere).

## Notes

- Tokens are not bound to a specific machine -- a valid, non-expired token
  activates on any install.
- For local dev testing without minting a real token every time, copy
  `desktop/electron/license/masterKey.local.cjs.example` to
  `masterKey.local.cjs` and set your own passphrase. That file is gitignored
  and excluded from packaged builds (`build.files` in `package.json`) --
  it never gets committed and never ships, unlike a hardcoded value would.
