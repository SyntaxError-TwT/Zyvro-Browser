# Zyvro ad-block data

Snapshot refreshed: 2026-09-27

The native matcher is Brave's `adblock-rust` 0.13.3, pinned by
`rust/zyvro-adblock/Cargo.lock`. Filter data is kept separate so it can be
updated without replacing the Rust engine.

Bundled filter sources:

- uBlock filters, privacy, quick fixes, unbreak, and uBO Lite filters:
  https://github.com/uBlockOrigin/uAssets
- EasyList and EasyPrivacy mirrors maintained by uAssets:
  https://ublockorigin.github.io/uAssets/
- Brave adblock resources:
  https://github.com/brave/adblock-resources

`scriptlets/youtube-main.js` and `scriptlets/youtube-isolated.js` are the
generated `ublock-filters` document-start scripts from the official uBlock
Origin Lite 2026.926.2202 package. They are loaded only for YouTube-family
hosts to cover trusted YouTube rules whose resource names are not present in
Brave's resource bundle. Their GPL-3.0 license is in `UBLOCK-LICENSE.txt`.

`manifest.json` records each bundled file's SHA-256 and size.

