# Security

## Security model

This project is a local-only Chrome extension plus a Python service. The service:

- Binds to `127.0.0.1` only.
- Requires a random bearer token for every database operation.
- Accepts browser requests only from a Chrome extension origin.
- Uses fixed API routes; the extension cannot proxy arbitrary URLs.
- Generates collection timestamps on the server.
- Stores the SQLite database outside the source tree by default.

The token is generated when the desktop application starts and written to the ignored local `config.js`. Never commit that file.

## Sensitive data

Follower/following lists, change history, SQLite files, and Excel exports are sensitive personal data. They are not encrypted by this application. Protect the Windows account, desktop data folder, database backups, and exported workbooks.

## Reporting a vulnerability

Do not publish database files, tokens, screenshots containing usernames, or exported workbooks in an issue. Report security problems privately to the repository maintainer with:

1. Affected version or commit.
2. Reproduction steps that do not expose real account data.
3. Impact and suggested mitigation.

## Safe release checklist

- Confirm `config.js`, `*.db`, `*.db-*`, and `*.xlsx` are absent from the release.
- Start the service from the desktop application's Collection service tab, reload the unpacked extension, and verify `/api/health`.
- Verify unauthenticated data requests return `401`.
- Verify requests with a non-extension `Origin` return `403`.
- Verify profiles remain isolated.
- Stop the local service from the desktop application's Collection service tab when the extension is not in use.
