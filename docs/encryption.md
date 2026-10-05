# Data encryption

IB Circlio 1.0.8 uses SQLCipher 4 for encrypted SQLite files, the
`sqlcipher3-wheels` 0.5.7 Windows CPython 3.13 wheel, and `cryptography`
50.0.2 for authenticated avatar and key-envelope encryption. The SQLCipher
wheel is self-contained and includes its native Python extension. The build
must collect that extension and its dependent libraries and must be tested as
a frozen Windows application before release.

## Key storage and recovery

New Windows installations generate a random 256-bit master key. Windows DPAPI
protects the key for the current Windows user; only the protected blob is
stored in the data folder. The app also generates a one-time recovery code.
The recovery code encrypts a copy of the master key using scrypt and
AES-256-GCM. It is shown once and can be saved to a location chosen by the
user. The recovery code is not stored in the data folder or application logs.

Losing access to the Windows account or DPAPI-protected key requires the
recovery code or an intact backup. Without either, encrypted data cannot be
recovered. Keep recovery codes and backups separate from the computer.
Passphrase mode, where offered, uses a random salt and scrypt to wrap the same
random master key; a wrong passphrase must not create or replace a database.
Database, avatar, and token keys are independently derived from the master
key using HKDF with distinct context strings.

Encryption protects data at rest while the protected key is unavailable. It
does not protect an unlocked session from malware or another process running
as the same Windows user.

The local service token is encrypted in the data folder. The browser extension
must still receive its token through a generated `config.js` file so it can
authenticate to the local service. That file is plaintext by design, is
excluded from source control and release archives, and is restricted to the
current user where the operating system permits. Exported Excel files are
readable by default. Treat unprotected workbooks as real account data.

## Encrypted files

SQLCipher encrypts the SQLite database, including its schema and records.
Avatar files use a versioned `IBC1` header followed by a unique random
96-bit nonce and AES-256-GCM ciphertext. The UI decrypts avatar bytes in
memory and does not create plaintext avatar temporary files. The token is
stored as an authenticated encrypted envelope.

## Existing installations and migration

An existing plaintext installation is not converted in the background.
The desktop app offers encryption and allows the user to defer or decline.
Migration must run with the local service stopped. Before conversion, it
checks free space and creates a timestamped, complete sibling backup. It
converts into staging files, verifies database integrity and content hashes,
and verifies every avatar by decrypting it and comparing its hash. Only
verified data is switched into use. Plaintext backups are retained; the user
may remove them after verifying the encrypted installation. Deleting files,
particularly on SSDs, cannot guarantee forensic erasure.

Disabling encryption follows the same backup, staging, and verification
process in reverse. Interrupted migration state must be detected before the
database is opened and rolled back from the retained backup rather than
creating a new empty database.

New Windows data folders use DPAPI protection. To change the key-protection
mode, disable encryption from the desktop app and then enable it again,
choosing passphrase protection when prompted. Each conversion creates its own
verified sibling backup.

## Developer and recovery operations

On Windows, normal startup obtains the key through DPAPI. In passphrase mode,
the desktop app passes the unlocked key to its child service through that
process's environment; it is not written to a command line or file. For
isolated tests and service development, `IB_CIRCLIO_KEY` may contain exactly
64 hexadecimal characters (a 256-bit key); keep this environment variable
private and never put it in a command line, repository file, or log. Existing
`INSTAGRAM_DB`, `INSTAGRAM_PORT`, `INSTAGRAM_EXPORTER_TOKEN`, `--token-file`,
and `--data-dir` overrides remain supported. The reset utility continues to
require its existing password and exact `CLEAR` confirmation. It clears the database records and archived pictures while
retaining the database file, encryption metadata, key material, and service
token so the configured installation remains usable.

## Export password support

Unprotected `.xlsx` remains the default. If password-protected workbook output
is not supported and tested by the packaged Windows build, the UI must not
offer a password option that cannot reliably produce a standard Office
encrypted workbook.
