# Legacy uBlock Origin Lite package

This source package is retained only as an unbundled upstream reference.
Zyvro 0.4 and later use the native Rust engine under `rust/zyvro-adblock` and
do not package or load this extension.

The archived files came from the official uBlock Origin Lite Chromium release
`2026.926.2202` from:

https://github.com/uBlockOrigin/uBOL-home/releases/tag/2026.926.2202

Official archive SHA-256:

`9A0D94E832FDE9430F64817FF1BA3F34040F19CAA113A24E6D84AAD1D05EB1AA`

The upstream files are distributed under GPL-3.0-or-later; see `LICENSE.txt`.

Qt WebEngine 6.11 loads the Manifest V3 package but does not activate its
static `declarativeNetRequest` rules. Zyvro therefore evaluates the package's
six upstream default network rulesets in its native request interceptor. The
adapter supports block and allow rules, including URL, domain, initiator,
resource-type, request-method, and first/third-party conditions. Redirect and
response-header modification rules are left to upstream-compatible browsers
and are not represented as successful blocks in Zyvro.

Zyvro's own per-site switch, local counters, and cosmetic cleanup remain the
user interface around these rules. No browsing data is sent to uBlock Origin,
Google, or a Zyvro service.
