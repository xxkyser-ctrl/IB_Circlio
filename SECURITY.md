# Security

## Local service

- The service binds to `127.0.0.1`; it is not intended to be reachable from other machines.
- It validates the request `Host` against the local service address and port, and accepts only an exact Chromium extension origin or a Firefox `moz-extension://` UUID origin.
- Protected routes, including read-only routes and `/version`, require the random bearer token. The health check exposes only service availability. Token comparisons use `hmac.compare_digest`.
- CORS responses echo only a validated extension origin; wildcard origins are not used.
- Collection JSON is size-limited and validated, usernames follow Instagram's username character and length constraints, and database queries use parameterized SQL.
- Avatar downloads accept HTTPS URLs only from Instagram/Facebook CDN hostnames, reject non-public DNS results, revalidate redirects, impose a timeout and size limit, and verify image content before saving under a generated hash filename.
- Workbook strings are written as text and formula-like values are prefixed to prevent spreadsheet formula execution.

## Local files and encryption

The database, bearer token, archived profile pictures, settings, and exported workbooks are local files. They are **not encrypted** by the application; an encrypted-database option is planned but is not currently implemented. Protect the Windows account and any backups or exported workbooks. On POSIX systems, token and reset-password files are restricted to the current user. On Windows, keep the files in the user-local application data directory and protect the account that owns it.

The reset utility uses PBKDF2-HMAC-SHA256 with a per-file random salt and a high iteration count. Its password protects the reset operation; it does not encrypt the database.

`config.js`, `server-token.txt`, database files, and user exports must not be included in source control or public release packages. The build rejects local config and token files from the portable Windows ZIP. Optional update checks contact the public GitHub Releases API only; no usernames, collection data, token, or other personal data are sent.

## Third-party software and dependencies

The bundled SheetJS library and its license are identified in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Python build dependencies are pinned in `requirements-build.txt`; run `py -3 -m pip_audit -r requirements-build.txt` to check them against known Python advisories.

## Reporting a vulnerability

Do not publish database files, tokens, screenshots containing usernames, or exported workbooks in an issue. Report security problems privately to the repository maintainer with:

1. Affected version or commit.
2. Reproduction steps that do not expose real account data.
3. Impact and suggested mitigation.

## Safe release checklist

- Run `py -3 -B -m unittest -q` and `py -3 -m pip_audit -r requirements-build.txt`.
- Confirm `config.js`, `server-token.txt`, `*.db`, `*.db-*`, and `*.xlsx` are absent from all release assets.
- Confirm Chromium and Firefox extension packages contain their correct manifests and no local token.
- Verify the SHA-256 sums match each release asset.
- Verify unauthenticated and wrong-token protected requests return `401`; invalid `Host` or `Origin` requests return `403`.
- Verify invalid collection bodies return `400`, the local version endpoint returns the canonical version, and avatar redirects cannot reach non-public hosts.
- Stop the local service using the **Stop service** button or close the desktop app when it is not in use.
